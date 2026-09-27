"""Paraphrase audit for golden query set entries (Task 10b).

    set -a; . ./.env; set +a
    uv run python scripts/golden_audit.py /tmp/lausnir-dev/golden500/candidates.yaml
    uv run python scripts/golden_audit.py tests/golden/queries.yaml --max-run 2

For each entry, lemmatises both `question` and its source summary (BÍN,
`engine.processors.lemmatizer`), strips Icelandic stopwords/function words,
and finds the longest run of consecutive content-word lemmas that appears as
a consecutive run in BOTH sequences (classic longest-common-substring over
token sequences, via DP on lemma equality).

An entry is a *violation* when that run is strictly greater than --max-run
(default 2): i.e. 3+ shared content words in a row is treated as a copied
clause rather than an unavoidable legal term of art (which is at most 1-2
words, e.g. "varanleg örorka").

The summary is read from a `summary` field if present, otherwise extracted
from a `note` field formatted as produced by scripts/golden_candidates.py:
"Reifun: <text> | Lykilorð: ... [| Málategund: ... | Ár: ...]" (the older
Task 10 format "Úr reifun: <text> | lykilorð: ..." is also accepted).

Ported from the uncommitted Task 10 fix-round-1 helper
(/tmp/lausnir-dev/audit_paraphrase.py); this is now the committed version.

CLI: prints every violating entry and exits 1 if any are found, 0 otherwise.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from engine.processors.lemmatizer import _lemmatize_word, _WORD_RE

STOPWORDS = {
    # conjunctions / prepositions / articles / pronouns / common aux verbs
    "og", "í", "á", "að", "er", "var", "með", "um", "til", "frá", "sem", "þar",
    "en", "eða", "ekki", "þess", "þau", "þeir", "þeirra", "hún", "hann", "það",
    "sig", "sér", "sína", "sinni", "sitt", "fyrir", "milli", "undir", "yfir",
    "við", "af", "upp", "niður", "inn", "út", "þá", "nú", "svo", "því", "enda",
    "heldur", "aðeins", "einnig", "þó", "hvort", "hvers", "hvaða", "allir",
    "allt", "hafði", "hefði", "hefur", "höfðu", "verið", "væri", "yrði", "vera",
    "hafa", "gera", "gert", "gerði", "gerðu", "koma", "kom", "kæmi", "komið",
    "taka", "tók", "tóku", "telja", "taldi", "talið", "töldu", "vísa", "vísað",
    "vísaði", "vísar", "samkvæmt", "grundvelli", "meðal", "annars", "fram",
    "beri", "ber", "bæri", "sk", "skuli", "skyldi", "þessi", "þessa", "þessu",
    "þessum", "hinn", "hin", "hið", "hinni", "hinum", "annar", "önnur", "annað",
    "báðir", "bæði", "einn", "eitt", "ein", "aðili", "aðila", "aðilar", "aðilum",
    "einnig", "hann", "hana", "honum", "hennar", "einu", "sinn", "hvaða", "þann",
    "þeim", "gagn", "gögn", "hafi", "hafði", "geta", "getur", "gæti", "leiða",
    "leiddi", "leiði", "liggja", "lá", "lægi", "lagt",
    # extra prepositions / conjunctions / generic boilerplate nouns that show
    # up in nearly every summary and aren't distinguishing content
    "vegna", "án", "auk", "gegn", "eftir", "áður", "meðan", "þótt", "þrátt",
    "sbr", "skv", "tiltekinn", "tiltekin", "tiltekið", "tiltekinnar",
    "umræddur", "umrædd", "umrætt", "framangreindur", "framangreind",
    "framangreint", "nefndur", "nefnd", "nefnt", "nefndu", "mál", "dómur",
    "úrskurður", "krafa", "kröfur", "grein", "lög", "l", "hf", "ehf", "svf",
    "hins", "vegar", "annarrar", "hinsvegar",
    # lemma forms BÍN returns for some of the above function words
    "veginn", "auka", "þrár",
}

NOTE_RE = re.compile(r"(?:Reifun|Úr reifun):\s*(.*?)\s*\|\s*(?:Lykilorð|lykilorð):", re.DOTALL)


def content_lemmas(text: str) -> list[str]:
    """Tokenise Icelandic text, drop stopwords/short words, lemmatise via BÍN."""
    if not text:
        return []
    tokens = _WORD_RE.findall(text)
    out = []
    for t in tokens:
        low = t.lower()
        if low in STOPWORDS:
            continue
        lemma = _lemmatize_word(t).lower()
        if lemma in STOPWORDS or len(lemma) <= 2:
            continue
        out.append(lemma)
    return out


def longest_common_run(a: list[str], b: list[str]) -> tuple[int, list[str]]:
    """Longest run of lemmas that appears consecutively in both a and b."""
    best_len, best_seq = 0, []
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
                if dp[i][j] > best_len:
                    best_len = dp[i][j]
                    best_seq = a[i - best_len:i]
    return best_len, best_seq


def longest_shared_run(question: str, summary: str) -> int:
    """Longest run of content-word lemmas shared between question and summary.

    Stopwords are filtered before comparison. An empty question or summary
    yields 0.
    """
    if not question or not summary:
        return 0
    run_len, _ = longest_common_run(content_lemmas(question), content_lemmas(summary))
    return run_len


def extract_summary(entry: dict) -> str:
    """Pull the summary text out of an entry: a `summary` field wins, else
    it's parsed out of a `note` field (golden_candidates.py's format)."""
    if entry.get("summary"):
        return entry["summary"]
    note = entry.get("note") or ""
    m = NOTE_RE.match(note)
    return m.group(1) if m else note


def audit_entries(entries: list[dict], max_run: int = 2) -> list[dict]:
    """Return the entries whose question/summary share a run > max_run.

    Each returned dict adds `run` (int) and `lemmas` (the offending run).
    """
    offenders = []
    for e in entries:
        question = e.get("question") or ""
        summary = extract_summary(e)
        run_len, run_seq = longest_common_run(content_lemmas(question), content_lemmas(summary))
        if run_len > max_run:
            offenders.append({**e, "run": run_len, "lemmas": run_seq})
    return offenders


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("yaml_path", type=Path, help="YAML file with question + summary/note per entry")
    ap.add_argument("--max-run", type=int, default=2)
    args = ap.parse_args(argv)

    entries = yaml.safe_load(args.yaml_path.read_text(encoding="utf-8")) or []
    offenders = audit_entries(entries, max_run=args.max_run)

    print(f"Checked {len(entries)} entries. Offenders (run > {args.max_run}): {len(offenders)}\n")
    for o in offenders:
        print(f"{o.get('id', '?')}  run={o['run']}  lemmas={o['lemmas']}")
        print(f"      question: {o.get('question', '')}")
        print()

    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main())
