# NATOフォネティックコード表

この表をSumradioが起動時に読み込み、文字起こし後の通話表検知に使用します。
編集後はサーバーを再起動してください。YAMLへの転記は不要です。

「通話表の表記」はNATOの英字コードワード、「追加の検知表記」はSumradioで受け付ける
カタカナ読みやASRの表記揺れです。追加表記は標準の綴りを変更するものではありません。
英字の大文字・小文字は区別しません。追加表記は半角セミコロン `;` で区切ります。
表の3列の見出しと開始・終了マーカーは変更せず、セル内には改行や `|` を入れないでください。

出典: [NATO — The NATO Phonetic Alphabet](https://www.nato.int/en/about-us/nato-history/history-by-theme/symbols-of-nato/nato-phonetic-alphabet)
（確認日: 2026-09-10）。同ページの1956年以降の表に合わせ、AはAlfa、JはJuliett、XはX-rayとします。

<!-- phonetic-table:start -->
| 文字 | 通話表の表記 | 追加の検知表記 |
| --- | --- | --- |
| A | Alfa | アルファ; アルファー; Alpha |
| B | Bravo | ブラボー |
| C | Charlie | チャーリー |
| D | Delta | デルタ |
| E | Echo | エコー |
| F | Foxtrot | フォックストロット |
| G | Golf | ゴルフ |
| H | Hotel | ホテル |
| I | India | インディア |
| J | Juliett | ジュリエット; Juliet |
| K | Kilo | キロ |
| L | Lima | リマ |
| M | Mike | マイク |
| N | November | ノベンバー; ノーベンバー |
| O | Oscar | オスカー |
| P | Papa | パパ |
| Q | Quebec | ケベック |
| R | Romeo | ロミオ; ロメオ |
| S | Sierra | シエラ |
| T | Tango | タンゴ |
| U | Uniform | ユニフォーム |
| V | Victor | ビクター |
| W | Whiskey | ウィスキー; ウイスキー; Whisky |
| X | X-ray | エックスレイ; エックスレー; Xray |
| Y | Yankee | ヤンキー |
| Z | Zulu | ズールー; ズル |
<!-- phonetic-table:end -->

## 検知の条件

`SUMRADIO_RADIO_MODE=true` のとき、文中に「符号」「通話表」「コールサイン」があるか、
通話表の表現が空白・句読点を挟んで2つ以上続く場合に注釈します。
表現の前は文頭・空白・`、。，:：`、後ろは文末・空白・`、。，` が必要です。
例えば `符号：alfa、ブラボー` は `符号：A（alfa）、B（ブラボー）` という候補になります。
`ホテルへ向かって` や `アルファ波` の一部分は変換しません。

原文を保持し、候補と「通話表」ラベルを別に表示します。
このMarkdownを音声認識モデルへ全文投入したり、モデルを追加学習したりする処理ではありません。
