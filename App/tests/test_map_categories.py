import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from scripts.interpretation_fixture import make_run
from sumradio.app import create_app
from sumradio.audio import WhisperEngine
from sumradio.config import Settings
from sumradio.extraction import (
    INSTRUCTIONS,
    materialize_evidence,
    source_spans,
    structured_schema,
    validate_extraction,
)
from sumradio.models import Category, TaskCreate, TaskEdit
from sumradio.phonetics import load_tables
from sumradio.service import Service
from sumradio.storage import Store


@pytest.mark.parametrize("category", list(Category))
def test_category_schema_acceptance_evidence_and_persistence(tmp_path, category):
    # Tests the contract, not the semantic quality of model classification.
    run = make_run()
    comm = next(iter(run.communications.values()))
    raw = comm.extraction.model_dump(mode="json")
    raw["transcript_interpretations"] = []
    raw["tasks"][0]["map_categories"] = [category.value]
    for field in ("evidence", "category_evidence"):
        raw["tasks"][0][field] = [{"source_id": "s1"}]
    extracted = materialize_evidence(raw, comm, source_spans(comm.text))
    validate_extraction(extracted, comm, run, load_tables(Settings().resources))
    assert extracted.tasks[0].map_categories == [category]
    comm.extraction = extracted
    run.tasks["task_interpretationtest"].map_categories = [category]
    store = Store(tmp_path)
    store.add(run)
    store.close()
    restored = Store(tmp_path)
    try:
        assert restored.get(run.id).tasks["task_interpretationtest"].map_categories == [category]
    finally:
        restored.close()


def test_schema_and_prompt_cover_all_categories_but_reject_unknown():
    assert len(Category) == 15
    assert set(structured_schema()["$defs"]["Category"]["enum"]) == {c.value for c in Category}
    assert all(f"{c.value}=" in INSTRUCTIONS for c in Category)
    with pytest.raises(ValidationError):
        TaskCreate(
            kind="request",
            title="未知分類",
            manual_reason="検証",
            actor="検証",
            map_categories=["urgent"],
        )


def test_multiple_categories_manual_edits_and_old_saved_task_remain_compatible(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(WhisperEngine, "load", lambda self: None)
    service = Service(Settings(data_dir=tmp_path))
    original = make_run()
    service.store.add(original)
    with TestClient(create_app(service.settings, service)) as client:
        assert client.get(f"/api/runs/{original.id}").json()["tasks"]["task_interpretationtest"][
            "map_categories"
        ] == ["water"]
        body = TaskCreate(
            kind="request",
            title="複数記号",
            actor="検証",
            manual_reason="記号の操作検証",
            map_categories=["rescue", "injury", "medical"],
        )
        task = client.post(
            f"/api/runs/{original.id}/tasks", json=body.model_dump(mode="json")
        ).json()
        assert task["map_categories"] == ["rescue", "injury", "medical"]
        client.portal.call(service.location_queue.join)
        task = client.get(f"/api/runs/{original.id}").json()["tasks"][task["id"]]
        edit = TaskEdit(**body.model_dump(), expected_version=task["version"])
        edit.map_categories = [Category.fire, Category.flood]
        response = client.put(
            f"/api/runs/{original.id}/tasks/{task['id']}", json=edit.model_dump(mode="json")
        )
        assert response.status_code == 200
        assert response.json()["map_categories"] == ["fire", "flood"]
        assert response.json()["status"] == "candidate"
        assert client.get(f"/api/runs/{original.id}").json()["tasks"]["task_interpretationtest"][
            "map_categories"
        ] == ["water"]
