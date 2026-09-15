# 横浜地点データと出典

## 用途

施設名・道路名・街区名を地図の代表位置へ対応させる索引です。実際の災害、施設の開設・閉鎖、避難所の開設状況を示すデータではありません。台本の被害・人数・要請は架空です。施設の入口、橋詰、個別住宅、駐車場の詳細座標は推測しません。

## 公開施設一覧

- `yokohama-places.json`: 横浜市「防災関連データ」のCSVから作成。2026-09-15取得、627件。
- 出典: https://www.city.yokohama.lg.jp/bousai-kyukyu-bohan/bousai-saigai/data/shiryodata/data/data.html
- CSV: https://www.city.yokohama.lg.jp/bousai-kyukyu-bohan/bousai-saigai/data/shiryodata/data/data.files/hinanjo.csv
- 利用条件: CC BY 2.1 JP（横浜市）。名称・住所・緯度経度・カナを利用。
- 文字コード: CP932。元データの列は `Type,Definition,Name,Address,Lat,Lon,Kana,Ward,WardCode`。
- 本町小学校: 35.45061431, 139.6285715。元街小学校: 35.43620779, 139.6489516。
- 公開ページに記載された元データの更新日と本アプリでの取得日は異なります。取得日を施設情報の最新性の保証として扱わないでください。

## 公園・道路などの補足地点

`yokohama-landmarks.json`は2026-09-15にNominatimで照会したOSM地点を索引にしたものです。各地点にOSMオブジェクトURLとライセンスを保存しています。

| 台本 | 位置 | 選択根拠・精度 |
| --- | --- | --- |
| 02 | 野毛山公園 | 公園way/94893959の代表位置 |
| 03 | 紅葉坂 | 道路way/26616872。同名の停留所は採用しない |
| 04 | 万国橋 | 橋way/530755814。同じ橋の旧字体を別名として登録 |
| 05 | 本牧元町 | 街区node/7621526876。個別住宅を指さない |
| 06 | 野毛地区センター | ちぇるる野毛way/94922685の建物代表位置。横浜市施設案内の「野毛町3-160-4 ちぇるる野毛3階」と照合 |
| 07 | 本牧山頂公園 | 公園way/95183768の代表位置 |
| 08 | 横浜市健康福祉総合センター | 建物way/85644095の代表位置 |
| 09 | 伊勢佐木町商店街 | 伊勢佐木町一丁目node/8366483998の街区代表位置。店舗・駐車場の位置ではない |

名称・施設の照合資料:

- 横浜市中区の施設案内: https://www.city.yokohama.lg.jp/naka/kusei/koho/map.files/0067_20250327.pdf
- 紅葉坂: https://www.city.yokohama.lg.jp/nishi/shokai/kanko/spot/tekuteku/tekutekusketch12.html
- 伊勢佐木町地区: https://www.city.yokohama.lg.jp/kurashi/machizukuri-kankyo/toshiseibi/plan-rule/kyogichiku/kubetsu/naka/isezaki-ks.html

OSM由来の座標データ: © OpenStreetMap contributors / ODbL。https://www.openstreetmap.org/copyright
索引内のタスク分類や台本内容は地点データに埋め込んでいません。分類は都度、交信テキストから抽出します。

## 別の地域の地点を追加する

画面で場所を手動確定すると、その地域の同名地点の照合に利用できます。まとまった地点一覧を管理する場合は、`SUMRADIO_DATA_DIR/geography/locations.json`（既定は `data/geography/locations.json`）を次の形式で用意してアプリを再起動します。対象範囲に含まれる地点だけが照合対象です。同名で座標の違う地点は候補としてすべて残ります。

```json
{
  "places": [
    {
      "id": "local-honcho",
      "name": "本町小学校",
      "address": "神奈川県横浜市中区花咲町3丁目86",
      "lat": 35.45061431,
      "lon": 139.6285715,
      "source": "master",
      "source_url": "https://www.city.yokohama.lg.jp/bousai-kyukyu-bohan/bousai-saigai/data/shiryodata/data/data.html",
      "license": "横浜市 / CC BY 2.1 JP",
      "precision": "representative",
      "aliases": ["本町小学校避難所"]
    }
  ]
}
```

緯度経度は必ず出典または現地で確認した値を使ってください。全地点を外部サービスへ一括照会する機能はありません。
