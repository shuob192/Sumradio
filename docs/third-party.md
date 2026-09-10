# 外部ライブラリ・モデル

アプリはクラウド推論APIを使いません。モデル取得は利用者が明示する初回準備操作です。依存バージョンは `uv.lock` で固定します。

| コンポーネント | 公式配布元・ライセンス確認元 |
| --- | --- |
| faster-whisper | https://github.com/SYSTRAN/faster-whisper （MIT） |
| kotoba-whisper-v2.0-faster | https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-faster （モデルカード: Apache-2.0） |
| sounddevice | https://python-sounddevice.readthedocs.io/ （MIT） |
| CTranslate2 | https://github.com/OpenNMT/CTranslate2 |
| FastAPI | https://github.com/fastapi/fastapi |
| NumPy / SciPy / SoundFile | 各配布パッケージに同梱されたLICENSEを確認 |

比較モデルは元モデルとCTranslate2変換配布元の両方のモデルカードを確認してください。モデルウェイトはこのリポジトリに含めません。ハッカソン後にバイナリ・モデルをまとめて再配布する場合、ネイティブ依存（PortAudio、libsndfile等）を含むライセンス表示を別途確認してください。
