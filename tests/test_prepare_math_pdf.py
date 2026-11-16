from __future__ import annotations

from scripts.prepare_math_pdf import _formula_candidates, _normalise_text


def test_normalise_text_removes_pdf_artifacts() -> None:
    assert _normalise_text("a\u00adb\x0c\n\n\ntext") == "ab\n\ntext"


def test_formula_candidates_are_reviewable() -> None:
    candidates = _formula_candidates("Обычный текст\nf(x) = x^2 + 1\nещё текст")
    assert candidates == ["f(x) = x^2 + 1"]
