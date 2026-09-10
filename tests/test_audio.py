import numpy as np
import pytest
import soundfile as sf

from sumradio.audio import Segmenter, preprocess, rms, save_pair


def tone(freq, rate=48000, duration=1):
    return (0.2 * np.sin(2 * np.pi * freq * np.arange(round(rate * duration)) / rate)).astype(
        np.float32
    )


@pytest.mark.parametrize("profile", ["raw", "bandpass", "wide-bandpass", "bandpass-denoise"])
def test_wav_contract_and_native_preservation(settings, tmp_path, profile):
    settings.profile = profile
    stereo = np.column_stack([tone(1000), tone(2000)])
    audio = save_pair(tmp_path, "sample", stereo, 48000, settings)
    raw, rate = sf.read(tmp_path / "sample.raw.wav", dtype="float32")
    np.testing.assert_array_equal(raw, stereo)
    assert rate == 48000
    info = sf.info(tmp_path / "sample.wav")
    assert (info.samplerate, info.channels, info.subtype, info.frames) == (
        16000,
        1,
        "PCM_16",
        16000,
    )
    assert max(abs(audio)) <= 10 ** (-3 / 20) + 1e-6


@pytest.mark.parametrize("profile", ["bandpass", "wide-bandpass", "bandpass-denoise"])
def test_filter_attenuates_out_of_band(settings, profile):
    passed = rms(preprocess(tone(1000), 48000, settings, profile)[1600:])
    for frequency in [50, 7000]:
        rejected = rms(preprocess(tone(frequency), 48000, settings, profile)[1600:])
        assert rejected < passed * 0.05


def test_raw_resamples_without_dc_removal_and_limits_peak(settings):
    output = preprocess(np.full((48000, 2), 0.2), 48000, settings)
    assert output[100:-100].mean() == pytest.approx(0.2, abs=0.001)
    assert max(abs(preprocess(tone(1000) * 8, 48000, settings))) <= 10 ** (-3 / 20) + 1e-6


def test_vad_preroll_silence_and_squelch(settings):
    segmenter = Segmenter(16000, settings)
    quiet = np.zeros((1600, 1), dtype=np.float32)
    loud = tone(1000, 16000, 0.1)[:, None]
    for _ in range(10):
        assert segmenter.feed(quiet) is None
    assert segmenter.feed(loud) is None  # 100 ms squelch click is not a speech start
    assert not segmenter.active
    segmenter.feed(quiet)
    for _ in range(5):
        segmenter.feed(loud)
    assert segmenter.active
    for _ in range(9):
        assert segmenter.feed(quiet) is None
    chunk = segmenter.feed(quiet)
    assert chunk is not None
    assert chunk.start_sample <= 12 * 1600 - 3000
    assert len(chunk.samples) <= 2.1 * 16000
    assert not segmenter.active


def test_max_length_and_manual_end(settings):
    settings.max_seconds = 2
    loud = tone(1000, 16000, 0.1)[:, None]
    segmenter = Segmenter(16000, settings)
    chunks = [chunk for _ in range(45) if (chunk := segmenter.feed(loud)) is not None]
    assert len(chunks) == 2
    assert all(len(c.samples) <= 32000 for c in chunks)
    manual = Segmenter(16000, settings, manual=True)
    for _ in range(4):
        assert manual.feed(loud, False) is None
    for _ in range(3):
        assert manual.feed(loud, True) is None
    assert manual.feed(loud, False) is not None


def test_empty_and_nonfinite_audio_rejected(settings):
    for samples in [np.array([]), np.array([np.nan]), np.array([np.inf])]:
        with pytest.raises(ValueError):
            preprocess(samples, 16000, settings)
