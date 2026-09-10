"""Exercise real HTTP streaming and Last-Event-ID across separate connections."""

import json
import socket
import threading
import time

import httpx
import uvicorn

from sumradio.app import create_app
from sumradio.asr import Result


def read_frame(response):
    frame = {}
    for line in response.iter_lines():
        if not line and "data" in frame:
            return frame
        if ": " in line:
            key, value = line.split(": ", 1)
            frame[key] = value
    raise AssertionError("SSE ended before a complete frame")


def test_real_http_sse_resume(settings, backend):
    app = create_app(settings, backend)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
                snapshot = client.get("/api/snapshot").json()
                svc = app.state.service
                svc.finalize(svc._event("mock"), Result("青葉避難所へ飲料水を手配願う", "fake"), 10)
                with client.stream("GET", f"/api/stream?after={snapshot['cursor']}") as response:
                    assert response.headers["content-type"].startswith("text/event-stream")
                    first = read_frame(response)
                with client.stream(
                    "GET", "/api/stream", headers={"Last-Event-ID": first["id"]}
                ) as response:
                    second = read_frame(response)
                assert (first["event"], second["event"]) == ("event", "task")
                assert int(second["id"]) > int(first["id"])
                assert json.loads(second["data"])["source_event_ids"] == [
                    json.loads(first["data"])["id"]
                ]
        finally:
            server.should_exit = True
            thread.join(5)
        assert not thread.is_alive()
