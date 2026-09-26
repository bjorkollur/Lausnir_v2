"""Generic importer for stjornarradid.is committee rulings.

Two fetch paths:

* **default (no ``--cid``)** — the current site.  stjornarradid.is was rebuilt
  as a Blazor Server app in 2026, so the listing is rendered in Chromium via
  Playwright at ``?Verkefni=<committee display_name>&index=<page>`` and each
  detail page is rendered the same way.  external_id is the detail URL slug.
* **``--cid``** — the pre-2026 ``$LisasticSearch`` endpoint + BeautifulSoup.
  That endpoint now 404s for every source; kept only until the three
  cid-filtered committees (afryjunarnefnd_haskoli, kaeruna_utlend, urvel) are
  ported to the new site.

Usage::

    uv run python3 scripts/import_stjornarradid.py --source mannanafnanefnd --new-only

Adding a new committee:
    1. Add SourceConfig entry in engine/config/sources.py.
    2. Register ``_extract_stjornarradid`` in _EXTRACTORS for the short_name.
    3. Run this script with the new --source value.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.parse
import uuid
from typing import Any

from bs4 import BeautifulSoup, NavigableString
from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright
from sqlalchemy import func, null as sa_null, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

import engine.database.connection as _db_conn
from engine.config.sources import SourceConfig, get_config
from engine.database.connection import init_db
from engine.database.models import Document, Source
from engine.processors.extractor import Extractor
from engine.processors.http_utils import get_with_retry, make_client
from engine.processors.renderer import (
    unique_verdict_filename,
    verdict_filename,
    write_markdown,
)
from engine.processors.validator import validate

log = logging.getLogger(__name__)

_SEARCH_URL = (
    "https://www.stjornarradid.is/gogn/urskurdir-og-alit-/"
    "$LisasticSearch/Search/"
)
_DETAIL_BASE = "https://www.stjornarradid.is"
_REQUEST_DELAY = 1.0  # polite delay between requests


# ─── HTML → Markdown conversion ───────────────────────────────────────────────

# Spaced uppercase: "Ú R S K U R Ð I" — signals the verdict section boundary
_SPACED_UPPER_RE = re.compile(
    r"^[A-ZÁÉÍÓÚÝÞÆÖÐА-ЯÄÖ](\s+[A-ZÁÉÍÓÚÝÞÆÖÐА-ЯÄÖ])+$"
)
# Roman numeral section heading: "I.", "II.", "III.", …
_ROMAN_RE = re.compile(r"^[IVX]+\.$")
# Single letter sub-label: "A.", "B.", "C.", …
_LETTER_RE = re.compile(r"^[A-Z]\.$")
# Icelandic date anywhere in text: "23. nóvember 2025"
_IS_DATE_RE = re.compile(
    r"\b(\d{1,2})\.\s*(janúar|febrúar|mars|apríl|maí|júní|júlí|ágúst|"
    r"september|október|nóvember|desember)\s+(\d{4})\b",
    re.IGNORECASE,
)
# "Ár 2025, 23. nóvember, lauk …"
_AR_DATE_RE = re.compile(
    r"Ár\s+(\d{4}),\s+(\d{1,2})\.\s+"
    r"(janúar|febrúar|mars|apríl|maí|júní|júlí|ágúst|"
    r"september|október|nóvember|desember)",
    re.IGNORECASE,
)
# Slash-separated case number: "2/2024"
_CASE_NR_RE = re.compile(r"(\d+/\d{4})")
# Preferred over _CASE_NR_RE when text has "mál/máli/málið nr." — avoids matching
# law references. Also captures alphanumeric formats like "IRR12030163" used by
# innanr_utl. The vowel ending is optional: "Mál nr. X" (nominative, used as a
# document heading — e.g. urvel's "Mál nr. 126/2026") has no trailing i/í, unlike
# "í máli nr. X" / "málið nr. X" (dative/definite, used in running prose).
_CASE_NR_SPECIFIC_RE = re.compile(
    r"m[aá]l(?:[ií])?(?:ð)?\s+nr\.?\s*([A-Za-z]{2,4}\d{6,}|\d+/\d{4})", re.IGNORECASE
)


def _to_md_text(elem) -> str:
    """Recursively convert an element's content to markdown, preserving italic/bold."""
    parts: list[str] = []
    for child in elem.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif child.name in ("em", "i"):
            inner = _to_md_text(child).strip()
            parts.append(f"*{inner}*" if inner else "")
        elif child.name in ("strong", "b"):
            inner = _to_md_text(child).strip()
            parts.append(f"**{inner}**" if inner else "")
        else:
            parts.append(_to_md_text(child))
    return "".join(parts)


def _section_to_markdown(section) -> str:
    """Convert ``section.single-news__content`` HTML to clean markdown.

    Skips preamble paragraphs (centered text before the spaced-uppercase
    verdict-type marker), then converts the remaining content to markdown,
    preserving Roman-numeral section headings, sub-headings, lists, and inline
    italic/bold.
    """
    lines: list[str] = []
    seen_verdict_marker = False
    prev_was_roman = False

    for child in section.children:
        if isinstance(child, NavigableString):
            continue
        tag = child.name
        if not tag:
            continue

        if tag == "p":
            style = child.get("style", "")
            is_centered = "text-align: center" in style
            strong = child.find("strong")
            strong_text = strong.get_text(strip=True) if strong else ""

            if is_centered and strong_text:
                if _SPACED_UPPER_RE.match(strong_text):
                    # Collapse "Ú R S K U R Ð I" → "ÚRSKURÐI"
                    seen_verdict_marker = True
                    lines.append(re.sub(r"\s+", "", strong_text))
                    lines.append("")
                    prev_was_roman = False
                elif not seen_verdict_marker:
                    continue  # preamble — skip
                elif _ROMAN_RE.match(strong_text):
                    lines.append(f"## {strong_text.rstrip('.')}")
                    prev_was_roman = True
                elif prev_was_roman:
                    # Section title immediately after Roman numeral → sub-heading
                    lines.append(f"### {strong_text}")
                    lines.append("")
                    prev_was_roman = False
                elif _LETTER_RE.match(strong_text):
                    # "A.", "B." labels — kept as plain text, not headings
                    lines.append(strong_text)
                    lines.append("")
                    prev_was_roman = False
                else:
                    lines.append(f"#### {strong_text}")
                    lines.append("")
                    prev_was_roman = False
            else:
                prev_was_roman = False
                if not seen_verdict_marker:
                    continue  # preamble — skip non-heading centered or plain paragraphs

                inner = _to_md_text(child).strip()
                if inner:
                    lines.append(inner)
                    lines.append("")

        elif tag in ("ol", "ul"):
            prev_was_roman = False
            if not seen_verdict_marker:
                continue
            for i, li in enumerate(child.find_all("li", recursive=False), 1):
                li_text = _to_md_text(li).strip()
                prefix = f"{i}." if tag == "ol" else "-"
                lines.append(f"{prefix} {li_text}")
            lines.append("")

    result = "\n".join(lines).strip()

    # Fallback for old-style cases with no spaced-uppercase verdict marker:
    # output all paragraph text without heading conversion.
    # Use find_all('p') recursively so content nested inside <div> wrappers
    # (e.g. landskjörstjórn format) is captured too.
    if not result:
        fallback: list[str] = []
        for p in section.find_all("p"):
            t = _to_md_text(p).strip()
            if t:
                fallback.append(t)
                fallback.append("")
        for lst in section.find_all(["ol", "ul"]):
            for i, li in enumerate(lst.find_all("li", recursive=False), 1):
                li_text = _to_md_text(li).strip()
                prefix = f"{i}." if lst.name == "ol" else "-"
                fallback.append(f"{prefix} {li_text}")
            fallback.append("")
        result = "\n".join(fallback).strip()

    return result


# ─── Preamble parser ──────────────────────────────────────────────────────────

def _clean_party_name(name: str) -> str:
    """Strip trailing list punctuation from a party name line.

    Handles "Fjársýslu ríkisins," and "Heilsugæslu höfuðborgarsvæðisins og".
    """
    name = name.rstrip(",").strip()
    name = re.sub(r"\s+og\s*$", "", name, flags=re.IGNORECASE).strip()
    return name


def _is_cname_variant(t_norm: str, cname_norm: str) -> bool:
    """Return True if t_norm looks like the committee name (possibly in genitive).

    Icelandic inflection changes word endings, so we compare the first 8
    characters of each word to handle e.g. "kærunefnd" vs "kærunefndar".

    A leading word-prefix counts too: the six "Úrskurðarnefnd velferðarmála -
    <svið>" configs share one committee that signs its rulings with just
    "Úrskurðarnefnd velferðarmála", so the page never spells out the full
    display_name.  Two words minimum, so a one-word party name can't be eaten.
    """
    if not cname_norm or not t_norm:
        return False
    if cname_norm == t_norm:
        return True
    cw = cname_norm.split()
    tw = t_norm.split()
    if len(tw) > len(cw) or (len(tw) < len(cw) and len(tw) < 2):
        return False
    return all(a[:8] == b[:8] for a, b in zip(cw, tw))


def _parse_preamble(
    section,
    committee_name: str = "",
) -> tuple[str | None, list[dict] | None, list[dict] | None]:
    """Extract case_number, plaintiffs, defendants from pre-verdict preamble.

    Returns (case_number_str, plaintiffs, defendants).  Any value may be None.
    """
    preamble: list[str] = []

    for child in section.children:
        if isinstance(child, NavigableString) or not getattr(child, "name", None):
            continue
        if child.name != "p":
            continue
        style = child.get("style", "")
        is_centered = "text-align: center" in style
        strong = child.find("strong")
        strong_text = strong.get_text(strip=True) if strong else ""
        t = child.get_text(strip=True)

        if is_centered:
            if child.find("br"):
                # <br/>-separated lines (e.g. kærunefnd útboðsmála format)
                raw_html = str(child)
                flat = re.sub(r"<br\s*/?>", "\n", raw_html)
                flat = re.sub(r"<[^>]+>", "", flat)
                lines = [l.strip() for l in flat.splitlines() if l.strip()]
                preamble.extend(lines)
            elif strong_text:
                if _SPACED_UPPER_RE.match(strong_text):
                    break  # spaced-uppercase verdict marker
                preamble.append(strong_text)
            elif t:
                preamble.append(t)
        elif not is_centered and t:
            break  # first non-centered body text — preamble is over

    # Fallback: if no centered preamble found, try first <p> with <br/> lines
    # (e.g. matsnefnd_eignarnam where entire case is in one non-centered <p>)
    if not preamble:
        for child in section.children:
            if not getattr(child, "name", None) or child.name != "p":
                continue
            if child.find("br"):
                raw_html = str(child)
                flat = re.sub(r"<br\s*/?>", "\n", raw_html)
                flat = re.sub(r"<[^>]+>", "", flat)
                lines = [l.strip() for l in flat.splitlines() if l.strip()]
                if any(l.lower() == "gegn" for l in lines[:10]):
                    for line in lines:
                        if line.lower().startswith("og kveðinn") or line.lower().startswith("kveðinn upp"):
                            break
                        if len(line) > 120:
                            break
                        preamble.append(line)
            break  # only check first <p>

    # Fallback: div-wrapped <p> elements (e.g. Félagsdómur, landskjör format).
    # Each preamble line is a separate <div><p>…</p></div>.
    # Collect short <p> texts until we hit a spaced-uppercase verdict word or a long paragraph.
    if not preamble:
        all_p_texts = [p.get_text(strip=True) for p in section.find_all("p")]
        if any(t.lower() == "gegn" for t in all_p_texts[:15]):
            for t in all_p_texts:
                t_norm = t.lower()
                if t_norm.startswith("kveðinn") or t_norm.startswith("og kveðinn"):
                    break
                if _SPACED_UPPER_RE.match(t.rstrip(":")):
                    break
                if len(t) > 120:
                    break
                preamble.append(t)

    return _parties_from_lines(preamble, committee_name)


_VERDICT_WORDS = {"úrskurður", "álit", "dómur", "niðurstaða", "dómsúrskurður"}


def _parties_from_lines(
    preamble: list[str],
    committee_name: str = "",
) -> tuple[str | None, list[dict] | None, list[dict] | None]:
    """Turn preamble lines into (case_number, plaintiffs, defendants).

    Shared by the old HTML path (which collects the lines from centered ``<p>``
    elements) and the Blazor path (which collects them from the rendered text
    around the "gegn" separator) — same committees, same conventions, so the
    cleaning rules live in one place.
    """
    # Find case number; strip committee heading, verdict-type words and date lines
    _cname_norm = " ".join(committee_name.lower().split())

    case_number_str: str | None = None
    cleaned: list[str] = []
    for t in preamble:
        t_norm = " ".join(t.lower().split())
        m = _CASE_NR_SPECIFIC_RE.search(t) or _CASE_NR_RE.search(t)
        if m and case_number_str is None:
            case_number_str = m.group(1)
        elif _cname_norm and _is_cname_variant(t_norm, _cname_norm):
            pass  # committee name heading (possibly genitive) — skip
        elif t_norm in _VERDICT_WORDS or (t_norm and t_norm.split()[0] in _VERDICT_WORDS):
            pass  # verdict-type heading (or "Úrskurður nefndarinnar ...") — skip
        elif t_norm.startswith("uppkveðinn") or t_norm.startswith("kveðinn") or t_norm.startswith("og kveðinn"):
            pass  # "rendered on" / "og kveðinn upp" date line — skip
        elif _IS_DATE_LOOSE_RE.search(t):
            # A date line that carried no case number: "Fimmtudaginn 6. febrúar
            # 2026" (úrskurðarnefnd velferðarmála) or "Ákvörðun kærunefndar
            # útboðsmála 14. júlí 2026".  No party name contains a month + year.
            pass
        elif t_norm == "og":
            pass  # standalone connector — skip
        else:
            cleaned.append(t)

    # Split on "gegn"
    gegn_idx: int | None = None
    for i, t in enumerate(cleaned):
        if t.lower() == "gegn":
            gegn_idx = i
            break

    if gegn_idx is not None:
        plaintiff_names = [_clean_party_name(t) for t in cleaned[:gegn_idx] if t.lower() != "gegn"]
        defendant_names = [_clean_party_name(t) for t in cleaned[gegn_idx + 1:] if t.lower() != "gegn"]
    else:
        plaintiff_names = [_clean_party_name(t) for t in cleaned]
        defendant_names = []

    plaintiffs = [{"name": n, "lawyer": None} for n in plaintiff_names if n] or None
    defendants = [{"name": n, "lawyer": None} for n in defendant_names if n] or None
    return case_number_str, plaintiffs, defendants


# ─── Date extraction ──────────────────────────────────────────────────────────

def _extract_date_from_page(soup: BeautifulSoup, section_raw_text: str) -> str | None:
    """Return an Icelandic date string like '23. nóvember 2025', or None.

    ``section_raw_text`` is the raw (pre-markdown) text of the section element,
    so the "Ár YYYY, DD. month, lauk…" preamble line is still present.
    """
    # 1. Any <time> tag whose text looks like an Icelandic date
    for time_tag in soup.find_all("time"):
        text = time_tag.get_text(strip=True)
        if _IS_DATE_RE.search(text):
            return text

    # 2. "Ár YYYY, DD. month, lauk…" pattern in preamble (appears before ÚRSKURÐI)
    m = _AR_DATE_RE.search(section_raw_text[:400])
    if m:
        return f"{m.group(2)}. {m.group(3)} {m.group(1)}"

    # 3. First Icelandic date anywhere in section text
    m = _IS_DATE_RE.search(section_raw_text)
    if m:
        return f"{m.group(1)}. {m.group(2)} {m.group(3)}"

    return None


# ─── Case list fetcher ────────────────────────────────────────────────────────

def _parse_case_list(html: str) -> list[tuple[str, str, str]]:
    """Return list of (newsid, relative_href, description) from the committee list page.

    ``description`` is the text of the <p> sibling after the case link, e.g.
    "Lyfjaskírteini. Staðfest ákvörðun Sjúkratrygginga Íslands um..." — may be empty.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if "stakur-urskurdur" not in href:
            continue
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        newsid = (qs.get("newsid") or [""])[0]
        if newsid and newsid not in seen:
            seen.add(newsid)
            # Description is in a <div class="...abstract"> that follows the <h2> parent
            h2 = a.parent if a.parent and a.parent.name == "h2" else a
            abstract = h2.find_next_sibling("div")
            description = abstract.get_text(strip=True) if abstract else ""
            results.append((newsid, href, description))

    return results


# ─── PDF fallback ─────────────────────────────────────────────────────────────

async def _body_from_pdf(client, section) -> str | None:
    """If section contains only a PDF link, download and parse it."""
    a = section.find("a", href=lambda h: h and h.lower().endswith(".pdf"))
    if not a:
        return None
    href: str = a["href"]
    pdf_url = (_DETAIL_BASE + href) if href.startswith("/") else href
    try:
        resp = await client.get(pdf_url, follow_redirects=True, timeout=60)
        if resp.status_code != 200 or not resp.content:
            return None
        from engine.processors.pdf_parser import parse_pdf
        text = parse_pdf(resp.content)
        return text.strip() or None
    except Exception as exc:
        log.warning("PDF fetch/parse failed for %s: %s", pdf_url, exc)
        return None


# ─── Single-case parser ───────────────────────────────────────────────────────

async def _parse_case_page(
    html: str,
    newsid: str,
    url: str,
    cname: str,
    description: str = "",
    client=None,
) -> dict | None:
    """Parse one case page.  Returns raw dict suitable for DB storage, or None."""
    soup = BeautifulSoup(html, "html.parser")

    section = soup.select_one("section.single-news__content")
    if not section:
        log.warning("No section.single-news__content in %s", url)
        return None

    # Raw section text used for case number / date extraction (computed early so
    # it's available even for PDF-only cases where body_text comes from the PDF).
    section_raw_text = section.get_text(separator="\n", strip=True)

    body_text = _section_to_markdown(section)
    if not body_text and client:
        body_text = await _body_from_pdf(client, section)
        if body_text:
            log.info("    → body from PDF (%d chars)", len(body_text))
    if not body_text:
        log.warning("Empty body text for %s", url)
        return None

    case_number_str, plaintiffs_raw, defendants_raw = _parse_preamble(section, committee_name=cname)

    # Fallback: case number from body opening line, e.g. "... úrskurður nr. 154/2026 ..."
    if case_number_str is None:
        m = _CASE_NR_SPECIFIC_RE.search(section_raw_text) or _CASE_NR_RE.search(section_raw_text)
        if m:
            case_number_str = m.group(1)

    # Verdict type — look for spaced uppercase in the section (all <p> recursively)
    # Strip trailing ":" so "D Ó M U R:" is matched the same as "D Ó M U R".
    verdict_type_str: str | None = None
    for p in section.find_all("p"):
        strong = p.find("strong")
        if not strong:
            continue
        st = strong.get_text(strip=True).rstrip(":")
        if _SPACED_UPPER_RE.match(st):
            collapsed = re.sub(r"\s+", "", st)
            if collapsed.upper().startswith("DÓMSÚRSKURÐ"):
                verdict_type_str = "Dómsúrskurður"
            elif collapsed.upper().startswith("ÚRSKURÐ"):
                verdict_type_str = "Úrskurður"
            elif collapsed.upper().startswith("DÓM"):
                verdict_type_str = "Dómur"
            elif collapsed.upper().startswith("ÁLIT"):
                verdict_type_str = "Álit"
            break

    date_str = _extract_date_from_page(soup, section_raw_text) or ""

    # Extract keyword + summary — prefer explicit Lykilorð/Útdráttur on case page
    keywords_raw: list[str] = []
    summary: str | None = None
    for p in section.find_all("p"):
        strong = p.find("strong")
        if not strong:
            continue
        label = strong.get_text(strip=True).lower()
        em = p.find("em")
        em_text = em.get_text(strip=True) if em else ""
        if label == "lykilorð" and em_text:
            keywords_raw = [kw.strip() for kw in em_text.rstrip(".").split(".") if kw.strip()]
        elif label == "útdráttur" and em_text:
            summary = em_text

    # Fall back to list-page description if page had no structured sections
    if not keywords_raw and not summary and description:
        dot_idx = description.find(". ")
        if dot_idx > 0:
            keywords_raw = [description[:dot_idx].strip()]
            summary = description[dot_idx + 2:].strip() or None
        else:
            summary = description or None

    return {
        "newsid": newsid,
        "url": url,
        "cname": cname,
        "case_number_str": case_number_str,
        "verdict_type_str": verdict_type_str,
        "date_str": date_str,
        "plaintiffs_raw": [p["name"] for p in (plaintiffs_raw or [])],
        "defendants_raw": [d["name"] for d in (defendants_raw or [])],
        "body_text": body_text,
        "keywords_raw": keywords_raw,
        "summary": summary,
    }


# ─── Blazor site (Playwright) ─────────────────────────────────────────────────
#
# stjornarradid.is was rebuilt as a Blazor Server app in 2026: the old
# $LisasticSearch endpoint 404s and a plain GET returns only a ~6 kB loading
# shell, so listing *and* detail pages have to be rendered in a real browser.
# Detail URLs are slugs now — the old "?newsid=<uuid>" scheme is gone, so the
# slug becomes external_id (never collides with the stored 36-char UUIDs).
#
# Only the "?Verkefni=<committee>" path is ported; the --cid path above still
# uses the dead endpoint (out of scope, equally broken either way).

_LIST_BASE = "https://www.stjornarradid.is/gogn/urskurdir-og-alit-/"
_DETAIL_LINK_SEL = 'a[href*="stakur-urskurdur"]'
_H1_SEL = "main h1.title"  # only present once the Blazor circuit has rendered
_PW_NAV_TIMEOUT = 45_000
_PW_RENDER_TIMEOUT = 25_000

# Icelandic date with the day dot optional: "12. ágúst 2026" and "12 ágúst 2026"
_IS_DATE_LOOSE_RE = re.compile(
    r"\b(\d{1,2})\.?\s+(janúar|febrúar|mars|apríl|maí|júní|júlí|ágúst|"
    r"september|október|nóvember|desember)\s+(\d{4})\b",
    re.IGNORECASE,
)
# Page chrome that trails the ruling text on a detail page
_DETAIL_TRAILERS = {"hlusta", "til baka", "prenta", "deila", "/"}

# Bare agency case ids used as the whole listing title by innanr_utl ("IRR12030163")
# and innvida ("IRN26050091").  _CASE_NR_SPECIFIC_RE only finds these behind a
# "máli nr." prefix, which those listing entries do not have.
_AGENCY_CASE_NR_RE = re.compile(r"\b([A-Z]{2,4}\d{6,})\b")

# kaeruna_utlend states its own number as "úrskurður nr. N/YYYY", not "mál nr.";
# velferdar_raduneyti names itself in between ("Úrskurður velferðarráðuneytisins
# nr. 006/2018"), so allow one intervening word — the ministry's own name.
_URSKURDUR_NR_RE = re.compile(
    r"úrskurð(?:ur|i)\s+(?:\w+\s+)?nr\.?\s*(\d+/\d{4})", re.IGNORECASE
)

# umhverfi_raduneyti titles its own rulings "Mál 17050084 <description>" — a
# bare internal case id, no "nr.", no slash-year. Confirmed live: the site's
# own filter for this ministry returns exactly as many results (141) as the
# DB already has rows for this source, so nothing is missing — the historical
# import just couldn't recognise this id shape at all and fell back to
# whatever law citation appeared first in the body instead. Older rulings
# (2000-2001) use a 9-digit "YYYYNNNNN" variant of the same id shape, e.g.
# "Mál 199900452" — widen from 7-8 to 6-9 digits to cover both. A few
# 2010-2011 titles have "Titill" (Icelandic for "Title") literally in place
# of "Mál" — an unfilled template placeholder — but still carry the real
# case id right after it, e.g. "Titill 09120125".
_BARE_MAL_NR_RE = re.compile(r"\b(?:Mál|Titill)\s+(\d{6,9})\b")

# urnefnd_raforka switched to "í máli nr. 7. 2025" (period, not slash, before
# the year) for rulings from 2024 onward — older rulings from the same source
# still use "nr. 2/2023" and are already handled by _CASE_NR_SPECIFIC_RE.
_MAL_NR_DOT_YEAR_RE = re.compile(
    r"m[aá]l(?:[ií])?(?:ð)?\s+nr\.?\s*(\d{1,4})\.\s*((?:19|20)\d{2})\b", re.IGNORECASE
)


def _case_number_match(text: str) -> str | None:
    """Try every known "own case number" shape against `text`, normalised to N/YYYY."""
    m = (
        _CASE_NR_SPECIFIC_RE.search(text)
        or _AGENCY_CASE_NR_RE.search(text)
        or _BARE_MAL_NR_RE.search(text)
    )
    if m:
        return m.group(1)
    m = _MAL_NR_DOT_YEAR_RE.search(text)
    return f"{m.group(1)}/{m.group(2)}" if m else None

# Longest line still plausible as a party name; anything longer is ruling prose.
_PARTY_LINE_MAX = 120
# Spaced-out letters, either case: "Ú R S K U R Ð U R", "ú r s k u r ð u r :"
_SPACED_LETTERS_RE = re.compile(r"^\w(\s+\w)+$")


def _is_party_block_end(line: str) -> bool:
    """True for the boilerplate that closes a "<X> gegn <Y>" party block."""
    t = " ".join(line.replace("\xa0", " ").lower().split()).rstrip(":")
    return (
        bool(_SPACED_LETTERS_RE.match(t))
        or t.startswith(("kveðinn", "og kveðinn", "uppkveðinn", "svohljóðandi", "með svohljóðandi"))
        or t in ("lykilorð", "útdráttur")
    )


def _gegn_block(body_text: str) -> list[str]:
    """Lines of the "<plaintiff> gegn <defendant>" block, or [] if there is none.

    Bounded outward from the "gegn" separator rather than scanned from the top
    of the page, because the lead-in varies per committee — a long "Ár 2025, …,
    lauk áfrýjunarnefnd … málinu" sentence (afryjunarnefnd_haskoli), a
    "KÆRUNEFND HÚSAMÁLA / ÚRSKURÐUR" heading stack (knhus), or a bare weekday
    date line (urvel_*).  What they share is that the party lines sit adjacent
    to "gegn" and are short; ruling prose and the closing boilerplate are not.
    """
    lines = [ln.replace("\xa0", " ").strip() for ln in body_text.splitlines()]
    lines = [ln for ln in lines if ln]
    g = next((i for i, ln in enumerate(lines[:20]) if ln.lower() == "gegn"), None)
    if g is None:
        return []

    start, end = g, g + 1
    while start > 0 and len(lines[start - 1]) <= _PARTY_LINE_MAX and not _is_party_block_end(lines[start - 1]):
        start -= 1
    while end < len(lines) and len(lines[end]) <= _PARTY_LINE_MAX and not _is_party_block_end(lines[end]):
        end += 1
    return lines[start:end]


def _slug(href: str) -> str:
    """Last path segment of a detail URL — used as external_id."""
    return href.split("?")[0].rstrip("/").rsplit("/", 1)[-1]


def _case_number_from_title(title: str) -> str | None:
    """Pull "51/2026" out of "Mál nr. 51/2026 Úrskurður 12. ágúst 2026"."""
    found = _case_number_match(title)
    if found:
        return found
    # umhverfi_raduneyti's older (2004-2005) listing titles are the bare case
    # id with no "Mál" word at all, e.g. "03120125" — only trust this when the
    # *entire* line is nothing but digits, so a stray number inside ordinary
    # prose from another source is never mistaken for one.
    stripped = title.strip()
    if stripped.isdigit() and 6 <= len(stripped) <= 9:
        return stripped
    m = _CASE_NR_RE.search(title)
    return m.group(1) if m else None


def _blazor_list_entry(link_text: str) -> tuple[str | None, str]:
    """Split a listing link's text into (case_number, description).

    Link text is "<date>\\n<committee>\\n<title>\\n\\n<abstract>"; the abstract
    is everything after the first line that carries a case number.
    """
    lines = [line.strip() for line in link_text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        case_number = _case_number_from_title(line)
        if case_number:
            return case_number, " ".join(lines[i + 1:]).strip()
    return None, ""


def _parse_blazor_detail(
    slug: str,
    url: str,
    h1: str,
    main_text: str,
    config: SourceConfig,
    description: str = "",
) -> dict | None:
    """Parse a rendered detail page into the raw dict `_extract_stjornarradid` wants.

    ``main_text`` is ``page.inner_text("main")``: a date lead, "/", the h1, the
    ruling text, then the committee-name badge and page chrome.
    """
    lines = main_text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == h1.strip()), -1)
    lead = "\n".join(lines[:start]) if start >= 0 else ""
    body_lines = lines[start + 1:] if start >= 0 else list(lines)

    drop = _DETAIL_TRAILERS | {config.display_name.lower()}
    while body_lines and (not body_lines[-1].strip() or body_lines[-1].strip().lower() in drop):
        body_lines.pop()

    body_text = re.sub(r"\n{3,}", "\n\n", "\n".join(body_lines)).strip()
    if not body_text:
        log.warning("Empty body text for %s", url)
        return None

    # Only the h1 and the body's first line ("Mál nr. 51/2026   Eiginnafn: …")
    # are safe to read a case number from — the ruling text cites law numbers
    # in the same "nr. 45/1996" shape.  The body line additionally has to name
    # the case explicitly ("mál/máli nr. N") or be an agency id: sveitarstj_alit
    # opens on "…sveitarstjórnarlaga nr. 138/2011…", and reading a bare N/YYYY
    # there turned every álit into the same bogus "138/2011".
    #
    # The body wins when the two disagree: it's the signed ruling text quoting
    # its own case number (twice — the opening line and again in the "Hinn …
    # kveður … upp … í máli N/YYYY" sentence), while the h1/slug are page
    # metadata the site sets independently and can mislabel. Observed for real:
    # mál nr. 41/2026 ("Mollý") is titled/slugged "42/2026" on stjornarradid.is
    # but its own body says 41/2026 throughout — importing the h1's "42/2026"
    # would silently fork a third row for a case already in the DB as 41/2026.
    # Adversarial committees print "<plaintiff> gegn <defendant>" above the
    # ruling; the block also carries the case number for the committees whose
    # first body line is a heading rather than the case ("í máli nr. 167/2025").
    block_case_number: str | None = None
    plaintiffs = defendants = None
    if config.parse_parties != "none":
        block = _gegn_block(body_text)
        if block:
            block_case_number, plaintiffs, defendants = _parties_from_lines(
                block, config.display_name
            )
        else:
            log.warning("No 'gegn' party block found for %s", url)

    first_line = body_text.split("\n", 1)[0]
    body_case_number = _case_number_match(first_line)
    # Fallback: neither the first line nor the gegn block always carries the
    # case number — some committees (e.g. urvel: "Úrskurðarnefnd velferðarmála
    # \n Mál nr. 126/2026 \n Miðvikudaginn …") state it a couple of lines down,
    # before the gegn block even starts, and others (kaeruna_utlend) use
    # "úrskurður nr. N/YYYY" instead of "mál/máli nr.". Two real cases each hit
    # this and got silently mislabeled from the h1/slug instead — e.g. a body
    # opening "úrskurður nr. 825/2025" got stored as 888/2025 because a
    # different, correctly-labeled 888/2025 ruling's h1 leaked in. Search a
    # window (not the whole document — later sections cite unrelated law/case
    # numbers) rather than trusting the h1 unchecked.
    if not body_case_number:
        window = body_text[:400]
        m = _URSKURDUR_NR_RE.search(window)
        body_case_number = _case_number_match(window) or (m.group(1) if m else None)
    case_number_from_h1 = _case_number_from_title(h1)
    case_number_from_body = body_case_number or block_case_number
    if (
        case_number_from_h1
        and case_number_from_body
        and case_number_from_h1 != case_number_from_body
    ):
        log.warning(
            "Case number mismatch for %s: h1 says %r, body says %r — using body",
            url, case_number_from_h1, case_number_from_body,
        )
    case_number_str = case_number_from_body or case_number_from_h1
    # The date lead above the h1 ("28 ágúst 2026") is the publication date the
    # old <time>-tag path stored, so it keeps re-imports aligned with the rows
    # already in the DB.  Falling straight through to the body instead picks up
    # whatever date the prose happens to mention first — ferdathjod's newest
    # ruling led with "29. september 2021" on a page published 2024-12-11.
    m = (
        _IS_DATE_LOOSE_RE.search(lead)
        or _IS_DATE_LOOSE_RE.search(h1)
        or _IS_DATE_LOOSE_RE.search(body_text)
    )
    date_str = f"{m.group(1)}. {m.group(2)} {m.group(3)}" if m else ""
    verdict_type_str = next(
        (v for v in config.verdict_types_allowed if v.lower() in h1.lower()), None
    )

    return {
        "newsid": slug,
        "url": url,
        "cname": config.display_name,
        "case_number_str": case_number_str,
        "verdict_type_str": verdict_type_str,
        "date_str": date_str,
        "plaintiffs_raw": [p["name"] for p in (plaintiffs or [])],
        "defendants_raw": [d["name"] for d in (defendants or [])],
        "body_text": body_text,
        "keywords_raw": [],
        "summary": description or None,
    }


def _collect_blazor_listing(
    config: SourceConfig,
    known_case_numbers: set[str],
    new_only: bool,
    max_items: int | None = None,
) -> list[tuple[str, str, str]]:
    """Return newest-first [(slug, detail_url, description)] for unseen cases.

    With ``new_only`` we stop at the first listing page that contributes nothing
    new.  Page-level rather than item-level, so one out-of-order entry (the site
    sorts by date, not case number) cannot truncate the run.
    """
    base = f"{_LIST_BASE}?Verkefni={urllib.parse.quote(config.display_name)}"
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            index = 0
            while True:
                page.goto(f"{base}&index={index}", timeout=_PW_NAV_TIMEOUT)
                try:
                    page.wait_for_selector(_DETAIL_LINK_SEL, timeout=_PW_RENDER_TIMEOUT)
                except PWTimeout:
                    log.info("  page %d: no results — end of listing", index)
                    break

                listed = fresh = 0
                for a in page.query_selector_all(_DETAIL_LINK_SEL):
                    href = a.get_attribute("href") or ""
                    slug = _slug(href)
                    if not slug or slug in seen:
                        continue
                    seen.add(slug)
                    listed += 1
                    case_number, description = _blazor_list_entry(a.inner_text())
                    if not case_number:
                        # Pre-2005 entries titled "Mannanafnanefnd urskurdir …".
                        # Skipping keeps the case_number dedup honest — those are
                        # already in the DB under their old newsid external_id.
                        log.warning("  no case number in listing entry %s — skipping", slug)
                        continue
                    if case_number in known_case_numbers:
                        continue
                    fresh += 1
                    out.append((
                        slug,
                        href if href.startswith("http") else _DETAIL_BASE + href,
                        description,
                    ))

                log.info("  page %d: %d listed, %d new (%d total)", index, listed, fresh, len(out))
                if not listed or (new_only and not fresh):
                    break
                if max_items is not None and len(out) >= max_items:
                    break
                index += 1
                time.sleep(_REQUEST_DELAY)
        finally:
            browser.close()

    return out[:max_items] if max_items is not None else out


def _scrape_blazor_detail(
    slug: str, url: str, config: SourceConfig, description: str
) -> dict | None:
    """Render one detail page in Chromium and parse it.

    ponytail: a fresh browser per case (~3 s) mirrors import_fjarskiptastofa's
    _scrape_detail_standalone and keeps this thread-safe under asyncio.to_thread.
    Fine for incremental runs; hoist the browser into the caller if a full
    ~1800-case backfill is ever needed.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, timeout=_PW_NAV_TIMEOUT)
            page.wait_for_selector(_H1_SEL, timeout=_PW_RENDER_TIMEOUT)
            h1 = page.inner_text(_H1_SEL)
            main_text = page.inner_text("main")
        finally:
            browser.close()
    return _parse_blazor_detail(slug, url, h1, main_text, config, description)


# ─── DB helpers ───────────────────────────────────────────────────────────────

async def _ensure_source(session: AsyncSession, config: SourceConfig, base_url: str) -> uuid.UUID:
    result = await session.execute(
        select(Source).where(Source.short_name == config.short_name)
    )
    source = result.scalar_one_or_none()
    if source is None:
        source_id = uuid.uuid4()
        session.add(Source(
            id=source_id,
            short_name=config.short_name,
            display_name=config.display_name,
            base_url=base_url,
        ))
        await session.commit()
        return source_id
    return source.id


async def _upsert_doc(session: AsyncSession, doc: Document) -> None:
    def _v(val: Any) -> Any:
        return sa_null() if val is None else val

    values: dict[str, Any] = {
        "id": doc.id,
        "source_id": doc.source_id,
        "external_id": doc.external_id,
        "url": _v(doc.url),
        "raw_api_data": _v(doc.raw_api_data),
        "case_number": _v(doc.case_number),
        "document_date": _v(doc.document_date),
        "court": _v(doc.court),
        "verdict_type": _v(doc.verdict_type),
        "instance_tier": _v(doc.instance_tier),
        "plaintiffs": _v(doc.plaintiffs),
        "defendants": _v(doc.defendants),
        "keywords": _v(doc.keywords),
        "summary": _v(doc.summary),
        "body_text": _v(doc.body_text),
        "lower_body_text": _v(doc.lower_body_text),
        "validation_errors": _v(doc.validation_errors),
    }
    update_cols = {k: v for k, v in values.items() if k not in ("id", "source_id", "external_id")}
    update_cols["updated_at"] = func.now()
    await session.execute(
        pg_insert(Document)
        .values(**values)
        .on_conflict_do_update(constraint="uq_doc_source_external", set_=update_cols)
    )


def _render_and_save(
    doc: Document,
    config: SourceConfig,
    taken: set[str],
    existing_vf: dict[str, str] | None = None,
) -> str | None:
    # Reuse the existing verdict_filename on re-import so we overwrite in-place
    if existing_vf and doc.external_id in existing_vf:
        vf = existing_vf[doc.external_id]
    else:
        base = verdict_filename(doc, config)
        vf = unique_verdict_filename(base, taken)
        taken.add(vf)
    try:
        write_markdown(doc, config, vf=vf)
    except Exception as exc:
        log.warning("write_markdown failed for %s: %s", doc.external_id, exc)
        return None
    return vf


def _build_document(
    raw: dict,
    source_id: uuid.UUID,
    config: SourceConfig,
) -> Document:
    fields = Extractor(config).extract(raw)
    doc = Document(
        id=uuid.uuid4(),
        source_id=source_id,
        external_id=raw["newsid"],
        url=raw["url"],
        **fields,
    )
    errors = validate(doc, config)
    doc.validation_errors = errors or None
    return doc


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main(
    source_short_name: str,
    cid: str | None,
    dry_run: bool = False,
    limit: int | None = None,
    new_only: bool = False,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = get_config(source_short_name)
    if cid:
        list_url = f"https://www.stjornarradid.is/gogn/urskurdir-og-alit-/?cid={cid}"
    else:
        encoded = urllib.parse.quote(config.display_name)
        list_url = f"{_LIST_BASE}?Verkefni={encoded}"
    log.info("Source: %s  CID: %s", source_short_name, cid or "(none)")

    await init_db()
    async with _db_conn.AsyncSessionLocal() as session:
        source_id = await _ensure_source(session, config, base_url=list_url)

    async with _db_conn.AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Document.external_id, Document.case_number, Document.verdict_filename)
            .where(Document.source_id == source_id)
        )).all()
    # taken: all known filenames (for collision avoidance)
    # existing_vf: existing_id → filename (to reuse on re-import)
    # existing_ids: skip fetch+upsert entirely for already-imported docs
    # existing_case_numbers: the Blazor site dropped the newsid UUIDs, so
    #   "already imported?" has to be answered by case number there
    taken: set[str] = {r.verdict_filename for r in rows if r.verdict_filename}
    existing_vf: dict[str, str] = {r.external_id: r.verdict_filename for r in rows if r.verdict_filename}
    existing_ids: set[str] = {r.external_id for r in rows}
    existing_case_numbers: set[str] = {r.case_number for r in rows if r.case_number}

    if not cid:
        log.info(
            "Fetching case list via Playwright for committee: %s (%d already imported)",
            config.display_name,
            len(existing_ids),
        )
        cases = await asyncio.to_thread(
            _collect_blazor_listing, config, existing_case_numbers, new_only, limit
        )
        log.info("Found %d new cases", len(cases))

        imported = 0
        skipped = 0
        for slug, detail_url, description in cases:
            if slug in existing_ids:
                skipped += 1
                continue
            try:
                raw = await asyncio.to_thread(
                    _scrape_blazor_detail, slug, detail_url, config, description
                )
            except Exception as exc:
                log.warning("Fetch failed for %s: %s", slug, exc)
                skipped += 1
                continue
            if raw is None:
                log.warning("Parse failed for %s", slug)
                skipped += 1
                continue

            # The listing-page label can disagree with the detail page's own body
            # text (see the case-number-mismatch warning in _parse_blazor_detail);
            # the pre-fetch listing dedup above only sees the listing's label, so
            # re-check here against the body-derived case number before upserting
            # — otherwise a mislabeled case can fork a duplicate row.
            resolved_case_number = raw.get("case_number_str")
            if resolved_case_number and resolved_case_number in existing_case_numbers:
                log.info(
                    "  %s  case=%s already in DB under a different label — skipping",
                    slug, resolved_case_number,
                )
                skipped += 1
                continue

            log.info(
                "  %s  case=%s  date=%s  type=%s  body=%d chars  %s",
                slug,
                raw.get("case_number_str") or "?",
                raw.get("date_str") or "?",
                raw.get("verdict_type_str") or "?",
                len(raw.get("body_text") or ""),
                f"{raw['plaintiffs_raw']} gegn {raw['defendants_raw']}"
                if config.parse_parties != "none" else "",
            )

            if dry_run:
                continue

            doc = _build_document(raw, source_id, config)
            async with _db_conn.AsyncSessionLocal() as session:
                try:
                    await _upsert_doc(session, doc)
                    await session.commit()
                except Exception as exc:
                    log.error("Upsert failed for %s: %s", slug, exc)
                    await session.rollback()
                    skipped += 1
                    continue

            async with _db_conn.AsyncSessionLocal() as session:
                vf = _render_and_save(doc, config, taken, existing_vf)
                if vf:
                    await session.execute(
                        update(Document)
                        .where(
                            Document.source_id == doc.source_id,
                            Document.external_id == doc.external_id,
                        )
                        .values(verdict_filename=vf)
                    )
                await session.commit()

            imported += 1
            await asyncio.sleep(_REQUEST_DELAY)

        log.info("Done: %d imported, %d skipped", imported, skipped)
        return

    async with make_client() as client:
        log.info("Fetching case list for committee: %s", config.display_name)

        # Two-mode fetch strategy:
        # 1. Always start with no-pageSize: returns the "true" results for the
        #    committee. For many sources, pageSize breaks the Committee filter
        #    (returns 0 or a completely different set of results).
        # 2. If no-pageSize returns exactly 200 (server's apparent max), the source
        #    may have more cases. Check if pageSize pagination overlaps — if so,
        #    paginate to get the full count.
        all_cases: list[tuple[str, str, str]] = []
        seen_ids: set[str] = set()

        base_params_paged = {
            "SearchQuery": "",
            "Committee": config.display_name,
            "SortByDate": "True",
            "pageSize": "200",
        }
        base_params_nopaged = {
            "SearchQuery": "",
            "Committee": config.display_name,
            "SortByDate": "True",
        }

        # Step 1: fetch without pageSize
        resp = await client.get(_SEARCH_URL, params=base_params_nopaged, follow_redirects=True)
        resp.raise_for_status()
        nopaged_batch = _parse_case_list(resp.text)
        nopaged_ids = {n for n, _, _ in nopaged_batch}
        log.info("  No-pageSize fetch: %d cases", len(nopaged_batch))

        if len(nopaged_batch) < 200:
            # Got all cases in one shot (source has <200 or pageSize is broken)
            all_cases = nopaged_batch
            log.info("  Using no-pageSize results (%d cases)", len(all_cases))
        else:
            # Might be truncated — check if pageSize pagination works for this source
            resp = await client.get(
                _SEARCH_URL,
                params={**base_params_paged, "PageIndex": "1"},
                follow_redirects=True,
            )
            resp.raise_for_status()
            paged_page1 = _parse_case_list(resp.text)
            paged_ids_p1 = {n for n, _, _ in paged_page1}

            overlap = len(nopaged_ids & paged_ids_p1)
            if paged_page1 and overlap > 0:
                # Pagination works — use it to get all cases
                log.info("  Pagination works (overlap=%d) — paginating all pages", overlap)
                for n, h, d in paged_page1:
                    if n not in seen_ids:
                        seen_ids.add(n)
                        all_cases.append((n, h, d))
                log.info("  Page 1: %d items", len(paged_page1))
                page = 2
                while True:
                    resp = await client.get(
                        _SEARCH_URL,
                        params={**base_params_paged, "PageIndex": str(page)},
                        follow_redirects=True,
                    )
                    resp.raise_for_status()
                    batch = _parse_case_list(resp.text)
                    if not batch:
                        break
                    new = [(n, h, d) for n, h, d in batch if n not in seen_ids]
                    for n, h, d in new:
                        seen_ids.add(n)
                    all_cases.extend(new)
                    log.info("  Page %d: %d items, %d total", page, len(batch), len(all_cases))
                    page += 1
            elif paged_page1:
                # pageSize returns a non-overlapping set — the API splits results
                # into two disjoint groups depending on whether pageSize is set.
                # Combine both to get the full corpus (e.g. velferdar_raduneyti).
                log.info(
                    "  pageSize returns disjoint set (overlap=%d) — merging both sets", overlap
                )
                all_cases = list(nopaged_batch)
                seen_ids = nopaged_ids.copy()
                # Paginate the paged set fully (page 1 already fetched)
                for n, h, d in paged_page1:
                    if n not in seen_ids:
                        seen_ids.add(n)
                        all_cases.append((n, h, d))
                log.info("  Paged page 1: %d new, %d total", len(paged_page1), len(all_cases))
                page = 2
                while True:
                    resp = await client.get(
                        _SEARCH_URL,
                        params={**base_params_paged, "PageIndex": str(page)},
                        follow_redirects=True,
                    )
                    resp.raise_for_status()
                    batch = _parse_case_list(resp.text)
                    if not batch:
                        break
                    new = [(n, h, d) for n, h, d in batch if n not in seen_ids]
                    for n, h, d in new:
                        seen_ids.add(n)
                    all_cases.extend(new)
                    log.info("  Paged page %d: %d new, %d total", page, len(new), len(all_cases))
                    page += 1
            else:
                # pageSize returns nothing — no-pageSize results are all we have
                log.info("  pageSize broken (overlap=%d, paged=%d) — using no-pageSize results", overlap, len(paged_page1))
                all_cases = nopaged_batch

        cases = all_cases
        if limit:
            cases = cases[:limit]
        log.info("Found %d cases total", len(all_cases))

        imported = 0
        skipped = 0

        for newsid, href, description in cases:
            if newsid in existing_ids:
                skipped += 1
                continue

            # Build full detail URL
            if href.startswith("http"):
                detail_url = href
            else:
                detail_url = _DETAIL_BASE + href

            # Add cname if missing
            parsed = urllib.parse.urlparse(detail_url)
            qs = urllib.parse.parse_qs(parsed.query)
            if "cname" not in qs:
                encoded_name = urllib.parse.quote(config.display_name)
                detail_url = f"{detail_url}&cname={encoded_name}"

            await asyncio.sleep(_REQUEST_DELAY)

            try:
                resp = await get_with_retry(client, detail_url)
            except Exception as exc:
                log.warning("Fetch failed for %s: %s", newsid, exc)
                skipped += 1
                continue

            if resp.status_code != 200:
                log.warning("HTTP %d for %s", resp.status_code, newsid)
                skipped += 1
                continue

            raw = await _parse_case_page(resp.text, newsid, detail_url, config.display_name, description, client=client)
            if raw is None:
                log.warning("Parse failed for %s", newsid)
                skipped += 1
                continue

            log.info(
                "  %s  case=%s  date=%s  type=%s  kw=%s",
                newsid[:8],
                raw.get("case_number_str") or "?",
                raw.get("date_str") or "?",
                raw.get("verdict_type_str") or "?",
                ",".join(raw.get("keywords_raw") or []) or "—",
            )

            if dry_run:
                continue

            doc = _build_document(raw, source_id, config)

            async with _db_conn.AsyncSessionLocal() as session:
                try:
                    await _upsert_doc(session, doc)
                    await session.commit()
                except Exception as exc:
                    log.error("Upsert failed for %s: %s", newsid, exc)
                    await session.rollback()
                    skipped += 1
                    continue

            async with _db_conn.AsyncSessionLocal() as session:
                vf = _render_and_save(doc, config, taken, existing_vf)
                if vf:
                    await session.execute(
                        update(Document)
                        .where(
                            Document.source_id == doc.source_id,
                            Document.external_id == doc.external_id,
                        )
                        .values(verdict_filename=vf)
                    )
                await session.commit()

            imported += 1

    log.info("Done: %d imported, %d skipped", imported, skipped)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Import stjornarradid.is committee rulings")
    parser.add_argument("--source", required=True, help="Source short_name (e.g. afryjunarnefnd_haskoli)")
    parser.add_argument("--cid", default=None, help="Committee UUID from stjornarradid.is (optional)")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, do not write to DB")
    parser.add_argument("--limit", type=int, default=None, help="Stop after N cases (for testing)")
    parser.add_argument(
        "--new-only",
        action="store_true",
        help="Stop paginating at the first listing page with no new cases",
    )
    args = parser.parse_args()

    asyncio.run(main(
        args.source, args.cid, dry_run=args.dry_run, limit=args.limit, new_only=args.new_only
    ))
