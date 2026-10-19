import pytest

from edit_plan import fit


def test_video_shorter_than_audio_holds_last_frame():
    result = fit(video_s=2.0, audio_s=5.0)

    assert result.strategy == "hold"
    assert result.speed == 1.0
    assert result.hold_s == pytest.approx(3.0)
    assert result.pad_s == 0.0
    assert result.scene_s == pytest.approx(5.0)


def test_video_equal_to_audio_holds_for_nothing():
    result = fit(video_s=4.0, audio_s=4.0)

    assert result.strategy == "hold"
    assert result.hold_s == 0.0
    assert result.scene_s == pytest.approx(4.0)


def test_zero_length_video_holds_for_the_whole_narration():
    result = fit(video_s=0.0, audio_s=3.5)

    assert result.strategy == "hold"
    assert result.hold_s == pytest.approx(3.5)
    assert result.scene_s == pytest.approx(3.5)


def test_modestly_longer_video_is_sped_up_to_audio_length():
    result = fit(video_s=5.0, audio_s=4.0)

    assert result.strategy == "speedup"
    assert result.speed == pytest.approx(1.25)
    assert result.hold_s == 0.0
    assert result.pad_s == 0.0
    assert result.scene_s == pytest.approx(4.0)


def test_exactly_one_and_a_half_times_is_sped_up_without_padding():
    result = fit(video_s=6.0, audio_s=4.0)

    assert result.strategy == "speedup"
    assert result.speed == pytest.approx(1.5)
    assert result.pad_s == 0.0
    assert result.scene_s == pytest.approx(4.0)


def test_much_longer_video_is_capped_at_one_and_a_half_and_audio_is_padded():
    result = fit(video_s=9.0, audio_s=4.0)

    assert result.strategy == "pad"
    assert result.speed == pytest.approx(1.5)
    assert result.scene_s == pytest.approx(6.0)
    assert result.pad_s == pytest.approx(2.0)
    assert result.hold_s == 0.0


def test_audio_length_must_be_positive():
    with pytest.raises(ValueError, match="audio length"):
        fit(video_s=1.0, audio_s=0.0)
