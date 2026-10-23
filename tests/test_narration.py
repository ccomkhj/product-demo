import pytest

from narration import Narration, PhraseError


def test_cues_share_the_audio_across_sentences_by_character_count():
    cues = Narration("One two.  Three   four five six.").cue_shares()

    assert [sentence for *_, sentence in cues] == ["One two.", "Three four five six."]
    assert cues[0][:2] == (0.0, pytest.approx(8 / 28))
    assert cues[1][:2] == (pytest.approx(8 / 28), 1.0)


def test_abbreviations_and_initials_do_not_end_a_sentence():
    cues = Narration("Ask Dr. Who, e.g. at 5 p.m. today. J. Smith agrees!").cue_shares()

    assert [sentence for *_, sentence in cues] == [
        "Ask Dr. Who, e.g. at 5 p.m. today.",
        "J. Smith agrees!",
    ]


def test_a_phrase_starts_with_the_cue_of_the_sentence_it_opens():
    narration = Narration("First we open the list. Then we click Save.")

    [share] = narration.phrase_shares(["then we click"])

    assert share == pytest.approx(narration.cue_shares()[1][0])


def test_a_phrase_inside_a_sentence_is_timed_by_the_characters_before_it():
    narration = Narration("Open the list.  Click   Save.")

    assert narration.phrase_shares(["the list", "CLICK save"]) == pytest.approx([5 / 25, 14 / 25])


def test_phrases_must_keep_the_narrations_order():
    with pytest.raises(PhraseError, match="after the previous phrase") as caught:
        Narration("Click Save, then the heading.").phrase_shares(["the heading", "Click Save"])

    assert caught.value.index == 1


def test_a_phrase_not_in_the_narration_names_its_index():
    with pytest.raises(PhraseError, match="'Delete' is not in the narration$") as caught:
        Narration("Click Save.").phrase_shares(["Click", "Delete"])

    assert caught.value.index == 1


@pytest.mark.parametrize("phrase", ["", "  ", 3, None])
def test_a_phrase_must_be_non_empty_text(phrase):
    with pytest.raises(PhraseError, match="must be a phrase"):
        Narration("Click Save.").phrase_shares([phrase])
