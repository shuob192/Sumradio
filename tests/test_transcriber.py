from unittest.mock import Mock

import pytest
from huggingface_hub.errors import LocalEntryNotFoundError

from sumradio.transcriber import WhisperTranscriber, WhisperUnavailableError


@pytest.fixture
def model_cache(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (snapshot / name).write_text("test", encoding="utf-8")
    download = Mock(return_value=str(snapshot))
    model = Mock()
    monkeypatch.setattr("faster_whisper.utils.download_model", download)
    monkeypatch.setattr("faster_whisper.WhisperModel", model)
    return snapshot, download, model


def test_complete_cache_loads_directly_without_remote_lookup(tmp_path, model_cache):
    snapshot, download, model = model_cache
    transcriber = WhisperTranscriber(download_root=tmp_path)
    transcriber.load()
    download.assert_called_once_with("medium", cache_dir=str(tmp_path), local_files_only=True)
    assert model.call_args.args == (str(snapshot),)
    assert transcriber.loaded


@pytest.mark.parametrize("missing", ["model.bin", "tokenizer.json", "config.json", "vocabulary.txt"])
def test_partial_cache_still_downloads_missing_files(tmp_path, model_cache, missing):
    snapshot, _, model = model_cache
    (snapshot / missing).unlink()
    WhisperTranscriber(download_root=tmp_path).load()
    assert model.call_args.args == ("medium",)
    assert model.call_args.kwargs["download_root"] == str(tmp_path)


def test_first_start_downloads_model(tmp_path, model_cache):
    _, download, model = model_cache
    download.side_effect = LocalEntryNotFoundError("No cached snapshot")
    WhisperTranscriber(download_root=tmp_path).load()
    assert model.call_args.args == ("medium",)


def test_invalid_cached_model_reports_error_without_redownloading(tmp_path, model_cache):
    _, _, model = model_cache
    model.side_effect = RuntimeError("invalid model")
    transcriber = WhisperTranscriber(download_root=tmp_path)
    with pytest.raises(WhisperUnavailableError, match="invalid model"):
        transcriber.load()
    assert not transcriber.loaded
    assert model.call_count == 1
