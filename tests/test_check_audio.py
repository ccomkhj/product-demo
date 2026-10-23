import pytest

from check_audio import compare

NARRATION = "Open the orders page and pick the late delivery from last week."
WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike "
         "november oscar papa quebec romeo sierra tango").split()


def test_leaked_reference_words_at_the_start_are_flagged():
    verdict, ratio, diff = compare(NARRATION, "This is my voice. " + NARRATION)

    assert verdict == "check"
    assert ratio < 0.9
    assert diff == ['+ "this is my voice"']


def test_a_misspoken_word_is_shown_as_what_was_heard_instead():
    verdict, _, diff = compare(NARRATION, NARRATION.replace("delivery", "livery"))

    assert verdict == "check"
    assert diff == ['"delivery" -> "livery"']


def test_a_dropped_word_is_shown_as_missing():
    _, _, diff = compare(NARRATION, NARRATION.replace(" late", ""))

    assert diff == ['- "late"']


def test_one_wrong_word_passes_in_a_long_scene_but_not_in_a_short_one():
    # 20 words, one substituted: 19/20 = 0.95 matches, the lowest ratio that passes.
    long = " ".join(WORDS[:20])
    verdict, ratio, _ = compare(long, long.replace("golf", "other"))
    assert (verdict, ratio) == ("ok", 0.95)

    # 19 words, one substituted: 18/19 < 0.95.
    short = " ".join(WORDS[:19])
    verdict, ratio, _ = compare(short, short.replace("golf", "other"))
    assert verdict == "check"
    assert ratio < 0.95


def test_an_exact_match_is_ok_with_no_differences():
    assert compare(NARRATION, NARRATION) == ("ok", 1.0, [])


def test_case_and_punctuation_are_ignored():
    assert compare('Click "Save" -- then, done!', "click save. Then done")[0:2] == ("ok", 1.0)


def test_apostrophes_do_not_split_words():
    assert compare("It's the team's view.", "its the teams view")[0:2] == ("ok", 1.0)


def test_hyphens_split_words():
    assert compare("Real-time sync, end-to-end.", "real time sync end to end")[0:2] == ("ok", 1.0)


def test_digits_and_number_words_match():
    narration = "Pick 3 of the 21 items, about 2.5 days and 1,200 euros, 40% off."
    heard = "Pick three of the twenty-one items, about two point five days and 1200 euros, forty percent off."

    assert compare(narration, heard)[0:2] == ("ok", 1.0)


def test_large_numbers_become_words_on_both_sides():
    heard = "two thousand twenty six, one million three hundred thousand"
    assert compare("2026, 1,300,000", heard)[0:2] == ("ok", 1.0)


def test_the_platform_error_names_the_audio_check(monkeypatch):
    import voice_config
    monkeypatch.setattr(voice_config.platform, "machine", lambda: "x86_64")
    with pytest.raises(ValueError, match="^check_audio requires native Apple Silicon"):
        voice_config.check_platform("check_audio")
