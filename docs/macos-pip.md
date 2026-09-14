# macOSでuvを使わずに起動する

Python標準の `venv` と `pip` でSumradioを導入します。Apple Silicon / Intel Macを想定した手順です。Mac実機での動作確認は未実施です。uvで失敗した原因が音声認識ライブラリやOSとの互換性にある場合、pipでも同じエラーが出る可能性があります。

## 1. Pythonを準備する

Sumradioの対応範囲はPython **3.11〜3.13** です。ここでは **3.12** を使用します。

Homebrewを導入済みなら、次を実行します。

```sh
brew install python@3.12
python3.12 --version
```

Homebrewを使わない場合は、[Python公式のmacOSダウンロードページ](https://www.python.org/downloads/macos/) から **Python 3.13のmacOS installer** を選んでインストールしてください。その場合、以下の `python3.12` は `python3.13` に読み替えます。Python 3.14以降は現時点のSumradioの対応範囲外です。

公式インストーラーはIntelとApple Silicon向けのuniversal2形式です。Apple Siliconでは、ターミナルをRosettaで起動せず、Pythonと依存ライブラリのアーキテクチャを揃えてください。macOS付属のPythonを変更する必要はありません。

## 2. 仮想環境を作り、まず画面を確認する

ターミナルで、ダウンロードまたはcloneした **Sumradioフォルダ（`pyproject.toml` がある場所）** へ移動します。以後のコマンドはすべてその場所で実行してください。

```sh
python3.12 -m venv .venv-pip
source .venv-pip/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m sumradio
```

`.venv-pip` はこの手順専用の仮想環境です。既存のuv用 `.venv` はそのまま残せます。`sudo pip` は使いません。

ブラウザで **http://127.0.0.1:8000** を開き、「保存済み台本を再生」で画面を確認します。この段階では音声認識用の依存パッケージとモデルを準備していないため、台本デモのみ確認できます。

停止するには、起動したターミナルで **Ctrl+C** を押します。

## 3. 音声認識を準備する

同じターミナル・仮想環境で実行します。初回はパッケージとモデルのダウンロードにインターネット接続と空きディスク容量が必要です。

```sh
python -m pip install -e '.[audio]'
python -m pip check
python -m sumradio setup
python -m sumradio
```

`'.[audio]'` の引用符は省略しないでください。macOS標準のzshでは、引用符がないと `no matches found` になる場合があります。

`setup` は既定で暫定字幕・確定字幕に共通の `medium` を `models/` に準備します。既存の `.env` がある場合は `SUMRADIO_PARTIAL_MODEL` と `SUMRADIO_FINAL_MODEL` を両方 `medium` に変更してください。`Setup complete.` と表示されるまで待ってから起動します。モデル取得後は、同じフォルダ・モデル設定でオフライン起動できます。

Macでは既定のCPU設定を使用してください。既存の `.env` に `SUMRADIO_DEVICE=cuda` があれば `SUMRADIO_DEVICE=cpu` に変更します。設定を変更する場合だけ `.env` を用意し、既存のファイルは上書きしないでください。

画面の「無線入力・録音WAVを使う」からWAVを選ぶか、入力デバイスを選択します。ライブ入力では、macOSの「システム設定 → プライバシーとセキュリティ → マイク」で、起動に使用しているターミナルやVS Codeに入力を許可してください。

## 4. 次回からの起動

Sumradioフォルダへ移動し、次の2行だけ実行します。

```sh
source .venv-pip/bin/activate
python -m sumradio
```

通常はインストールや `setup` を繰り返す必要はありません。依存関係が変更された場合は `python -m pip install -e '.[audio]'`、使用モデルを変更した場合は `python -m sumradio setup` を再実行してください。

## 困ったとき

| 症状 | 確認・対処 |
| --- | --- |
| `python3.12: command not found` | 手順1でインストールしたPythonのバージョンを確認。公式3.13インストーラーを使った場合は `python3.13` に読み替える |
| `externally-managed-environment` / `No module named sumradio` | Sumradioフォルダで `source .venv-pip/bin/activate` を実行し直す。`python -m pip --version` のパスが `.venv-pip` 配下か確認 |
| `No matching distribution found` / `incompatible architecture` | Pythonのバージョンとアーキテクチャを下記コマンドで確認し、失敗したパッケージ名を記録。uvからpipへの変更だけでは解決しない場合がある |
| `PortAudio library not found` | pip版sounddeviceは通常macOS用PortAudioを同梱する。読み込みに失敗する場合は、Homebrew利用環境で `brew install portaudio` 後に再確認 |
| 入力デバイスが見つからない | サーバーを停止し、マイク権限・接続を確認して `python -m sumradio devices` を実行 |
| モデルが見つからない / cached snapshotに関するエラー | 起動と同じフォルダ・仮想環境・設定で、オンライン時に `python -m sumradio setup` を完了させる |
| モデル取得中に証明書エラー | Python公式インストーラーを使った場合は、アプリケーション内の該当Pythonフォルダにある `Install Certificates.command` を実行し再試行 |
| `address already in use` | 既に起動したSumradioをCtrl+Cで停止するか、`python -m sumradio serve --port 8001` で起動し http://127.0.0.1:8001 を開く |

解決しない場合は、**失敗したコマンド、エラー全文、次の出力**を共有してください。アクセストークンや `.env` 全体の共有は不要です。

```sh
sw_vers
uname -m
python --version
python -c "import platform, sys; print(sys.executable); print(platform.machine())"
python -m pip --version
python -m pip check
```

## 参考資料

- [Python公式: macOSでの利用](https://docs.python.org/3.13/using/mac.html)
- [Python公式: venv](https://docs.python.org/3.12/library/venv.html)
- [Homebrew: python@3.12](https://formulae.brew.sh/formula/python@3.12)
- [sounddevice公式: インストールとPortAudio](https://python-sounddevice.readthedocs.io/en/latest/installation.html)
- [CTranslate2公式: 対応プラットフォーム](https://opennmt.net/CTranslate2/installation.html)
