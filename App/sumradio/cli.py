import argparse
import json
import subprocess

from .config import Settings


def main():
    parser = argparse.ArgumentParser(description="Sumradio")
    parser.add_argument(
        "command",
        choices=["serve", "prepare-model", "doctor", "schema"],
        nargs="?",
        default="serve",
    )
    args = parser.parse_args()
    settings = Settings()
    if args.command == "serve":
        import uvicorn

        from .app import create_app

        uvicorn.run(
            create_app(settings),
            host="127.0.0.1",
            port=settings.port,
            timeout_graceful_shutdown=3,
        )
    elif args.command == "prepare-model":
        from faster_whisper import WhisperModel

        WhisperModel(
            "medium",
            device="cpu",
            compute_type=settings.whisper_compute,
            download_root=str(settings.model_dir or settings.data_dir / "models"),
        )
        print("Whisper medium を準備しました。Sumradioを再起動してください。")
    elif args.command == "schema":
        from .extraction import structured_schema

        print(json.dumps(structured_schema(), ensure_ascii=False, indent=2))
    else:
        import sounddevice as sd

        from .phonetics import load_tables

        print("保存先:", settings.data_dir)
        print("通話表:", {k: len(v) for k, v in load_tables(settings.resources).items()})
        print("入力マイク:", [d["name"] for d in sd.query_devices() if d["max_input_channels"]])
        for extra in [["--version"], ["login", "status"]]:
            try:
                result = subprocess.run(
                    [settings.codex_bin, *extra], capture_output=True, text=True, timeout=10
                )
                print((result.stdout + result.stderr).strip())
            except Exception as exc:
                print("Codex:", exc)
        print("抽出モデル:", settings.codex_model)


if __name__ == "__main__":
    main()
