"""Isolated browser-test server. Never seed fixtures into the live application."""

from interpretation_fixture import make_run

from sumradio.audio import WhisperEngine
from sumradio.cli import main
from sumradio.config import ROOT, Settings
from sumradio.storage import Store

if __name__ == "__main__":
    settings = Settings()
    if settings.port != 8766 or not settings.data_dir.resolve().is_relative_to(
        ROOT / ".sumradio-data"
    ):
        raise RuntimeError("画面テスト専用のポート・保存先が必要です")
    store = Store(settings.data_dir)
    try:
        store.add(make_run())
    finally:
        store.close()
    WhisperEngine.load = lambda self: None
    main()
