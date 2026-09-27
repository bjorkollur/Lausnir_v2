"""Dump candidate documents for the golden set as a YAML skeleton.

    set -a; . ./.env; set +a
    uv run python scripts/golden_candidates.py > /tmp/lausnir-dev/golden_candidates.yaml

Picks Hæstiréttur/Landsréttur documents with a substantial summary, spread over
years, with their appeal-chain neighbours as extra expected answers. The
'question' field is left empty — it is written by hand (paraphrased, not copied).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from sqlalchemy import text

import engine.database.connection as c


async def main(n: int = 80) -> None:
    await c.init_db()
    async with c.AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            WITH cand AS (
              SELECT d.id, s.short_name, d.case_number, d.document_date, d.summary, d.keywords,
                     row_number() OVER (PARTITION BY s.short_name, extract(year FROM d.document_date)
                                        ORDER BY random()) AS rn
              FROM documents d JOIN sources s ON s.id = d.source_id
              WHERE s.short_name IN ('haestirettur', 'landsrettur')
                AND length(d.summary) BETWEEN 300 AND 1500
                AND d.document_date >= '2010-01-01')
            SELECT * FROM cand WHERE rn <= 3 ORDER BY random() LIMIT :n"""), {"n": n})).mappings().all()
        out = []
        for i, r in enumerate(rows, 1):
            linked = (await s.execute(text("""
                SELECT os.short_name, o.case_number, o.document_date
                FROM document_links dl JOIN documents o ON o.id = dl.to_doc_id
                JOIN sources os ON os.id = o.source_id
                WHERE dl.from_doc_id = :id AND dl.relation IN ('appealed_to', 'appealed_from')"""),
                {"id": r["id"]})).all()
            out.append({
                "id": f"g{i:03d}",
                "question": "",
                "expected": [{"source": r["short_name"], "case_number": r["case_number"],
                              "document_date": str(r["document_date"])}]
                            + [{"source": a, "case_number": b, "document_date": str(d)} for a, b, d in linked],
                "scope": ["domstolar"],
                "note": f"Úr reifun: {r['summary'][:400]} | lykilorð: {', '.join(r['keywords'] or [])}",
            })
    print(yaml.safe_dump(out, allow_unicode=True, sort_keys=False, width=120))


if __name__ == "__main__":
    asyncio.run(main())
