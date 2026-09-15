# Sumradio

Sumradioは、災害本部で受信した無線交信をPCのマイクから取り込み、ローカルで文字起こししたうえで、人間が確認する対応事項として整理するアプリです。

## 処理の流れ

1. 選択したマイクから16bit PCM音声を取得します。
2. マイクの元サンプルレートと、16kHz・モノラルの2種類のWAVを保存します。
3. 5秒の無音または人間の操作で交信を区切り、`faster-whisper`の`medium`モデルで文字起こします。
4. 確定文字はCodexの応答を待たずに保存・表示します。
5. `codex exec`がテキストをJSONに整理し、検証済みの候補を「タスク候補」へ追加します。
6. 人間の明示操作でのみ「タスク候補 → 未対応 → 対応済み」と進めます。

## 対応環境

- macOS（Apple Siliconで検証）
- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- PortAudio
- Codex CLIのログイン済み環境
- 初回のWhisper medium取得とAI整理のためのインターネット接続

## 初回セットアップ

```sh
brew install portaudio
uv python install 3.13
uv sync --all-groups
codex login status
```

Whisperモデルは初回起動時にダウンロードされます。デモ当日の取得を避ける場合は、事前に次を実行します。

```sh
uv run python -c 'from faster_whisper import WhisperModel; WhisperModel("medium", device="cpu", compute_type="int8", download_root="data/models")'
```

## 起動

```sh
uv run sumradio
```

ブラウザで [http://127.0.0.1:8000](http://127.0.0.1:8000) を開きます。初回録音時にmacOSがマイクの利用を尋ねたら許可し、アプリを再起動してください。マイクが出ない場合は、「システム設定 → プライバシーとセキュリティ → マイク」で起動に使ったターミナルの許可を確認します。

## 操作

- マイクを選択して「録音開始」を押します。発話終了後5秒の無音で自動確定します。
- 騒音が多い場合は「今の交信を確定」で区切ります。そのまま次の交信を受信できます。
- 交信履歴から原音、Whisper原文、AI整理結果を確認します。訂正した場合は新しい文字版で再整理されます。
- タスク編集では、人数を「説明｜人数」、物資を「品目｜数量｜単位｜詳細」の形式で1行ずつ入力できます。
- AI整理が失敗しても音声と文字は残り、手動登録も利用できます。
- 「承認」「破棄」「対応済みにする」は確認ダイアログの後に状態を変更します。

## 保存先とプライバシー

`data/communications/<交信ID>/`に元音、認識用音声、Markdown、メタデータ、抽出JSONを保存し、`data/tasks/`にタスクと操作履歴を保存します。音声はCodexへ送信しません。Codexに渡すのは文字記録、通話表、必要な関連タスクだけです。

保存先を変える場合は`SUMRADIO_DATA_DIR`を指定します。データを消すUIはありません。

## 設定

| 環境変数 | 既定値 | 用途 |
| --- | --- | --- |
| `SUMRADIO_DATA_DIR` | `./data` | 音声・記録の保存先 |
| `SUMRADIO_MODEL_DIR` | `./data/models` | Whisper mediumの保存先 |
| `SUMRADIO_CODEX_PATH` | PATH上の`codex` | Codex CLI実行ファイル |
| `SUMRADIO_CODEX_MODEL` | `gpt-5.6-luna` | 抽出モデル |
| `SUMRADIO_CODEX_EFFORT` | `low` | 推論強度 |
| `SUMRADIO_CODEX_TIMEOUT_SECONDS` | `60` | 1回のAI整理の制限時間 |
| `SUMRADIO_VOICE_RMS_THRESHOLD` | `300` | 実機の騒音を発話と誤判定しないための音量下限 |
| `SUMRADIO_PORT` | `8000` | ローカル画面のポート |

Sumradio用のAPIキー設定はありません。Codex CLIの保存済み認証を使います。

静かな声を検出しない場合は`SUMRADIO_VOICE_RMS_THRESHOLD`を下げ、周囲の音を発話と誤判定する場合は上げてください。短いノイズだけで交信を作らないよう、発話判定が合計240ms未満の区間は保存しません。

## 検証

```sh
uv run pytest
uv run pytest --cov=sumradio --cov-report=term-missing
uv run python scripts/verify_codex_cases.py --case 01
uv run python scripts/verify_codex_cases.py --case all
uv run python scripts/verify_codex_special_cases.py
```

後半2つの検証は、架空の訓練文字記録を実際のCodexサービスへ送ります。マイク・Whisperモデル・実際のCodex通信を使う確認は自動テストと分け、[検証結果.md](検証結果.md)に記録します。
