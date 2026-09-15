from fastapi.testclient import TestClient

from sumradio.app import create_app


def test_health_state_and_manual_board_flow(settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/health").json()["ok"]
        created = client.post(
            "/api/tasks",
            json={
                "kind": "request",
                "title": "飲料水",
                "action": "飲料水を手配する",
                "people": [{"description": "避難者", "count": 60}],
                "resources": [{"item": "飲料水", "quantity": 15, "unit": "箱"}],
            },
        )
        assert created.status_code == 200
        task = created.json()
        assert task["state"] == "candidate"
        assert task["people"][0]["count"] == 60
        assert task["resources"][0]["quantity"] == 15
        forbidden = client.post(
            f"/api/tasks/{task['id']}/transition",
            json={"version": task["version"], "target_state": "done", "actor": "operator"},
        )
        assert forbidden.status_code == 422
        opened = client.post(
            f"/api/tasks/{task['id']}/transition",
            json={"version": task["version"], "target_state": "open", "actor": "operator"},
        )
        assert opened.status_code == 200
        state = client.get("/api/state").json()
        assert state["tasks"][0]["state"] == "open"
        assert state["runtime"]["whisper"]["status"] == "skipped"


def test_version_conflict_is_409(settings) -> None:
    with TestClient(create_app(settings)) as client:
        task = client.post(
            "/api/tasks",
            json={"kind": "situation_confirmation", "title": "状況確認", "action": "対応要否を判断する"},
        ).json()
        response = client.patch(
            f"/api/tasks/{task['id']}",
            json={"version": 999, "title": "変更", "action": "変更"},
        )
        assert response.status_code == 409
