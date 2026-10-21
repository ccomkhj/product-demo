"""A stand-in raw recording for edit-plan tests: the ``video`` that build_plan measures."""

from edit_plan import FPS


class FakeVideo:
    """No frozen frames, unless ``decimated`` maps a (trim_start, trim_end) window to seconds.

    ``measured`` lists the windows build_plan asked about; ``scanned`` says whether it
    looked for the sync flash, which shows at ``flash_s`` (None: there is none).
    """

    def __init__(self, decimated=None, flash_s=None):
        self._decimated = decimated or {}
        self._flash_s = flash_s
        self.measured = []
        self.scanned = False

    def flash_s(self):
        self.scanned = True
        return self._flash_s

    def frames(self, trim_start, trim_end):
        self.measured.append((trim_start, trim_end))
        return round(self._decimated.get((trim_start, trim_end), trim_end - trim_start) * FPS)
