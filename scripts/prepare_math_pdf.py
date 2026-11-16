"""Prepare a scanned, mathematics-heavy PDF for ingestion by RAGU.

The script deliberately keeps the PDF preprocessing layer outside the RAGU
pipeline. RAGU consumes text; this tool produces text, page images, and a
manifest that preserves the link back to the source PDF page.

The local path uses Poppler and Tesseract. Mathpix can optionally be used for
page-level OCR when MATHPIX_APP_ID and MATHPIX_APP_KEY are available.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


FORMULA_CHARS = set("=∑∫√∞≤≥≠≈∂∇αβγδεζηθλμνξπρστφχψω^_{}[]")
PAGE_MARKER = "<!-- source-pdf-page: {page} -->"


@dataclass(slots=True)
class PageResult:
    """Processing metadata for one source PDF page."""

    page: int
    source: str
    text_path: str
    image_path: str | None
    character_count: int
    formula_candidate_count: int
    needs_review: bool
    review_reasons: list[str]


def _command_or_error(command: str) -> str:
    path = shutil.which(command)
    if path is None:
        raise RuntimeError(
            f"Required command '{command}' was not found. "
            "Install Poppler (pdftoppm/pdftotext) and Tesseract, or pass "
            "--ocr-engine mathpix."
        )
    return path


def _run(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout


def _pdf_page_count(pdf_path: Path) -> int:
    pdfinfo = _command_or_error("pdfinfo")
    output = _run([pdfinfo, str(pdf_path)])
    match = re.search(r"^Pages:\s+(\d+)$", output, re.MULTILINE)
    if match is None:
        raise RuntimeError("pdfinfo did not report a page count")
    return int(match.group(1))


def _extract_text_page(pdf_path: Path, page: int) -> str:
    pdftotext = _command_or_error("pdftotext")
    return _run([pdftotext, "-f", str(page), "-l", str(page), "-layout", str(pdf_path), "-"])


def _render_page(pdf_path: Path, page: int, output_dir: Path, dpi: int) -> Path:
    pdftoppm = _command_or_error("pdftoppm")
    prefix = output_dir / f"page-{page:04d}"
    expected = prefix.with_suffix(".png")
    if not expected.exists():
        _run([
            pdftoppm,
            "-f",
            str(page),
            "-l",
            str(page),
            "-r",
            str(dpi),
            "-png",
            "-singlefile",
            str(pdf_path),
            str(prefix),
        ])
    return expected


def _tesseract_page(image_path: Path, language: str, psm: int) -> str:
    tesseract = _command_or_error("tesseract")
    return _run([
        tesseract,
        str(image_path),
        "stdout",
        "-l",
        language,
        "--psm",
        str(psm),
    ])


def _mathpix_page(image_path: Path, app_id: str, app_key: str) -> str:
    """Run Mathpix page OCR, including math in the returned text."""
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - requests is a project dependency
        raise RuntimeError("Mathpix OCR requires the 'requests' package") from exc

    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    response = requests.post(
        "https://api.mathpix.com/v3/text",
        headers={"app_id": app_id, "app_key": app_key, "Content-Type": "application/json"},
        json={"src": f"data:image/png;base64,{encoded}", "formats": ["text"]},
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError(f"Mathpix returned no text for {image_path.name}: {payload}")
    return text


def _normalise_text(text: str) -> str:
    text = text.replace("\x0c", "")
    text = text.replace("\u00ad", "")
    text = text.replace("\u0000", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _formula_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    for line in text.splitlines():
        stripped = " ".join(line.split())
        if not stripped:
            continue
        symbol_count = sum(char in FORMULA_CHARS for char in stripped)
        has_equation = "=" in stripped or "→" in stripped or "⇔" in stripped
        has_math_word = bool(re.search(r"\b(?:min|max|argmin|argmax|lim|sup|inf)\b", stripped))
        if symbol_count >= 2 or (has_equation and has_math_word):
            candidates.append(stripped)
    return candidates


def _write_page_markdown(
    path: Path,
    page: int,
    text: str,
    image_relative_path: str | None,
    formula_candidates: list[str],
) -> None:
    lines = [PAGE_MARKER.format(page=page), f"# Страница {page}", "", text]
    if image_relative_path:
        lines.extend(["", f"Источник страницы: `{image_relative_path}`"])
    if formula_candidates:
        lines.extend([
            "",
            "## Кандидаты математических фрагментов",
            "",
            "> Автоматически найденные строки. Проверяйте их по изображению страницы.",
            "",
        ])
        lines.extend(f"- `{candidate}`" for candidate in formula_candidates)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _write_book(path: Path, page_paths: list[Path]) -> None:
    with path.open("w", encoding="utf-8") as output:
        output.write("# Основы методов оптимизации\n\n")
        output.write(
            "Корпус подготовлен автоматически. Номера страниц и изображения "
            "сохранены для проверки OCR, формул и рисунков.\n\n"
        )
        for page_path in page_paths:
            page_text = page_path.read_text(encoding="utf-8")
            # Page files live two levels below the output directory, while
            # book.md lives one level below it; adjust only the asset link.
            page_text = page_text.replace("../../pages/", "../pages/")
            output.write(page_text)
            output.write("\n\n---\n\n")


def prepare_pdf(
    pdf_path: Path,
    output_dir: Path,
    *,
    dpi: int = 300,
    language: str = "rus+eng",
    psm: int = 3,
    ocr_engine: str = "tesseract",
    first_page: int = 1,
    last_page: int | None = None,
    keep_images: bool = True,
    min_text_length: int = 80,
) -> dict[str, Any]:
    """Convert a PDF to RAGU-ready Markdown and an auditable manifest."""
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    if first_page < 1:
        raise ValueError("first_page must be >= 1")

    total_pages = _pdf_page_count(pdf_path)
    last_page = total_pages if last_page is None else min(last_page, total_pages)
    if first_page > last_page:
        raise ValueError("first_page must not be greater than last_page")

    output_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = output_dir / "pages"
    texts_dir = output_dir / "ragu_docs" / "pages"
    pages_dir.mkdir(exist_ok=True)
    texts_dir.mkdir(parents=True, exist_ok=True)

    if ocr_engine not in {"tesseract", "mathpix"}:
        raise ValueError("ocr_engine must be 'tesseract' or 'mathpix'")

    app_id = os.getenv("MATHPIX_APP_ID")
    app_key = os.getenv("MATHPIX_APP_KEY")
    if ocr_engine == "mathpix" and (not app_id or not app_key):
        raise RuntimeError("Mathpix requires MATHPIX_APP_ID and MATHPIX_APP_KEY")

    results: list[PageResult] = []
    page_paths: list[Path] = []
    for page in range(first_page, last_page + 1):
        extracted = _normalise_text(_extract_text_page(pdf_path, page))
        source = "embedded-text"
        image_path: Path | None = None
        if len(extracted) < min_text_length:
            image_path = _render_page(pdf_path, page, pages_dir, dpi)
            if ocr_engine == "mathpix":
                assert app_id is not None and app_key is not None
                extracted = _normalise_text(_mathpix_page(image_path, app_id, app_key))
                source = "mathpix"
            else:
                extracted = _normalise_text(_tesseract_page(image_path, language, psm))
                source = "tesseract"

        candidates = _formula_candidates(extracted)
        page_path = texts_dir / f"page-{page:04d}.md"
        image_relative = os.path.relpath(image_path, page_path.parent) if image_path else None
        _write_page_markdown(page_path, page, extracted, image_relative, candidates)
        page_paths.append(page_path)
        reasons: list[str] = []
        if len(extracted) < min_text_length:
            reasons.append("short OCR result")
        if candidates:
            reasons.append("formula candidates require verification")
        if image_path and not keep_images:
            image_path.unlink()
            image_path = None
        results.append(PageResult(
            page=page,
            source=source,
            text_path=str(page_path.relative_to(output_dir)),
            image_path=str(image_path.relative_to(output_dir)) if image_path else None,
            character_count=len(extracted),
            formula_candidate_count=len(candidates),
            needs_review=bool(reasons),
            review_reasons=reasons,
        ))

    _write_book(output_dir / "ragu_docs" / "book.md", page_paths)
    manifest = {
        "source_pdf": str(pdf_path.resolve()),
        "page_range": [first_page, last_page],
        "total_pdf_pages": total_pages,
        "ocr_engine": ocr_engine,
        "dpi": dpi,
        "language": language if ocr_engine == "tesseract" else None,
        "ragu_input": str((output_dir / "ragu_docs").resolve()),
        "pages": [asdict(result) for result in results],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    review_queue = [item for item in results if item.needs_review]
    (output_dir / "review_queue.json").write_text(
        json.dumps([asdict(item) for item in review_queue], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument("--ocr-engine", choices=("tesseract", "mathpix"), default="tesseract")
    parser.add_argument("--language", default="rus+eng", help="Tesseract language pack, e.g. rus+eng")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--psm", type=int, default=3, help="Tesseract page segmentation mode")
    parser.add_argument("--first-page", type=int, default=1)
    parser.add_argument("--last-page", type=int)
    parser.add_argument("--min-text-length", type=int, default=80)
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Do not retain rendered page PNGs (not recommended for formula/figure verification)",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        manifest = prepare_pdf(
            args.pdf,
            args.output_dir,
            dpi=args.dpi,
            language=args.language,
            psm=args.psm,
            ocr_engine=args.ocr_engine,
            first_page=args.first_page,
            last_page=args.last_page,
            keep_images=not args.no_images,
            min_text_length=args.min_text_length,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Prepared {len(manifest['pages'])} pages in {args.output_dir}")
    print(f"RAGU input: {manifest['ragu_input']}")
    print(f"Review queue: {args.output_dir / 'review_queue.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
