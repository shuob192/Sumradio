import logging
import sys
import warnings
from threading import Event
from types import SimpleNamespace

import pytest

from sumradio.__main__ import download_messages, main, progress


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in ("PARTIAL_MODEL", "FINAL_MODEL", "MODEL_DIR"):
        monkeypatch.delenv(f"SUMRADIO_{key}", raising=False)
    calls = []

    def download_model(model, **kwargs):
        calls.append((model, kwargs))
        return f"models/{model}"

    monkeypatch.setitem(
        sys.modules, "faster_whisper.utils", SimpleNamespace(download_model=download_model)
    )
    return calls


@pytest.mark.parametrize("command", ["setup", "download-models"])
def test_default_models(monkeypatch, downloads, capsys, command):
    monkeypatch.setattr(sys, "argv", ["sumradio", command])
    main()
    assert downloads == [
        ("small", {"cache_dir": "models"}),
        ("kotoba-tech/kotoba-whisper-v2.0-faster", {"cache_dir": "models"}),
    ]
    if command == "setup":
        output = capsys.readouterr().out
        assert "[1/3] Loading faster-whisper" in output
        assert "[2/3] Preparing small" in output
        assert "[3/3] Preparing kotoba-tech/kotoba-whisper-v2.0-faster" in output
        assert "Setup complete." in output


def test_setup_uses_env_settings_and_deduplicates(monkeypatch, downloads, tmp_path):
    (tmp_path / ".env").write_text(
        "SUMRADIO_PARTIAL_MODEL=medium\n"
        "SUMRADIO_FINAL_MODEL=medium\n"
        "SUMRADIO_MODEL_DIR=custom-models\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["sumradio", "setup"])
    main()
    assert downloads == [("medium", {"cache_dir": "custom-models"})]


def test_explicit_model_download(monkeypatch, downloads):
    monkeypatch.setattr(sys, "argv", ["sumradio", "download-models", "large-v3"])
    main()
    assert downloads == [("large-v3", {"cache_dir": "models"})]


def test_setup_failure_does_not_report_success(monkeypatch, downloads, capsys):
    def fail(model, **kwargs):
        raise OSError("connection failed")

    monkeypatch.setitem(sys.modules, "faster_whisper.utils", SimpleNamespace(download_model=fail))
    monkeypatch.setattr(sys, "argv", ["sumradio", "setup"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert "Setup complete." not in output.out
    assert "connection failed" in output.err


def test_setup_missing_audio_dependency(monkeypatch, downloads, capsys):
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", None)
    monkeypatch.setattr(sys, "argv", ["sumradio", "setup"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "uv run --extra audio sumradio setup" in capsys.readouterr().err


@pytest.mark.parametrize("fail", [False, True])
def test_progress_reports_wait_and_stops_on_exit(monkeypatch, fail):
    messages = []
    reported = Event()

    def record(message, **kwargs):
        messages.append(message)
        if "Still working" in message:
            reported.set()

    monkeypatch.setattr("builtins.print", record)
    try:
        with progress("Preparing model...", interval=0.01):
            assert reported.wait(2), "Slow operations must emit a status message"
            if fail:
                raise OSError("download failed")
    except OSError:
        assert fail
    assert messages[0] == "Preparing model..."
    reported.clear()
    assert not reported.wait(0.05), "Reporter must stop when the operation exits"


def test_hub_notices_are_short_deduplicated_and_other_warnings_survive(capsys, caplog):
    logger = logging.getLogger("huggingface_hub.utils._http")
    original_filters = list(logger.filters)
    original_showwarning = warnings.showwarning
    with download_messages():
        for _ in range(2):
            logger.warning("Warning: You are sending unauthenticated requests to the HF Hub.")
            warnings.warn("`huggingface_hub` cache-system uses symlinks by default", stacklevel=1)
        with pytest.warns(UserWarning, match="Unexpected warning"):
            warnings.warn("Unexpected warning", stacklevel=1)
        logger.error("Connection failed")
    assert logger.filters == original_filters
    assert warnings.showwarning is original_showwarning
    output = capsys.readouterr()
    assert output.out.count("Downloading without a token") == 1
    assert output.out.count("Symlinks unavailable") == 1
    assert "UserWarning" not in output.err
    assert "Connection failed" in caplog.text
