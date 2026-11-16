# PDF preprocessing for mathematical books

`prepare_math_pdf.py` converts a text/scanned PDF into a corpus that RAGU can
consume. RAGU itself accepts text, not PDF, OCR, or page images.

The converter:

- reuses embedded PDF text when it is present;
- renders scanned pages with Poppler and OCRs them with Tesseract;
- optionally uses Mathpix for page OCR with mathematical notation;
- writes one Markdown file per page and a combined `book.md`;
- keeps page PNGs for checking formulas and figures;
- records processing metadata in `manifest.json`;
- creates `review_queue.json` for short OCR results and formula candidates.

## Local OCR

Install Poppler, Tesseract, and the Russian language pack. Then run:

```bash
python scripts/prepare_math_pdf.py \
  "/path/to/book.pdf" \
  --output-dir "./working/math_book" \
  --ocr-engine tesseract \
  --language rus+eng \
  --dpi 300
```

The directory `working/math_book/ragu_docs` is the text corpus for RAGU.
Keep the `pages` directory and inspect `review_queue.json` before indexing.

## Mathpix OCR

Mathpix is optional and requires credentials:

```bash
export MATHPIX_APP_ID="..."
export MATHPIX_APP_KEY="..."
python scripts/prepare_math_pdf.py \
  "/path/to/book.pdf" \
  --output-dir "./working/math_book_mathpix" \
  --ocr-engine mathpix
```

Formula candidates are deliberately marked for verification. Automatic OCR is
not treated as authoritative for indices, signs, matrices, or inequalities.
