import argparse

from .settings import Settings


def main():
    parser = argparse.ArgumentParser(description="Sumradio — ローカル無線文字起こし")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="localhostで起動（既定）")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser("devices", help="入力デバイス一覧")
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
    elif args.command == "download-models":
        from faster_whisper.utils import download_model

        for model in args.models or [settings.partial_model, settings.final_model]:
            path = download_model(model, cache_dir=str(settings.model_dir))
            print(f"{model}: {path}")
    elif args.command == "evaluate":
        from .evaluation import evaluate_manifest

        evaluate_manifest(args, settings)
    else:
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(settings), host="127.0.0.1", port=getattr(args, "port", 8000))


if __name__ == "__main__":
    main()
