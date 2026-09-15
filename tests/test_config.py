from pathlib import Path

from sumradio.config import Settings


def test_local_settings_persist_independently_of_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("SUMRADIO_CODEX_PATH", raising=False)
    monkeypatch.delenv("SUMRADIO_PORT", raising=False)
    root = tmp_path / "project"
    root.mkdir()
    (root / ".env").write_text(
        'SUMRADIO_CODEX_PATH="/opt/compatible cli/codex"\nSUMRADIO_PORT=8123\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env(root)

    assert settings.codex_path == "/opt/compatible cli/codex"
    assert settings.port == 8123
    # Reading one project's settings must not leak into another project.
    monkeypatch.setattr("sumradio.config.shutil.which", lambda name: "/bin/codex")
    assert Settings.from_env(tmp_path).codex_path == "/bin/codex"
    assert Settings.from_env(tmp_path).port == 8000


def test_environment_overrides_local_settings(tmp_path: Path, monkeypatch):
    (tmp_path / ".env").write_text("SUMRADIO_CODEX_PATH=/local/codex\n", encoding="utf-8")
    monkeypatch.setenv("SUMRADIO_CODEX_PATH", "/explicit/codex")

    assert Settings.from_env(tmp_path).codex_path == "/explicit/codex"


def test_unset_local_setting_keeps_path_fallback(tmp_path: Path, monkeypatch):
    (tmp_path / ".env").write_text("SUMRADIO_CODEX_PATH\n", encoding="utf-8")
    monkeypatch.delenv("SUMRADIO_CODEX_PATH", raising=False)
    monkeypatch.setattr("sumradio.config.shutil.which", lambda name: "/bin/codex")

    assert Settings.from_env(tmp_path).codex_path == "/bin/codex"
