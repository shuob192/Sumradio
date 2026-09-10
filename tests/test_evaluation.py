from sumradio.evaluation import distance, metrics


def test_cer_slots_and_silence_metrics():
    assert distance("abc", "axc") == 1
    assert distance("", "abc") == 3
    report = metrics("訓練、十五名", "訓練、五名", ["十五名"])
    assert report["char_errors"] == 1
    assert report["slot_correct"] == 0
    assert metrics("", "幻覚", [])["false_transcription_on_silence"]
