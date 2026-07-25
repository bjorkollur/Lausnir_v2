"""One-off: merge mangled (Latin-1 decimal encoded) sources into their correct counterparts.

Each mangled source was created when an import script derived short_name from
Icelandic text, encoding each non-ASCII byte as its decimal value (á→225, ð→240…).
For each such source we:
  1. Find the correct target source by short_name
  2. Reassign documents whose external_id does NOT already exist in the target
  3. Delete duplicate documents (external_id already in target)
  4. Delete the mangled Source row
  5. Delete the mangled folder from disk (markdown + raw if present)
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from sqlalchemy import delete, select, update

import engine.database.connection as c
from engine.config.sources import MARKDOWN_DIR, RAW_DIR
from engine.database.connection import init_db
from engine.database.models import Document, Source

# mangled_short_name → correct_short_name
MAPPING: dict[str, str] = {
    "k230runefnd_h250sam225la":                          "knhus",
    "218rskur240arnefnd_velfer240arm225la_grei240slua24": "urvel_greid",
    # "innvi240ar225240uneyti240": ???  — 12 skjöl, cname='innviðaráðuneytið', court='Ú.Sr.', dagsetning 2018+
    # innvida er söguleg (–2012); þarf nýja heimild eða merge við innvida
    "f233lags_og_h250sn230240ism225lar225240uneyti240":   "felag_hus_raduneyti",
    "218rskur240arnefnd_um_uppl253singam225l":             "urnefnd_uppl",
    # "218rskur240ir_225_m225lefnasvi240i_innvi240ar22524": ???  — 7 skjöl, cname='Úrskurðir á málefnasviði innviðaráðuneytisins', court='Ú.Sr.', 2018–2019
    # innvida er söguleg (–2012); þarf nýja heimild
    "218rskur240arnefnd_velfer240arm225la_f230240ingar_":  "urvel_faed",
    "k230runefnd_250tlendingam225la":                      "kaeruna_utlend",
    "k230runefnd_250tbo240sm225la":                        "kaeruna_utbod",
    # "atvinnuvegar225240uneyti240": ???  — 2 skjöl, court='Ú.Sr.', cname='Atvinnuvegaráðuneytið'
    # Engin samsvörun í sources.py — þarf að ákveða handvirkt
    "218rskur240ir_f233lags_og_h250sn230240ism225lar225":  "felag_hus_raduneyti",
    "umhverfis_orku_og_loftslagsr225240uneyti240":         "umhverfi_raduneyti",
    "stj243rns253sluk230rur_250rskur240ir":                "stjornsyslu_kaerur",
}


async def main() -> None:
    await init_db()
    async with c.AsyncSessionLocal() as s:
        for mangled, target_name in MAPPING.items():
            # Load source rows
            src_bad = (await s.execute(select(Source).where(Source.short_name == mangled))).scalar_one_or_none()
            if src_bad is None:
                print(f"SKIP {mangled!r} — not in DB")
                continue

            src_good = (await s.execute(select(Source).where(Source.short_name == target_name))).scalar_one_or_none()
            if src_good is None:
                print(f"WARN {mangled!r} → target {target_name!r} not found, skipping")
                continue

            # Get all documents in mangled source
            bad_docs = (await s.execute(
                select(Document).where(Document.source_id == src_bad.id)
            )).scalars().all()

            # Get external_ids already in target
            good_eids = set((await s.execute(
                select(Document.external_id).where(Document.source_id == src_good.id)
            )).scalars().all())

            moved = dupes = 0
            for d in bad_docs:
                if d.external_id in good_eids:
                    # Duplicate — delete
                    await s.execute(delete(Document).where(Document.id == d.id))
                    dupes += 1
                else:
                    # Unique — reassign to correct source
                    await s.execute(
                        update(Document)
                        .where(Document.id == d.id)
                        .values(source_id=src_good.id)
                    )
                    moved += 1

            # Delete mangled Source row
            await s.execute(delete(Source).where(Source.id == src_bad.id))
            await s.commit()

            print(f"{mangled!r} → {target_name!r}: moved={moved} dupes_deleted={dupes}")

            # Delete folders from disk
            for base in (MARKDOWN_DIR, RAW_DIR):
                folder = Path(base) / mangled
                if folder.exists():
                    shutil.rmtree(folder)
                    print(f"  deleted {folder}")

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
