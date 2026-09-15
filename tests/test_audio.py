from sumradio.audio import AudioSegmenter


RAW = b"\x01\x00" * 1440
REC = b"\x01\x00" * 480
SILENCE_RAW = b"\x00\x00" * 1440
SILENCE_REC = b"\x00\x00" * 480


def feed(segmenter: AudioSegmenter, voiced: bool):
    return segmenter.feed(
        original_frame=RAW if voiced else SILENCE_RAW,
        recognition_frame=REC if voiced else SILENCE_REC,
        original_sample_rate=48000,
        voiced=voiced,
        timestamp="2026-09-14T00:00:00Z",
    )


def test_five_seconds_of_silence_finalizes_with_preroll() -> None:
    segmenter = AudioSegmenter()
    for _ in range(10):
        assert feed(segmenter, False).segment is None
    for _ in range(8):
        assert feed(segmenter, True).segment is None
    result = None
    for _ in range(167):
        result = feed(segmenter, False).segment
    assert result is not None
    assert result.original_sample_rate == 48000
    assert len(result.recognition_pcm) >= (10 + 8 + 167) * len(REC)
    assert not segmenter.active


def test_speech_before_five_seconds_resets_silence_timer() -> None:
    segmenter = AudioSegmenter()
    for _ in range(8):
        feed(segmenter, True)
    for _ in range(160):
        assert feed(segmenter, False).segment is None
    assert feed(segmenter, True).segment is None
    for _ in range(166):
        assert feed(segmenter, False).segment is None
    assert feed(segmenter, False).segment is not None


def test_manual_finalize_and_partial_snapshot() -> None:
    segmenter = AudioSegmenter(partial_ms=60, minimum_voice_ms=60)
    feed(segmenter, True)
    result = feed(segmenter, True)
    assert result.partial_pcm
    assert segmenter.finalize() is not None
    assert segmenter.finalize() is None


def test_short_noise_burst_is_discarded() -> None:
    segmenter = AudioSegmenter(minimum_voice_ms=240)
    feed(segmenter, True)
    for _ in range(167):
        result = feed(segmenter, False)
    assert result.segment is None
    assert not segmenter.active
