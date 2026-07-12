"""User questions for Stage 4 interactive recovery.

When intent recovery is not confident, it emits ``Question`` objects instead
of guessing. The GUI presents them; answers flow back into the feature
parameters before reconstruction.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Question:
    """One decision the user must make.

    Attributes:
        question_id: Stable id within one recovery run.
        text: The question, phrased for a mechanical engineer
            ("These 4 holes differ by <0.03 mm. Make them identical?").
        options: Answer labels; the first is the recommended default.
        feature_ids: Features affected by the answer (for highlighting).
        patch_ids: Surface patches to highlight in the viewport.
        answer: Index into ``options`` once the user decides, else None.
    """

    question_id: int
    text: str
    options: list[str]
    feature_ids: list[int] = field(default_factory=list)
    patch_ids: list[int] = field(default_factory=list)
    answer: int | None = None

    @property
    def answered(self) -> bool:
        return self.answer is not None

    def accepted_default(self) -> bool:
        """True when the user picked the recommended (first) option."""
        return self.answer == 0
