import argparse
import logging
import warnings
from contextlib import contextmanager
from threading import Event, Thread
from time import monotonic

from .settings import Settings


@contextmanager
def progress(message, interval=5):
    """Keep slow imports, downloads, and cache-lock waits visible."""
    print(message, flush=True)
    stopped = Event()
    started = monotonic()

    def report():
        while not stopped.wait(interval):
            elapsed = int(monotonic() - started)
            print(
                f"  Still working... {elapsed}s elapsed (network/cache wait included)", flush=True
            )

    reporter = Thread(target=report, daemon=True)
    reporter.start()
    try:
        yield
    finally:
        stopped.set()
        reporter.join()


@contextmanager
def download_messages():
    """Summarize known Hub notices without hiding other warnings or errors."""
    seen = set()

    def notice(key, message):
        if key not in seen:
            seen.add(key)
            print(f"  Note: {message}", flush=True)

    class HubNoticeFilter(logging.Filter):
        def filter(self, record):
            if record.levelno == logging.WARNING and (
                "You are sending unauthenticated requests to the HF Hub" in record.getMessage()
            ):
                notice("auth", "Downloading without a token. Set HF_TOKEN for higher rate limits.")
                return False
            return True

    logger = logging.getLogger("huggingface_hub.utils._http")
    log_filter = HubNoticeFilter()
    logger.addFilter(log_filter)
    try:
        with warnings.catch_warnings():
            original = warnings.showwarning

            def showwarning(message, category, filename, lineno, file=None, line=None):
                if issubclass(category, UserWarning) and str(message).startswith(
                    "`huggingface_hub` cache-system uses symlinks"
                ):
                    notice("symlinks", "Symlinks unavailable; downloads may use extra disk space.")
                else:
                    original(message, category, filename, lineno, file=file, line=line)

            warnings.showwarning = showwarning
            yield
    finally:
        logger.removeFilter(log_filter)


def main():
    parser = argparse.ArgumentParser(description="Sumradio — ローカル無線文字起こし")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="localhostで起動（既定）")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("devices", help="入力デバイス一覧")
    commands.add_parser("setup", help="Initial setup: download transcription models")
    download = commands.add_parser("download-models", help="事前にモデルをダウンロード")
    download.add_argument("models", nargs="*", help="省略時はsmallと主モデル")
    evaluate = commands.add_parser("evaluate", help="正解文付きWAV評価セットを比較")
    evaluate.add_argument("manifest", help="JSON配列: audio, text, slots")
    evaluate.add_argument("--output", default="data/evaluation.json")
    evaluate.add_argument("--models", nargs="+", default=None)
    evaluate.add_argument("--profiles", nargs="+", default=["raw"])
    evaluate.add_argument("--prompt", choices=["on", "off", "both"], default="both")
    args = parser.parse_args()
    settings = Settings()
    if args.command == "devices":
        import json

        from .service import RadioService

        print(json.dumps(RadioService.devices(), ensure_ascii=False, indent=2))
    elif args.command in {"setup", "download-models"}:
        models = list(
            dict.fromkeys(
                getattr(args, "models", None) or [settings.partial_model, settings.final_model]
            )
        )
        total = len(models) + 1
        print(
            "Sumradio | Initial setup" if args.command == "setup" else "Sumradio | Model download"
        )
        print(f"Model directory: {settings.model_dir.resolve()}", flush=True)
        try:
            with progress(f"[1/{total}] Loading faster-whisper..."):
                from faster_whisper.utils import download_model
        except ImportError as exc:
            parser.exit(
                1,
                f"ERROR: Could not load faster-whisper: {exc}\n"
                "Run: uv run --extra audio sumradio setup\n"
                "Or, in an activated venv from the project directory:\n"
                "  python -m pip install -e '.[audio]'\n"
                "  python -m sumradio setup\n"
                "For DLL errors, check your Python environment and OS execution policy.\n",
            )

        print("  Ready.", flush=True)
        print(
            "Checking cached files and downloading missing files. First download may take minutes."
        )
        with download_messages():
            for step, model in enumerate(models, start=2):
                try:
                    with progress(f"[{step}/{total}] Preparing {model}..."):
                        download_model(model, cache_dir=str(settings.model_dir))
                except Exception as exc:
                    parser.exit(
                        1,
                        f"ERROR: Could not prepare {model}: {exc}\n"
                        "Check your connection and free disk space, then run the command again.\n",
                    )
                except KeyboardInterrupt:
                    parser.exit(130, "\nCancelled. Run the command again to resume setup.\n")
                print("  Ready.", flush=True)
        if args.command == "setup":
            print(
                "Setup complete.\nStart the app: uv run --extra audio sumradio\n"
                "Or, in an activated venv: python -m sumradio",
                flush=True,
            )
        else:
            print("Models ready.", flush=True)
    elif args.command == "evaluate":
        from .evaluation import evaluate_manifest

        evaluate_manifest(args, settings)
    else:
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(settings), host="127.0.0.1", port=getattr(args, "port", 8000))


if __name__ == "__main__":
    main()
