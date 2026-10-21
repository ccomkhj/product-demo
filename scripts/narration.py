"""When the voice says what: one scene's narration split into sentences and timed (stdlib only).

The voice's timing is estimated from the text alone: a scene's audio is shared out
across its sentences by character count (sentences only, not the spaces between
them), over the narration with its whitespace collapsed. Subtitle cues and the
``at`` phrases that actions are synced to both use this one rule, so an action
anchored to a phrase starts with the cue that holds it (see
docs/adr/0002-actions-synced-to-narration-phrases.md).

Times are *shares* of the scene's audio, from 0 to 1; multiply by its length in
seconds to place them.
"""

import re

# Words that end in a full stop without ending the sentence (compared lower-case,
# without the stop). Dotted forms such as "e.g." and "U.S." and single-letter
# initials are recognised without being listed.
ABBREVIATIONS = frozenset({
    "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "mt", "vs", "etc", "approx",
    "fig", "inc", "ltd", "co", "corp", "dept",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
})

_BOUNDARY = re.compile(r"[.!?]+[\"')\]]*\s+")


class PhraseError(ValueError):
    """A phrase that can't be placed in the narration; ``index`` is its place in the list."""

    def __init__(self, index, message):
        super().__init__(message)
        self.index = index


class Narration:
    """One scene's narration text, split into sentences and timed by character share."""

    def __init__(self, text):
        self.text = " ".join(text.split())
        spans, cursor = [], 0
        for sentence in _split_sentences(self.text):
            start = self.text.find(sentence, cursor)
            spans.append((start, sentence))
            cursor = start + len(sentence)
        self._spans = tuple(spans)
        self._chars = sum(len(sentence) for _, sentence in spans)

    def cue_shares(self):
        """One (start, end, sentence) per sentence, start and end as shares of the audio."""
        cues, done = [], 0
        for _, sentence in self._spans:
            start = done / self._chars
            done += len(sentence)
            cues.append((start, done / self._chars, sentence))
        return tuple(cues)

    def phrase_shares(self, phrases):
        """When the voice reaches each phrase, as a share of the audio.

        Phrases match case-insensitively and with whitespace collapsed, each at or
        after the previous one, so they keep the narration's order. A phrase that
        is empty, not a string, or not found that way raises PhraseError.
        """
        text = self.text.lower()
        shares, cursor = [], 0
        for index, phrase in enumerate(phrases):
            if not isinstance(phrase, str) or not phrase.strip():
                raise PhraseError(index, f"must be a phrase from the narration, got {phrase!r}")
            needle = " ".join(phrase.split()).lower()
            found = text.find(needle, cursor)
            if found < 0:
                place = " after the previous phrase" if needle in text else ""
                raise PhraseError(index, f"phrase {phrase!r} is not in the narration{place}")
            shares.append(self._share_at(found))
            cursor = found
        return shares

    def _share_at(self, position):
        """The share of the audio spoken before character ``position`` of the text."""
        done = 0
        for start, sentence in self._spans:
            if position < start + len(sentence):
                done += max(0, position - start)
                break
            done += len(sentence)
        return done / self._chars


def _split_sentences(text):
    """Split narration into sentences without breaking after abbreviations like "e.g." or "Dr."."""
    sentences, start = [], 0
    for match in _BOUNDARY.finditer(text):
        candidate = text[start:match.end()].strip()
        if match.group().startswith(".") and _ends_with_abbreviation(candidate):
            continue
        sentences.append(candidate)
        start = match.end()
    if text[start:].strip():
        sentences.append(text[start:].strip())
    return sentences


def _ends_with_abbreviation(candidate):
    word = candidate.rstrip("\"')]").split(" ")[-1].lstrip("\"'([")
    stem = word[:-1] if word.endswith(".") else word
    if "." in stem:  # e.g. / i.e. / U.S.
        return True
    if len(stem) == 1 and stem.isalpha():  # an initial, as in "J. Smith"
        return True
    return stem.lower() in ABBREVIATIONS
