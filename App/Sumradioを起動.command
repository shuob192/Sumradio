#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if [[ ! -x .venv/bin/python || ! -f frontend/dist/index.html ]]; then
  print "初回セットアップが必要です。README.md の手順を実行してください。"
  read "?Enterで閉じます。"
  exit 1
fi
print "Sumradio: http://127.0.0.1:8765 をブラウザーで開いてください。"
print "終了は Control + C。録音は画面の開始ボタンを押してから始まります。"
exec .venv/bin/python -m sumradio.cli serve
