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
    """
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        pages = []
        for i, page in enumerate(pdf.pages):
            apply_header = header_pt and (i > 0 or not skip_header_on_first)
            if apply_header or footer_pt:
                y0 = header_pt if apply_header else 0
                y1 = page.height - footer_pt if footer_pt else page.height
                page = page.crop((0, y0, page.width, y1))

            if extract_tables:
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

    return _join_pages(pages)


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
