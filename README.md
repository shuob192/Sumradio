# Sumradio demo2 — 対応マップ

Sumradioは、災害本部で受信した無線交信をPCのマイクから取り込み、ローカルで文字起こししたうえで、人間が確認する対応事項として整理するアプリです。

## 処理の流れ

1. 選択したマイクから16bit PCM音声を取得します。
2. マイクの元サンプルレートと、16kHz・モノラルの2種類のWAVを保存します。
3. 5秒の無音または人間の操作で交信を区切り、`faster-whisper`の`medium`モデルで文字起こします。
4. 確定文字はCodexの応答を待たずに保存・表示します。
5. `codex exec`がテキストをJSONに整理し、検証済みの候補を「タスク候補」へ追加します。
6. 人間の明示操作でのみ「タスク候補 → 未対応 → 対応済み」と進めます。
7. タスクの場所を地域内の地点一覧と照合し、対応ボード下の地図に記号を表示します。未登録の地名は順番に外部検索します。

この版の要件と訓練台本は `Document_recent/` にあります。横浜版の台本は実在する地名を使用していますが、被害・要請・人数は架空です。

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

- 起動時に「災害対象地域」を指定します。横浜市は登録済みです。他の市区町村を検索するか、地図を移動して「表示中の範囲を使用」で範囲を決められます。録音中は地域を変更できません。
- マイクを選択して「録音開始」を押します。発話終了後5秒の無音で自動確定します。
- 騒音が多い場合は「今の交信を確定」で区切ります。そのまま次の交信を受信できます。
- 交信履歴から原音、Whisper原文、AI整理結果を確認します。訂正した場合は新しい文字版で再整理されます。
- タスク編集では、人数を「説明｜人数」、物資を「品目｜数量｜単位｜詳細」の形式で1行ずつ入力できます。
- AI整理が失敗しても音声と文字は残り、手動登録も利用できます。
- 「承認」「破棄」「対応済みにする」は確認ダイアログの後に状態を変更します。

## 対応マップ

- **自動表示**：場所が1件に絞れたタスクは未確認のピンで表示します。複数候補・該当なし・検索失敗は「地点の確認」に残ります。
- **7種類の記号**：通行障害、飲料水・給水、救助・搬送、物資、避難支援、停電・電源、その他。タスク詳細から複数選択・訂正できます。
- **位置の確認**：「場所確認」で候補・出典を確認して確定します。入口など詳細地点は地図クリックや座標入力で訂正できます。施設データは代表位置です。
- **カードとの連携**：カードの「地図」で該当ピンへ移動し、ピンの「カード・詳細」「元の交信」で根拠を確認できます。同じ座標のタスクは件数付きのピンにまとまります。
- **承認は別操作**：位置を確定してもタスク候補のままです。候補または位置未確認のピンは破線になります。
- **完了・破棄**：地図から外れます。「完了・破棄の履歴」から分類、座標、出典、変更履歴を確認できます。
- **地域切り替え**：地図は選択した地域のタスクだけを表示します。各タスクは作成時の地域を保持します。ボードには他地域のタスクも地域名付きで残ります。

地名の照合順は登録地点 → 人間が確定した地点の地域別キャッシュ → 外部検索キャッシュ → Nominatimです。横浜市の公開CSV627地点と、台本用に出典を確認した8地点を同梱しています。公開CSVは最大1日1回更新し、通信失敗時は同梱・保存済みデータを使います。公開データの掲載日と取得日は異なり、現在の避難所開設を示すものではありません。[地点データの出典と追加方法](src/sumradio/resources/README.md)を参照してください。

公開Nominatimへの検索は全タスク・地域検索を合わせて1プロセス最大毎分4件です。待機中も録音・文字起こし・タスク操作を続けられます。同じ検索はキャッシュを再利用し、成功結果は30日、該当なしは1日保存します。失敗した検索は「場所を再検索」で再試行してください。共有回線で複数台を運用する場合は、管理された検索サービスに切り替えてください。

背景地図はOpenStreetMapのオンラインタイルです。取得不能でも地点一覧・保存済み位置の操作は利用できます。地図の一括取得やオフライン用の先読みは行いません。

## 保存先とプライバシー

`data/communications/<交信ID>/`に元音、認識用音声、Markdown、メタデータ、抽出JSONを保存し、`data/tasks/`にタスクと操作履歴を保存します。音声はCodexへ送信しません。Codexに渡すのは文字記録、通話表、必要な関連タスクだけです。

保存先を変える場合は`SUMRADIO_DATA_DIR`を指定します。データを消すUIはありません。

`data/geography/` に対象地域、地点一覧、検索結果、確定済み地点を保存します。地名検索へ送るのは対象地域・地点名・区市町村名と検索範囲です。タスク本文や音声は送信しません。手動登録の「場所」には地点名のみを入力してください。背景地図の提供者には、表示範囲に対応するタイルのリクエストが送られます。外部検索を止めるには `SUMRADIO_GEOCODER_ENABLED=0`、背景地図を止めるには `SUMRADIO_TILE_URL=''` を指定できます。

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
| `SUMRADIO_GEOCODER_ENABLED` | `1` | 未登録地名の外部検索（`0`で停止） |
| `SUMRADIO_GEOCODER_URL` | `https://nominatim.openstreetmap.org/search` | Nominatim互換検索先 |
| `SUMRADIO_GEOCODER_USER_AGENT` | `Sumradio/0.2 (local disaster-training application)` | 検索サービスへのアプリ識別子 |
| `SUMRADIO_GEOCODER_INTERVAL_SECONDS` | `15` | 検索間隔。公開Nominatimでは15秒未満を指定しても15秒 |
| `SUMRADIO_GEOCODER_TIMEOUT_SECONDS` | `10` | 検索のタイムアウト |
| `SUMRADIO_TILE_URL` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` | 背景タイル。空文字で停止 |
| `SUMRADIO_TILE_ATTRIBUTION` | 空文字 | 独自タイル提供者の追加クレジット |
| `SUMRADIO_REFRESH_PLACES` | `1` | 横浜市CSVの更新（`0`で同梱・保存済みだけを使用） |
| `SUMRADIO_SKIP_MODEL_LOAD` | `0` | `1`で音声認識を省略し、地図・ボードだけを確認 |

Sumradio用のAPIキー設定はありません。Codex CLIの保存済み認証を使います。

静かな声を検出しない場合は`SUMRADIO_VOICE_RMS_THRESHOLD`を下げ、周囲の音を発話と誤判定する場合は上げてください。短いノイズだけで交信を作らないよう、発話判定が合計240ms未満の区間は保存しません。

## 検証

```sh
uv run pytest
uv run pytest --cov=sumradio --cov-report=term-missing
uv run python scripts/verify_codex_cases.py --case 01
uv run python scripts/verify_codex_cases.py --case all
uv run python scripts/verify_codex_special_cases.py
uv run python scripts/verify_audio_map.py /path/to/training.wav
```

`verify_codex_*.py` は架空の訓練文字記録を実際のCodexサービスへ送ります。台本検証は抽出件数に加え、地図分類と同梱地点への照合も確認します。マイク・Whisperモデル・実際のCodex通信を使う確認は自動テストと分け、[検証結果.md](検証結果.md)に記録します。

`verify_audio_map.py` は16-bitモノラルPCMの訓練用WAVを受け取り、実Whisper → 実Codex → 横浜地点の照合まで実行します。音声ファイルは送信せず、認識した文字をCodexへ送ります。検証データは `data/verification/audio-map-<日時>/` に分けて保存します。
