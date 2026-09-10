import pytest
from fastapi.testclient import TestClient

from sumradio.app import create_app
from sumradio.asr import Result
from sumradio.settings import Settings


class FakeASR:
    def __init__(self):
        self.result = Result("訓練、青葉避難所へ飲料水15箱を手配願う", "test-asr", -0.4, 0.02)
        self.calls = []
        self.error = None

    def transcribe(self, audio, *, partial=False):
        self.calls.append((len(audio), partial))
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path / "data")


@pytest.fixture
def backend():
    return FakeASR()


@pytest.fixture
def client(settings, backend):
    with TestClient(create_app(settings, backend)) as client:
        yield client
