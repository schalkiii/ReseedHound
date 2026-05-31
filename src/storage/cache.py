import asyncio
from pathlib import Path

import aiosqlite


class TorrentCache:
    def __init__(self, db_path: str = "data/cache.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    async def init(self):
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS pieces_cache (
                    pieces_hash TEXT PRIMARY KEY,
                    info_hash TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS reseed_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pieces_hash TEXT NOT NULL,
                    site_name TEXT NOT NULL,
                    torrent_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_pieces_hash
                ON pieces_cache(pieces_hash)
            """)
            await db.commit()

    async def is_cached(self, pieces_hash: str) -> bool:
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                "SELECT 1 FROM pieces_cache WHERE pieces_hash = ?",
                (pieces_hash,),
            )
            row = await cursor.fetchone()
            return row is not None

    async def filter_uncached(self, items: list[dict]) -> list[dict]:
        result = []
        batch_size = 500
        async with aiosqlite.connect(str(self._db_path)) as db:
            for i in range(0, len(items), batch_size):
                batch = items[i:i + batch_size]
                hashes = [item["pieces_hash"] for item in batch]
                placeholders = ",".join("?" for _ in hashes)
                cursor = await db.execute(
                    f"SELECT pieces_hash FROM pieces_cache WHERE pieces_hash IN ({placeholders})",
                    hashes,
                )
                cached_set = {row[0] for row in await cursor.fetchall()}
                for item in batch:
                    if item["pieces_hash"] not in cached_set:
                        result.append(item)
        return result

    async def add_batch(self, entries: list[dict]):
        async with self._lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.executemany(
                    "INSERT OR IGNORE INTO pieces_cache"
                    " (pieces_hash, info_hash, file_name) VALUES (?, ?, ?)",
                    [(e["pieces_hash"], e["info_hash"], e["file_name"]) for e in entries],
                )
                await db.commit()

    async def get_reseeded_combos(
        self, combos: list[tuple[str, str, int]],
    ) -> set[tuple[str, str, int]]:
        result = set()
        if not combos:
            return result
        async with aiosqlite.connect(str(self._db_path)) as db:
            for i in range(0, len(combos), 500):
                batch = combos[i:i + 500]
                placeholders = ",".join("(?, ?, ?)" for _ in batch)
                params = []
                for ph, sn, tid in batch:
                    params.extend([ph, sn, tid])
                cursor = await db.execute(
                    f"SELECT pieces_hash, site_name, torrent_id FROM reseed_history "
                    f"WHERE (pieces_hash, site_name, torrent_id) IN ({placeholders})",
                    params,
                )
                rows = await cursor.fetchall()
                for ph, sn, tid in rows:
                    result.add((ph, sn, tid))
        return result

    async def add_reseed_record(self, pieces_hash: str, site_name: str, torrent_id: int):
        async with self._lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    "INSERT INTO reseed_history"
                    " (pieces_hash, site_name, torrent_id) VALUES (?, ?, ?)",
                    (pieces_hash, site_name, torrent_id),
                )
                await db.commit()

    async def get_reseeded_hashes_by_site(
        self, pieces_hashes: list[str],
    ) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        if not pieces_hashes:
            return result
        async with aiosqlite.connect(str(self._db_path)) as db:
            for i in range(0, len(pieces_hashes), 500):
                batch = pieces_hashes[i:i + 500]
                placeholders = ",".join("?" for _ in batch)
                cursor = await db.execute(
                    f"SELECT DISTINCT pieces_hash, site_name FROM reseed_history "
                    f"WHERE pieces_hash IN ({placeholders})",
                    batch,
                )
                rows = await cursor.fetchall()
                for ph, sn in rows:
                    result.setdefault(sn, set()).add(ph)
        return result

    async def get_stats(self) -> dict:
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM pieces_cache")
            cached = (await cursor.fetchone())[0]
            cursor = await db.execute("SELECT COUNT(*) FROM reseed_history")
            reseeded = (await cursor.fetchone())[0]
            return {"cached_torrents": cached, "reseed_count": reseeded}

    async def clear_cache(self):
        async with self._lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute("DELETE FROM pieces_cache")
                await db.commit()
