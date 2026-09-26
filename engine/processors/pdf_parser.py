import io
import re
from collections import defaultdict

import pdfplumber

# Numbered paragraph: "10. Capital letter..." — not "1. mgr." or "1. gr."
# \d{1,3} prevents 4-digit years (e.g. "2015. Þar er...") from being treated
# as new paragraphs when a sentence wraps across a line or page boundary.
_NEW_PARA = re.compile(r'^\s*\d{1,3}\.\s+[A-ZÁÉÍÓÚÝÞÆÖ„\"\«]')
_HEADING_LINE = re.compile(r'^\s*#')
_PARA_NUM_ONLY = re.compile(r'^\s*\d{1,3}\.\s*$')  # "5." eða "99." ein á línu — aldrei heading (ekki ártöl)
_MARGIN_NUM = re.compile(r'^\d{1,3}$')  # "2", "14" — málsgreinanúmer án punkts


def parse_pdf(
    content: bytes,
    header_pt: int = 0,
    footer_pt: int = 0,
    skip_header_on_first: bool = False,
    heading_sizes: dict | None = None,
    heading_fonts: dict | None = None,
    extract_tables: bool = False,
    footnotes: bool = False,
    _plain_structured: bool = False,
) -> str:
    """
    Þáttar PDF í Markdown-texta.

    header_pt / footer_pt      : klippir síðuhausa/-fætur af (í punktum).
    skip_header_on_first       : ef True, beitt header_pt ekki á fyrstu síðu.
    heading_sizes              : {font_size: "## "} — línur í þessari stærð fá fyrirsagnarmerki.
    heading_fonts              : {font_substring: "### "} — línur þar sem fontname inniheldur
                                 lykil fá fyrirsagnarmerki (notað fyrir bold/italic fyrirsagnir).
    extract_tables             : ef True, leita á töflum á hverri síðu og setja
                                 markdown töflu þar á sviðinu þar sem hún birtist.
                                 Texti utan töflu er extracted venjulega.
    footnotes                  : ef True, para fótnótunúmer við texta eftir
                                 staðsetningu og skila þeim sem GFM-fótnótum
                                 ([^1] í meginmáli, [^1]: skýring í lokin).
                                 Ætlað fræðiritum; dómaskjöl nota þetta ekki.
    """
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        pages = []
        collected_notes: list[tuple[int, str]] = []
        for i, page in enumerate(pdf.pages):
            apply_header = header_pt and (i > 0 or not skip_header_on_first)
            if apply_header or footer_pt:
                y0 = header_pt if apply_header else 0
                y1 = page.height - footer_pt if footer_pt else page.height
                page = page.crop((0, y0, page.width, y1))

            if footnotes:
                text, notes = _extract_page_with_footnotes(page, footnotes=True)
                if _plain_structured:
                    # Numbering restarts per contribution, so notes cannot be
                    # linked document-wide. They are still lifted out of the
                    # prose and shown under the page they belong to — leaving
                    # them inline breaks sentences mid-argument.
                    if notes:
                        text = f"{text}\n\n" + "\n".join(
                            f"> {n}. {txt}" for n, txt in notes
                        )
                else:
                    collected_notes.extend(notes)
            elif extract_tables:
                text = _extract_with_tables(
                    page,
                    heading_sizes=heading_sizes or {},
                    heading_fonts=heading_fonts or {},
                )
            elif heading_sizes or heading_fonts:
                text = _extract_with_headings(page, heading_sizes or {}, heading_fonts or {})
            else:
                text = page.extract_text(x_tolerance=2, y_tolerance=2)

            if text and text.strip():
                pages.append(text.strip())

    body = _join_pages(pages)
    if footnotes and not _plain_structured:
        if _numbering_restarts(body):
            # Re-extract with the note block left in place. Still the structured
            # path — a collection needs its paragraphs and headings as much as
            # any other book, and the plain path returns one wall of text.
            return parse_pdf(
                content,
                header_pt=header_pt, footer_pt=footer_pt,
                skip_header_on_first=skip_header_on_first,
                heading_sizes=heading_sizes, heading_fonts=heading_fonts,
                extract_tables=extract_tables, footnotes=True,
                _plain_structured=True,
            )
        block = _render_footnotes(collected_notes, body)
        if block:
            body = f"{body}\n\n{block}"
    return body


def _numbering_restarts(body: str) -> bool:
    """True when footnote markers repeat so often that numbering must restart.

    A single-author work uses each number once. A collection of contributions
    uses `1` once per contribution — so the ratio of distinct markers to total
    markers collapses.
    """
    refs = re.findall(r'\[\^(\d+)\](?!:)', body)
    if len(refs) < 20:  # too few to judge; a handful of repeats proves nothing
        return False
    return len(set(refs)) / len(refs) < _MIN_UNIQUE_MARKER_RATIO


def _join_pages(pages: list[str]) -> str:
    """
    Samskeytir síður með greiningu á hvort síðuskil séu innan málsgreinar
    eða á milli þeirra.

    Reglur:
      - Ef næsta síða byrjar á tölusettri málsgrein eða heading → \n\n
      - Ef fyrsta orð næstu síðu byrjar á lágstaf → framhald → \n
      - Annars → \n\n
    """
    if not pages:
        return ""

    result = pages[0]
    for nxt in pages[1:]:
        first_word = nxt.split()[0] if nxt.split() else ""
        if _NEW_PARA.match(nxt) or _HEADING_LINE.match(nxt):
            result = result + "\n\n" + nxt
        elif first_word and first_word[0].islower():
            result = result + "\n" + nxt
        else:
            result = result + "\n\n" + nxt
    return result


def _extract_with_headings(page, heading_sizes: dict, heading_fonts: dict) -> str:
    """
    Endursmíðar texta af síðu og bætir markdown-fyrirsagnarmerkjum
    við línur sem eru skrifaðar í stærra eða sérstöku letri.
    """
    use_inline_italic = bool(heading_fonts)
    words = page.extract_words(
        x_tolerance=2,
        y_tolerance=2,
        extra_attrs=["size", "fontname"],
        keep_blank_chars=False,
    )
    if not words:
        return ""

    line_map: dict[int, list] = defaultdict(list)
    for w in words:
        key = round(w["top"])
        line_map[key].append(w)

    body_size = _most_common_size(words)
    line_tops = sorted(line_map)
    gaps = [line_tops[j] - line_tops[j - 1] for j in range(1, len(line_tops))]
    typical_gap = sorted(gaps)[len(gaps) // 2] if gaps else 14

    # Við notum "blokkir": fyrirsagnir eru einar og sér, meginmálslínur
    # eru samansteypts í eina línu með bili á milli.
    blocks: list[str] = []   # lokaðar blokkir
    current: list[str] = []  # línur í núverandi blokk
    prev_top = None
    pending_para_num: str | None = None  # málsgreinanúmer sem bíður næstu línu

    def flush(prefix: str = "") -> None:
        if current:
            blocks.append(prefix + " ".join(current))
            current.clear()

    for top in line_tops:
        line_words = sorted(line_map[top], key=lambda w: w["x0"])
        line_text = " ".join(w["text"] for w in line_words)
        dominant_size = round(_most_common_size(line_words), 1)
        dominant_font = line_words[0].get("fontname", "")

        gap = (top - prev_top) if prev_top is not None else None
        is_big_gap = gap is not None and gap >= typical_gap * 1.5
        is_section_start = prev_top is None or is_big_gap

        # Málsgreinanúmer án punkts við jaðar (t.d. "4" í 10pt við hlið 12pt meginmáls)
        # Síðasta lína í current er BYRJUN þessarar málsgreinar — við poppum hana,
        # flush-um það sem á undan er og byrjum nýja blokk með númerinu fremst.
        if _MARGIN_NUM.match(line_text.strip()) and dominant_size < body_size - 0.5:
            num = line_text.strip()
            if current:
                last_line = current.pop()
                flush()
                # Bæta aðeins við tómri línu ef síðasta block er ekki þegar tóm —
                # forðast tvær consecutive tómar línur þegar is_big_gap hafði þegar
                # bætt við einni áður.
                if blocks and blocks[-1] != "":
                    blocks.append("")
                current.append(f"{num}. {last_line}")
            else:
                pending_para_num = num
            # prev_top uppfærist ekki — gap næstu línu er reiknað frá línu fyrir númerið
            continue

        is_para_num_only = bool(_PARA_NUM_ONLY.match(line_text))
        active_fonts = heading_fonts if not is_para_num_only else {}
        active_sizes = {} if is_para_num_only else heading_sizes
        prefix = _heading_prefix(dominant_size, dominant_font, active_sizes, active_fonts)

        is_numbered_para = bool(_NEW_PARA.match(line_text)) and not prefix
        start_new_block = is_big_gap or is_numbered_para or bool(prefix) or is_para_num_only

        if start_new_block:
            flush()
            if blocks:
                blocks.append("")  # auð lína á milli blokka

        if prefix:
            # Fyrirsögn — alltaf ein lína, án skáletur-merkinga.
            # _collapse_spaced_letters lagar "D Ó M S O R Ð:" → "DÓMSORÐ:"
            heading_text = _collapse_spaced_letters(line_text)
            if heading_text.strip():  # skip empty bold lines (PDF separators)
                blocks.append(f"{prefix}{heading_text}")
            pending_para_num = None
        else:
            body = _build_line_text(line_words) if use_inline_italic else line_text
            if pending_para_num is not None:
                body = f"{pending_para_num}. {body}"
                pending_para_num = None
            current.append(body)

        prev_top = top

    flush()
    return "\n".join(blocks)


def _heading_prefix(size: float, fontname: str, heading_sizes: dict, heading_fonts: dict) -> str:
    if size in heading_sizes:
        return heading_sizes[size]
    for font_sub, prefix in heading_fonts.items():
        if font_sub in fontname:
            return prefix
    return ""


def _collapse_spaced_letters(line_text: str) -> str:
    """Sameinar stafabilsbreytt fyrirsagnartext.

    Í sumum PDF-skjölum eru stafir í fyrirsögnum staðsettir með víðu millibili
    (letter-spacing), þannig að pdfplumber les hvern staf sem sérstakt "orð".
    Niðurstaðan er "D Ó M S O R Ð:" í stað "DÓMSORÐ:".

    Greining: ef ÖLLUM tókum hefur í mesta lagi einn stafrænn staf (plús mögulega
    greinarmerki eins og ":"), og eru ≥ 3 tókar, þá er textinn sameinaður án bila.
    Þetta kemur í veg fyrir að "A og B" (lögmæt fyrirsögn) sé brotlægt sett saman
    (þar sem "og" hefur tvo stafræna stafi).
    Gildir eingöngu um fyrirsagnarlínur (þar sem prefix er sett).
    """
    tokens = line_text.split()
    if len(tokens) >= 3 and all(sum(c.isalpha() for c in t) <= 1 for t in tokens):
        return "".join(tokens)
    return line_text


def _build_line_text(line_words: list) -> str:
    """Sameinar orð í línu og pakkar skáletur-keyrslum í *...*."""
    if not line_words:
        return ""
    groups: list[tuple[bool, list[str]]] = []
    for w in line_words:
        is_italic = "Italic" in w.get("fontname", "") or "italic" in w.get("fontname", "")
        if groups and groups[-1][0] == is_italic:
            groups[-1][1].append(w["text"])
        else:
            groups.append((is_italic, [w["text"]]))
    parts = []
    for is_italic, words in groups:
        text = " ".join(words)
        parts.append(f"*{text}*" if is_italic else text)
    return " ".join(parts)


# ============================================================== TABLE EXTRACTION

_CELL_LEADING_NUM = re.compile(r"^\d{1,3}\s+")
_CELL_TRAILING_NUM = re.compile(r"\s*\d{1,3}$")


_NUMERIC_CELL_RE = re.compile(r"^[\d.,\s/\-]+$")


def _clean_table_cell(s: str | None) -> str:
    """Strippar PDF cell-númer fram/aftan af: '1 Mánuður 3' → 'Mánuður'.

    EKKI snerta cells sem eru hreinar tölur/dagsetningar (t.d. '6.000.000',
    '27.12.17', '4,13') — _CELL_TRAILING_NUM myndi sníða af þeim ranglega.
    """
    if not s:
        return ""
    s = s.strip()
    # Hreinar tölu/dagsetninga-cells → ekki stripa
    if _NUMERIC_CELL_RE.match(s):
        return s
    s = _CELL_LEADING_NUM.sub("", s)
    s = _CELL_TRAILING_NUM.sub("", s)
    return s.strip()


def _is_data_table(rows: list[list[str | None]]) -> bool:
    """Greinir hvort taflan inniheldur raunverulega data (ekki text flow).

    Tvö skilyrði sem aðgreina data-töflu frá lausum texta-flæði:
      (a) Multi-row: ≥ 2 dálkar með innihaldi í ≥ 3 röðum (sambland af texta)
      (b) Single/few-row: ≥ 5 dálkar OG ≥ 30% non-empty cells eru tölur
          (gripur 1-row gögn-töflur, e.g. fjárhags-yfirlit með einum aðila)
    """
    if not rows or not rows[0]:
        return False
    # Skilyrði (a) — multi-row check
    non_empty_per_col = [0] * len(rows[0])
    for row in rows:
        for i, cell in enumerate(row):
            if i >= len(non_empty_per_col):
                break
            if cell and cell.strip():
                non_empty_per_col[i] += 1
    if sum(1 for c in non_empty_per_col if c >= 3) >= 2:
        return True
    # Skilyrði (b) — single/few-row numeric check
    if len(rows[0]) >= 5:
        non_empty = sum(c for c in non_empty_per_col)
        if non_empty >= 3:
            numeric = sum(
                1 for row in rows for cell in row
                if cell and cell.strip() and any(ch.isdigit() for ch in cell)
            )
            if numeric / non_empty >= 0.3:
                return True
    return False


def _table_to_markdown(rows: list[list[str | None]]) -> str:
    """Skapar markdown töflu úr extracted rows. Hreinsar cell-númer artifacts."""
    cleaned = [[_clean_table_cell(c) for c in row] for row in rows]
    # Sleppa tómum röðum
    cleaned = [r for r in cleaned if any(c.strip() for c in r)]
    if not cleaned:
        return ""

    # Reyna að sameina multi-line haus (ef tvær efstu raðir eru stuttar)
    # ATH: bara mergea ef fyrsta röð er ekki data-row (þ.e. inniheldur engar
    # töluhringa-cells eins og '6.000.000' eða dagsetningu).
    def _looks_like_data_row(row: list[str]) -> bool:
        numeric_cells = sum(1 for c in row if c and _NUMERIC_CELL_RE.match(c.strip()))
        return numeric_cells >= 2
    merged: list[list[str]] = []
    i = 0
    while i < len(cleaned):
        cur = cleaned[i][:]
        if i < 2 and i + 1 < len(cleaned) and not _looks_like_data_row(cur):
            nxt = cleaned[i + 1]
            if (all(len(c) < 30 for c in nxt) and any(c.strip() for c in nxt)
                    and not _looks_like_data_row(nxt)):
                cur = [
                    (a + " " + b).strip() if b.strip() else a
                    for a, b in zip(cur, nxt)
                ]
                i += 1
        merged.append(cur)
        i += 1

    if not merged:
        return ""

    n_cols = len(merged[0])
    lines = []
    lines.append("| " + " | ".join(merged[0]) + " |")
    lines.append("|" + "|".join(["---"] * n_cols) + "|")
    for row in merged[1:]:
        # Pad röð ef hún hefur færri dálka
        padded = list(row) + [""] * (n_cols - len(row))
        lines.append("| " + " | ".join(padded[:n_cols]) + " |")
    return "\n".join(lines)


def _extract_with_tables(page, heading_sizes: dict, heading_fonts: dict) -> str:
    """Extracts text frá síðu og setur markdown töflur inn á sviðinu þar
    sem þær birtast.

    Aðferð:
      1. Finna töflur með find_tables (lines + text strategy)
      2. Sía út false-positive (texta sem fékkst sem 1-dálkstöflu)
      3. Extracta texta utan við töflu-svæði venjulega
      4. Setja markdown töflu inn í texta á viðeigandi stað (eftir y-pos)
    """
    tables = page.find_tables(table_settings={
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
    })

    # Sía út data-tables og halda y-position
    data_tables: list[tuple[float, str]] = []
    table_bboxes: list[tuple[float, float, float, float]] = []
    for tbl in tables:
        rows = tbl.extract()
        if not _is_data_table(rows):
            continue
        md = _table_to_markdown(rows)
        if md:
            data_tables.append((tbl.bbox[1], md))  # y0 = top
            table_bboxes.append(tbl.bbox)

    # Crop-a út töflu-svæði frá síðunni og extracta texta utan við
    if table_bboxes:
        # Skoða einungis texta sem er fyrir ofan EINU sinni töflu efst eða
        # neðan við þá síðustu — eða milli þeirra.
        page_h = page.height
        regions = []
        last_y = 0
        for bbox in sorted(table_bboxes, key=lambda b: b[1]):
            x0, y0, x1, y1 = bbox
            if y0 > last_y:
                regions.append((last_y, y0))
            last_y = y1
        if last_y < page_h:
            regions.append((last_y, page_h))

        text_parts = []
        for y0, y1 in regions:
            sub = page.crop((0, y0, page.width, y1))
            if heading_sizes or heading_fonts:
                t = _extract_with_headings(sub, heading_sizes, heading_fonts)
            else:
                t = sub.extract_text(x_tolerance=2, y_tolerance=2)
            if t and t.strip():
                text_parts.append(t.strip())

        # Tengja saman: byrja á efra-text, setja töflu(r) á réttri stöðu
        out_parts: list[str] = []
        for i, (region_text) in enumerate(text_parts):
            out_parts.append(region_text)
            if i < len(data_tables):
                out_parts.append(data_tables[i][1])
        return "\n\n".join(out_parts)

    # Engin tafla → venjuleg extraction
    if heading_sizes or heading_fonts:
        return _extract_with_headings(page, heading_sizes, heading_fonts)
    return page.extract_text(x_tolerance=2, y_tolerance=2) or ""


def _most_common_size(words: list) -> float:
    from collections import Counter
    sizes = Counter(round(w["size"], 1) for w in words)
    return sizes.most_common(1)[0][0] if sizes else 10.5


# ── Footnotes ─────────────────────────────────────────────────────────────────
# A PDF footnote is three separate things on the page: a superscript digit in the
# running text, a matching digit in the note block at the foot of the page, and
# the note's own text. page.extract_text() reads the note block column-first, so
# the digits arrive detached from their texts ("1\n2\n3\n4" then four sentences)
# and the pairing is unrecoverable afterwards. Rebuilding from word positions
# keeps each digit on the same line as its text, which is what makes this work.

_FOOTNOTE_START = re.compile(r'^(\d{1,3})\s+(.+)$')
# How much smaller than body text a footnote must be before we treat it as one.
_FOOTNOTE_SIZE_DELTA = 0.5
# Superscript markers are smaller still — this separates them from note bodies.
_SUPERSCRIPT_SIZE_DELTA = 1.5
# A "note block" covering more than this share of the page means the size test
# misfired — treat the page as ordinary body text rather than delete most of it.
_MAX_FOOTNOTE_LINE_SHARE = 0.5

# Footnote numbering is only document-wide in a single-author work. Collections —
# festschrifts, journal issues — restart at 1 for every contribution, so the same
# marker occurs in many unrelated places and a document-wide [^n] link would send
# the reader to the wrong note. When markers repeat far more than they should,
# emitting no footnote markup is better than emitting wrong markup.
_MIN_UNIQUE_MARKER_RATIO = 0.5


# A superscript marker sits a few points above the baseline of the line it
# annotates, so grouping words by exact `top` strands it on a line of its own —
# and a lone marker can no longer be told apart from body text. Cluster instead,
# with a tolerance well under normal line spacing (~10–14pt in these documents).
_LINE_CLUSTER_TOLERANCE = 5.0


def _page_lines(page) -> tuple[dict, list, float]:
    """Group a page's words into lines, tolerating raised superscripts.

    Returns (line_map keyed by the line's first `top`, ordered keys, body size).
    """
    words = page.extract_words(
        x_tolerance=2, y_tolerance=2, extra_attrs=["size", "fontname"],
    )
    if not words:
        return {}, [], 0.0

    line_map: dict[float, list] = defaultdict(list)
    keys: list[float] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        # Attach to the open line whose top is within tolerance; else start one.
        if keys and w["top"] - keys[-1] <= _LINE_CLUSTER_TOLERANCE:
            line_map[keys[-1]].append(w)
        else:
            keys.append(w["top"])
            line_map[w["top"]].append(w)
    return line_map, keys, _most_common_size(words)


# A line set this much larger than body text is a heading.
_HEADING_SIZE_DELTA = 1.0
# ...but only if it stands alone. Body size is measured per page, so a page whose
# dominant type is the note block — or an article opening set in larger type —
# makes ordinary prose look oversized. A length cap does not separate them: at
# line level a wrapped sentence is about as long as a heading. What does separate
# them is that a heading is one or two lines, while mis-sized body text runs on.
_MAX_HEADING_LINES = 2
# A vertical gap this much larger than the page's usual line spacing starts a new
# paragraph. Without this every page collapses into one unreadable block: PDF line
# breaks are not paragraph breaks, and markdown joins single newlines.
_PARAGRAPH_GAP_FACTOR = 1.5


def _extract_page_with_footnotes(page, *, footnotes: bool = True) -> tuple[str, list[tuple[int, str]]]:
    """Return (body markdown, [(n, note text), ...]) for one page.

    Kept separate from the heading/table extractors: those serve ~88,000 court
    documents whose layout has nothing to do with academic footnotes, and there is
    no reason to put that corpus at risk for this.

    With `footnotes=False` the note block is left in the body untouched — used for
    documents whose numbering restarts per contribution, which still need the
    paragraph and heading structure this function reconstructs.
    """
    line_map, line_tops, body_size = _page_lines(page)
    if not line_tops:
        return "", []

    note_start = (
        _footnote_block_start(line_map, line_tops, body_size, page.height)
        if footnotes else None
    )

    gaps = [line_tops[i] - line_tops[i - 1] for i in range(1, len(line_tops))]
    typical_gap = sorted(gaps)[len(gaps) // 2] if gaps else 14.0

    heading_idx = _heading_line_indexes(line_map, line_tops, body_size, note_start)

    blocks: list[str] = []       # finished paragraphs / headings
    current: list[str] = []      # lines of the paragraph being built
    note_lines: list[tuple[float, str]] = []  # (x0, text)
    prev_top: float | None = None

    def flush() -> None:
        if current:
            blocks.append(" ".join(current))
            current.clear()

    for idx, top in enumerate(line_tops):
        line_words = sorted(line_map[top], key=lambda w: w["x0"])
        sizes = [round(w["size"], 1) for w in line_words]

        if note_start is not None and idx >= note_start:
            note_lines.append((line_words[0]["x0"], " ".join(w["text"] for w in line_words)))
            continue

        # Body line: a digit set markedly smaller than the surrounding text is a
        # footnote reference, not a word — emit it as a GFM footnote marker.
        parts: list[str] = []
        for w in line_words:
            small = round(w["size"], 1) < body_size - _SUPERSCRIPT_SIZE_DELTA
            if (
                footnotes and small and w["text"].isdigit()
                and max(sizes) >= body_size - _FOOTNOTE_SIZE_DELTA
            ):
                # Attach to the preceding word so the marker sits tight against
                # the text it annotates, the way it does in the original.
                if parts:
                    parts[-1] = parts[-1] + f"[^{int(w['text'])}]"
                else:
                    parts.append(f"[^{int(w['text'])}]")
            else:
                parts.append(w["text"])
        line_text = " ".join(parts).strip()
        if not line_text:
            prev_top = top
            continue

        is_heading = idx in heading_idx
        started_paragraph = (
            prev_top is not None and (top - prev_top) >= typical_gap * _PARAGRAPH_GAP_FACTOR
        )

        if is_heading:
            flush()
            blocks.append(f"## {_collapse_spaced_letters(line_text)}")
        else:
            if started_paragraph:
                flush()
            current.append(line_text)
        prev_top = top

    flush()
    notes, leftovers = _parse_footnote_lines(note_lines)
    if leftovers:
        # Continuation of a note that began on an earlier page — keep the text.
        blocks.append(" ".join(leftovers))
    return "\n\n".join(blocks), notes


def _heading_line_indexes(
    line_map: dict, line_tops: list, body_size: float, note_start: int | None
) -> set[int]:
    """Indexes of lines that are genuinely headings.

    Oversized type alone is not enough: when a page's dominant size is misread,
    whole paragraphs come out "oversized". Only runs of one or two consecutive
    oversized lines are headings; a longer run is body text.
    """
    limit = note_start if note_start is not None else len(line_tops)
    big = [
        idx for idx in range(limit)
        if _most_common_size(line_map[line_tops[idx]]) >= body_size + _HEADING_SIZE_DELTA
    ]
    headings: set[int] = set()
    run: list[int] = []
    for idx in big:
        if run and idx == run[-1] + 1:
            run.append(idx)
        else:
            if 0 < len(run) <= _MAX_HEADING_LINES:
                headings.update(run)
            run = [idx]
    if 0 < len(run) <= _MAX_HEADING_LINES:
        headings.update(run)
    return headings


def _footnote_block_start(
    line_map: dict, line_tops: list, body_size: float, page_height: float
) -> int | None:
    """Index of the first line of the note block, or None if the page has none.

    Footnotes are the run of small-type lines the page *ends* with. Selecting on
    size alone would also catch block quotations, which Icelandic legal writing
    routinely sets a point or two smaller than body text — and those sit in the
    middle of the page with body text after them. Scanning up from the bottom and
    stopping at the first body-size line keeps quotations where they belong.
    """
    idx = len(line_tops) - 1
    seen_note = False
    while idx >= 0:
        line_words = line_map[line_tops[idx]]
        text = " ".join(w["text"] for w in sorted(line_words, key=lambda w: w["x0"])).strip()
        # A page number sits below or above the notes and belongs to neither.
        if text.isdigit():
            idx -= 1
            continue
        if _most_common_size(line_words) < body_size - _FOOTNOTE_SIZE_DELTA:
            seen_note = True
            idx -= 1
            continue
        break

    if not seen_note:
        return None
    start = idx + 1
    # A note block occupying most of the page means the size test misread the
    # page (a wholly small-type page, e.g. an appendix); treat none of it as notes.
    if start < len(line_tops) * _MAX_FOOTNOTE_LINE_SHARE:
        return None
    return start


def _parse_footnote_lines(
    note_lines: list[tuple[float, str]],
) -> tuple[list[tuple[int, str]], list[str]]:
    """Turn the foot-of-page block into ([(number, text)], leftover lines).

    A note begins on a line that starts with its number at the block's left
    margin; anything else — an indented line, or one that doesn't start with a
    number — continues the note above it. A line that is only a number is the
    page number, not a note.

    Lines that belong to a note which began on the *previous* page have no number
    here. They are returned as leftovers rather than discarded: silently dropping
    them cost one 682-page collection 12% of its text, and pushed many theses
    below the length check that guards this pass.
    """
    if not note_lines:
        return [], []
    margin = min(x0 for x0, _ in note_lines)

    notes: list[tuple[int, list[str]]] = []
    leftovers: list[str] = []
    for x0, text in note_lines:
        text = text.strip()
        if not text or text.isdigit():
            continue  # bare page number
        m = _FOOTNOTE_START.match(text)
        if m is not None and x0 <= margin + 2.0:
            notes.append((int(m.group(1)), [m.group(2).strip()]))
        elif notes:
            notes[-1][1].append(text)
        else:
            leftovers.append(text)
    return [(n, " ".join(parts).strip()) for n, parts in notes], leftovers


def _render_footnotes(notes: list[tuple[int, str]], body: str) -> str:
    """Render collected notes, keeping any whose marker we failed to find.

    remark-gfm silently drops a definition that nothing references, so an
    undetected superscript would delete the note's text from the document
    entirely. Anything unreferenced is therefore emitted as a plain list instead
    of a footnote definition — visibly imperfect, but never lost.
    """
    if not notes:
        return ""
    seen: dict[int, str] = {}
    for n, text in notes:
        if n not in seen and text:
            seen[n] = text
    if not seen:
        return ""

    referenced = {int(m) for m in re.findall(r'\[\^(\d+)\](?!:)', body)}
    linked = [n for n in sorted(seen) if n in referenced]
    orphans = [n for n in sorted(seen) if n not in referenced]

    out: list[str] = []
    if linked:
        out.append("\n\n".join(f"[^{n}]: {seen[n]}" for n in linked))
    if orphans:
        out.append(
            "### Neðanmálsgreinar án tilvísunar\n\n"
            + "\n".join(f"{n}. {seen[n]}" for n in orphans)
        )
    return "\n\n".join(out)


# ── Scanned-page detection ──────────────────────────────────────────────────────
# A scanned page is a photograph of paper with a text layer baked on top by
# whatever OCR the scanner ran — and that layer has been found to be corrupted in
# practice (garbled roman numerals, mis-encoded letters in the TOC) even though
# `parse_pdf`/`extract_words` return plenty of *text*, just not trustworthy text.
# An empty-text check misses this entirely: the page is not empty, it is wrong.
# A native PDF places its text directly on the page; even one that also embeds
# photos or diagrams never covers the *whole* page with a single image the way a
# scan does. That full-page-image signature is what this checks for instead.
_SCAN_IMAGE_COVERAGE = 0.9


def _sample_page_indices(n: int, sample_pages: int) -> list[int]:
    """Indexes spread across a document of `n` pages, always including the last.

    Shared by every sampling check in this module — sampling only the front
    would miss a book whose cover happens to be the one clean page.
    """
    if n == 0:
        return []
    step = max(1, n // sample_pages)
    return sorted({min(i, n - 1) for i in range(0, n, step)} | {n - 1})


def _page_has_full_page_image(page, threshold: float = _SCAN_IMAGE_COVERAGE) -> bool:
    """True if any image on `page` covers most of the page area."""
    area = page.width * page.height
    if area <= 0:
        return False
    for img in page.images:
        img_area = max(0.0, img["x1"] - img["x0"]) * max(0.0, img["bottom"] - img["top"])
        if img_area / area >= threshold:
            return True
    return False


def is_scanned_pdf(pdf_bytes: bytes, sample_pages: int = 5, threshold: float = _SCAN_IMAGE_COVERAGE) -> bool:
    """True when the PDF should be routed to OCR instead of the structured parser.

    Samples pages spread across the whole document — not just the front, which
    is often a clean cover even in an otherwise-scanned book — and checks each
    for a full-page image. One is enough: a book is scanned page-for-page or not
    at all, so a single hit settles it without opening every page.

    A PDF too broken for pdfplumber to even open is left to the caller's normal
    parse_pdf/OCR fallback chain rather than raised here — this is a routing
    decision, not the place to surface a corrupt file.
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            idx = _sample_page_indices(len(pdf.pages), sample_pages)
            return any(_page_has_full_page_image(pdf.pages[i], threshold) for i in idx)
    except Exception:
        return False


# ── Scan resolution normalization ────────────────────────────────────────────
# A 600 DPI scan costs real disk space and a slower browser PDF viewer for no
# benefit — nothing about a screen, or even OCR, needs more than ~300 DPI. Only
# ever worth calling on a PDF is_scanned_pdf() already confirmed: rasterizing a
# native PDF's selectable text at any DPI is a straight quality loss for nothing.
_TARGET_SCAN_DPI = 300
_DOWNSAMPLE_ABOVE_DPI = 400  # leave a scan already close to target alone
_JPEG_QUALITY = 80  # visually indistinguishable on a scanned text page, ~25x smaller than raw


def _page_image_dpi(page) -> float | None:
    """Effective DPI of a page's largest embedded image, or None if it has none
    or pdfplumber couldn't read the source pixel size."""
    images = page.images
    if not images:
        return None
    img = max(images, key=lambda im: (im["x1"] - im["x0"]) * (im["bottom"] - im["top"]))
    width_pt = img["x1"] - img["x0"]
    width_px = img.get("srcsize", (None, None))[0]
    if width_pt <= 0 or not width_px:
        return None
    return width_px / (width_pt / 72)


def downsample_if_high_res(
    pdf_bytes: bytes,
    target_dpi: int = _TARGET_SCAN_DPI,
    threshold_dpi: int = _DOWNSAMPLE_ABOVE_DPI,
    sample_pages: int = 5,
) -> bytes:
    """Re-render a scanned PDF at target_dpi when its scan resolution is well
    above what a screen needs. Returns the input unchanged when the resolution
    already looks reasonable (or on any failure) — safe to call unconditionally
    on every PDF already classified as scanned.

    Physical page size is preserved (only the backing image resolution drops),
    so this doesn't touch layout — the PDF viewer renders it identically, just
    from a smaller file.
    """
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            idx = _sample_page_indices(len(pdf.pages), sample_pages)
            dpis = [d for i in idx if (d := _page_image_dpi(pdf.pages[i])) is not None]
    except Exception:
        return pdf_bytes
    if not dpis or max(dpis) <= threshold_dpi:
        return pdf_bytes

    try:
        import fitz  # PyMuPDF

        src = fitz.open(stream=pdf_bytes, filetype="pdf")
        out = fitz.open()
        try:
            for page in src:
                pix = page.get_pixmap(dpi=target_dpi)
                # insert_image(pixmap=...) embeds the raster essentially
                # uncompressed — on a real 600 DPI book that produced a *larger*
                # file than the JPEG-compressed original despite 4x fewer
                # pixels. Re-encoding as JPEG first is what the pixel-count
                # reduction actually needs to show up as a smaller file.
                new_page = out.new_page(width=page.rect.width, height=page.rect.height)
                new_page.insert_image(new_page.rect, stream=pix.tobytes("jpg", jpg_quality=_JPEG_QUALITY))
            result = out.tobytes(garbage=4, deflate=True)
        finally:
            out.close()
            src.close()
    except Exception:
        return pdf_bytes

    # A source scanned with an efficient bilevel codec (CCITT/JBIG2 — common
    # for black-and-white text scans) can be *smaller* at its original DPI
    # than our RGB-JPEG re-render at a lower one: one real book went from
    # 18 MB to 322 MB this way. The DPI estimate alone can't predict this —
    # only comparing the actual output can, so always do that before trusting
    # the "optimization".
    return result if len(result) < len(pdf_bytes) else pdf_bytes


def docling_ocr_pdf(pdf_bytes: bytes, timeout: int = 300) -> str | None:
    """OCR an image-based PDF using Docling + Tesseract (Icelandic).

    Used for PDFs whose text layer is garbled due to font encoding issues
    (e.g. Úrskurðarnefnd fjarskipta- og póstmála documents), or for long
    scanned books where the default 300 s is too short (pass a larger
    `timeout` — e.g. `scripts/import_baekur.py` uses 1800).
    Returns plain text with markdown formatting stripped, or None on failure.
    """
    import pathlib
    import re
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = pathlib.Path(tmpdir) / "input.pdf"
        pdf_path.write_bytes(pdf_bytes)

        try:
            subprocess.run(
                [
                    "uvx", "docling", str(pdf_path),
                    "--to", "md",
                    "--force-ocr",
                    "--ocr-engine", "tesseract",
                    "--ocr-lang", "isl",
                    # Without this, Docling embeds every figure/page-picture as a
                    # base64 data URI right in the markdown — one scanned page
                    # blew this up to 3.5 MB and failed the insert outright
                    # (Postgres caps tsvector input at 1 MB). We already have the
                    # PDF for visual fidelity; body_text only needs the text.
                    "--image-export-mode", "placeholder",
                    "--output", tmpdir,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
            return None

        md_path = pathlib.Path(tmpdir) / "input.md"
        if not md_path.exists():
            return None

        md_text = md_path.read_text()
        # Belt-and-suspenders: strip any embedded image data URI regardless of
        # source (a stray one elsewhere in the pipeline must never reach the DB).
        text = re.sub(r"!\[[^\]]*\]\(data:image/[^)]*\)", "", md_text)
        # Strip markdown heading markers, keep content
        text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
        # Strip bullet list markers
        text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
        return text.strip() or None
