"""Add provisions JSONB column to documents table."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text
from engine.database.connection import init_db, get_engine

async def main():
    await init_db()
    engine = await get_engine()
    async with engine.connect() as conn:
        await conn.execute(text(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS provisions JSONB"
        ))
        await conn.commit()
    print("Done. provisions column added.")

if __name__ == "__main__":
    asyncio.run(main())
