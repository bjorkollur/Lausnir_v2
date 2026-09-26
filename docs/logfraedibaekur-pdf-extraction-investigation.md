# Lögfræðibækur — PDF-to-markdown extraction investigation

Status: **paused, no code changes made yet**. This document summarizes the investigation so it can be picked up later without re-deriving everything.

## Background

Books ingested via the `logfraedibaekur` source currently use the same `parse_pdf()`/pdfplumber pipeline used for short court rulings. For book-length PDFs this produces garbage:

- Repeated cover-page text
- Corrupted table of contents: dot leaders, garbled roman numerals (e.g. "Vil." instead of "VII."), stray artifacts
- No heading structure, no list/enumeration detection

User's direction: find a better PDF-to-markdown tool before touching the import pipeline. Also flagged separately (not yet fixed): `DocPanel.tsx`'s document header shows court-case fields ("Mál nr.", "gegn") for books, which don't apply — needs a dedicated book header, not adapted court-case markup.

## Evaluation criteria (user-specified, in priority order)

1. Footnote handling
2. Heading handling
3. Page number handling (roman numerals in TOC)
4. List/enumeration handling

## Root cause of the corruption

The source PDF's embedded font has a **text-layer character-encoding bug**. Roman numerals and some special characters render wrong through *any* text-layer-based extractor — pdfplumber, Docling's text mode, marker's fast mode — because they all trust the embedded text layer. Only true visual OCR (rasterize the page, ignore the text layer) can see past it.

## Tools tested

| Criterion | pdfplumber | Docling (text mode) | Docling `--force-ocr` | marker fast | marker balanced |
|---|---|---|---|---|---|
| Headings | ❌ none | ✅ | ✅ | ✅ | ✅ |
| Page numbers (TOC) | ❌ | ❌ | ✅ | ❌ | ❌ |
| Lists/enumerations | ❌ | ✅ | ✅ | ❌ | ❌ |
| Footnotes | ✅ plain | ✅ plain | ❌ garbled | ✅ plain | ✅✅ best (linked anchors) |

**Docling `--force-ocr --ocr-engine tesseract --ocr-lang isl` currently wins 3/4 criteria.** It's slower than pdfplumber (~15-20 min/book vs seconds) but fixes the roman-numeral/page-number corruption because it rasterizes and OCRs every page instead of trusting the broken text layer. Its one weakness: footnote quality degrades under force-OCR.

Docling's default mode (full ML layout model, no OCR) was tried on the theory it'd be faster than force-OCR — it was actually **slower** (~68 min/552-page book) and still has the text-layer corruption since it doesn't OCR.

### marker (`datalab-to/marker`) — LLM-correction detour

Investigated whether `marker --use_llm` could close marker's TOC/roman-numeral gap via LLM-based post-correction processors, using local Ollama (`llama3.1-is`, `llama3.1:8b-instruct-q5_K_M`).

- Hit and root-caused a real bug in marker's `OllamaService`: it strips `$defs` when building the JSON schema sent to Ollama, leaving a dangling `$ref` for any processor with nested Pydantic models. Patched in a throwaway venv (`/tmp/marker_venv`, not part of this repo) to confirm the theory — fix worked for `LLMSectionHeaderProcessor` (13-15s real inference, correct output).
- But `LLMSectionHeaderProcessor` only touches header/hierarchy blocks — it made **zero changes** to the TOC corruption (verified via diff).
- Tried `LLMPageCorrectionProcessor` next (the general-purpose block corrector) — it requires an explicit `--block_correction_prompt` flag to activate (silently no-ops otherwise, which is why it never appeared in earlier runs).
- Every call to it failed with an Ollama 400 error. Root-caused via direct API reproduction: **`LLMPageCorrectionProcessor` always sends the page image** (it has to — it's supposed to look at the image to fix what the text layer got wrong), but our local Ollama models (`llama3.1-is`, `llama3.1:8b-instruct-q5_K_M`) are **text-only, no vision support**. Confirmed error: `"Multimodal data provided, but model does not support multimodal requests."` Not a concurrency issue, not a schema issue — the model itself can't do this task.
- **Conclusion: marker's LLM-correction path cannot fix the TOC/roman-numeral problem without pulling a vision-capable Ollama model first** (e.g. `llama3.2-vision`, `qwen2.5vl`, `llava` — none currently installed, multi-GB download, untested payoff).

### Declined (security policy, not revisited)

Three third-party OCR forks were evaluated and declined as unsafe to run: `baidu/Unlimited-OCR` (hard CUDA dependency, no GPU here), `sabafallah/Unlimited-OCR-Universal` (unverified individual publisher, requires `trust_remote_code=True`), `AutomatosX/AX-Unlimited-OCR-3B-MoE-MLX-MXFP8` (unverified publisher, unaudited compiled binary runtime). This stands regardless of which extraction tool is chosen.

## Key discovery: books are not all the same kind of PDF

User flagged (correctly) that books vary: some have real embedded text, others are scans with an image covering the page and a text layer "in the background." Checked this empirically against all 3 books currently in `Lausnir_Data/raw/logfraedibaekur/`, sampling pages with pdfplumber and looking at `page.images` coverage:

| Book | Pages | Full-page image on sampled pages? | Text quality |
|---|---|---|---|
| `9789979825968.pdf` (Afmælisrit Jóns Steinars) | 392 | No (0 images) | ✅ Clean — TOC dot-leaders and page numbers correct, no corruption |
| `9979545593.pdf` | 200 | No (0 images) | ✅ Clean — same |
| `9789935233202.pdf` (Afbrot — the sample used for the whole tool comparison above) | 552 | **Yes — 1 image per page, `img_area_ratio` ≈ 1.00** | ❌ Corrupted (this is the ONLY book with the roman-numeral/TOC corruption problem) |

**This means the entire tool-comparison investigation above was solving a problem specific to scanned books, not books in general.** The two native-text books already extract perfectly with plain pdfplumber — their only gap is markdown structure (headings/lists), not text correctness.

**Decision (confirmed with user):** classify each incoming book PDF before extraction:
- **Native text** (no full-page image on sampled pages) → run through **Docling in default mode, no OCR** — text layer is trustworthy, Docling's ML layout model adds heading/list structure, no need to pay the OCR time cost (~15-20 min/book).
- **Scanned** (full-page image covering most sampled pages) → run through **Docling `--force-ocr`** — the only mode that fixes the corrupted embedded text layer, at the cost of degraded footnote quality (accepted tradeoff).

Detection heuristic: sample a handful of pages (e.g. 5), check `page.images` for an image whose bbox area covers close to the full page area. No linguistic/garbling heuristic needed — the presence of a full-page image is a clean, reliable signal on its own.

## Where this was paused

Conversation paused (user: "Stoppum aðeins núna") right after confirming the native/scanned classification + dispatch design above. **No code has been written yet for the classifier or the dispatch logic.**

## To resume

1. Implement a small classifier (e.g. `is_scanned_pdf(path) -> bool` using the `page.images` full-page-coverage heuristic above, sampling ~5 pages) in `engine/processors/` (alongside or inside the existing PDF parsing code).
2. Wire it into book ingestion (`scripts/import_baekur.py` or wherever `logfraedibaekur` body-text extraction happens) to dispatch: native → `docling` default mode, scanned → `docling --force-ocr`. Replace the generic `parse_pdf()` call for this source with this dispatch.
3. Re-process the 3 already-imported books' `body_text` with the new pipeline (2 through the fast native path, 1 through force-OCR).
4. Separately (independent of the above): fix `DocPanel.tsx`'s header for books — remove "Mál nr." / "gegn" court-case markup, add a book-appropriate header (title/author/date) sourced from the already-trustworthy API-derived metadata (`resolve_book_metadata()`), not from a variant of the court-ruling template. Not started.

## Where the diagnostic artifacts live

Everything from this investigation is scratch work in `/tmp` (sample PDFs, extraction outputs, the patched marker venv at `/tmp/marker_venv`) — none of it is part of the repo and can be discarded freely.
