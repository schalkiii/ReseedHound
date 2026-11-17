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
            await db.execute("""
                CREATE TABLE IF NOT EXISTS file_cache (
                    path TEXT PRIMARY KEY,
                    mtime INTEGER NOT NULL,
                    size INTEGER NOT NULL,
                    info_hash TEXT NOT NULL,
                    pieces_hash TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    torrent_name TEXT,
                    total_size INTEGER,
                    announce TEXT,
                    announce_list TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS dead_torrents (
                    site_name TEXT NOT NULL,
                    torrent_id INTEGER NOT NULL,
                    first_dead_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_dead_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (site_name, torrent_id)
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_dead_first
                ON dead_torrents(first_dead_at)
            """)

            # 历史库里可能存在同一 (pieces_hash, site_name, torrent_id) 的多条记录。
            # 首次升级时先去重再建唯一索引；索引已存在时跳过清理，避免每轮启动全表扫描。
            cursor = await db.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='index' AND name='uq_reseed_history'"
            )
            if not await cursor.fetchone():
                await db.execute("""
                    DELETE FROM reseed_history WHERE id NOT IN (
                        SELECT MIN(id) FROM reseed_history
                        GROUP BY pieces_hash, site_name, torrent_id
                    )
                """)
                await db.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_reseed_history
                    ON reseed_history(pieces_hash, site_name, torrent_id)
                """)
            await db.commit()

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

    async def lookup_file_cache(self, keys: list[tuple[str, int, int]]) -> dict:
        """批量查询文件级解析缓存（path + mtime + size 一致才命中）。

        命中可跳过「读文件 + bencode 解析 + 算 pieces_hash」，与 Rust 版同口径。
        """
        import json as _json

        out: dict = {}
        async with self._lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                for batch_start in range(0, len(keys), 900):
                    batch = keys[batch_start : batch_start + 900]
                    identity = {p: (m, s) for p, m, s in batch}
                    placeholders = ",".join("?" for _ in batch)
                    sql = (
                        "SELECT path, mtime, size, info_hash, pieces_hash, file_name,"
                        " torrent_name, total_size, announce, announce_list"
                        f" FROM file_cache WHERE path IN ({placeholders})"
                    )
                    async with db.execute(sql, [p for p, _, _ in batch]) as cur:
                        rows = await cur.fetchall()
                    for (path, mtime, size, info_hash, pieces_hash, file_name,
                         torrent_name, total_size, announce, announce_list) in rows:
                        if identity.get(path) != (mtime, size):
                            continue
                        item = {
                            "info_hash": info_hash,
                            "pieces_hash": pieces_hash,
                            "file_name": file_name,
                            "file_path": path,
                        }
                        if torrent_name is not None:
                            item["torrent_name"] = torrent_name
                        if total_size is not None:
                            item["total_size"] = total_size
                        if announce is not None:
                            item["announce"] = announce
                        if announce_list:
                            try:
                                item["announce_list"] = _json.loads(announce_list)
                            except ValueError:
                                pass
                        out[path] = item
        return out

    async def store_file_cache(self, entries: list[tuple[str, int, int, dict]]) -> None:
        """写入/更新文件级解析缓存（INSERT OR REPLACE）。"""
        import json as _json

        rows = []
        for path, mtime, size, t in entries:
            rows.append((
                path,
                mtime,
                size,
                t.get("info_hash", ""),
                t.get("pieces_hash", ""),
                t.get("file_name", ""),
                t.get("torrent_name"),
                t.get("total_size"),
                t.get("announce"),
                _json.dumps(t["announce_list"]) if t.get("announce_list") else None,
            ))
        async with self._lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.executemany(
                    "INSERT OR REPLACE INTO file_cache "
                    "(path, mtime, size, info_hash, pieces_hash, file_name,"
                    " torrent_name, total_size, announce, announce_list)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    rows,
                )
                await db.commit()

    async def prune_file_cache(self, keep: set[str]) -> int:
        """裁剪 keep 集合之外的陈旧行（对应文件已删除）。"""
        async with self._lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                async with db.execute("SELECT path FROM file_cache") as cur:
                    stale = [r[0] for r in await cur.fetchall() if r[0] not in keep]
                for start in range(0, len(stale), 900):
                    batch = stale[start : start + 900]
                    placeholders = ",".join("?" for _ in batch)
                    await db.execute(
                        f"DELETE FROM file_cache WHERE path IN ({placeholders})", batch
                    )
                await db.commit()
        return len(stale)

    async def add_reseed_record(self, pieces_hash: str, site_name: str, torrent_id: int):
        async with self._lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                # 唯一索引保证同一组合只记录一次，重复命中不写库
                await db.execute(
                    "INSERT OR IGNORE INTO reseed_history"
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

    async def load_persistent_dead(self, min_days: int = 15) -> set[tuple[str, int]]:
        """返回持续死亡达到 min_days 天的 (site, torrent_id) 集合，用于硬跳过。"""
        result: set[tuple[str, int]] = set()
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                "SELECT site_name, torrent_id FROM dead_torrents "
                "WHERE julianday('now') - julianday(first_dead_at) >= ?",
                (min_days,),
            )
            for site, tid in await cursor.fetchall():
                result.add((site, int(tid)))
        return result

    async def record_dead(self, site_name: str, torrent_id: int):
        """记录一次死种探测；首次写入 first_dead_at，后续仅刷新 last_dead_at。"""
        async with self._lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    "INSERT INTO dead_torrents "
                    "(site_name, torrent_id, first_dead_at, last_dead_at) "
                    "VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                    "ON CONFLICT(site_name, torrent_id) DO UPDATE "
                    "SET last_dead_at = CURRENT_TIMESTAMP",
                    (site_name, torrent_id),
                )
                await db.commit()

    async def clear_dead_if_alive(self, site_name: str, torrent_id: int):
        """某 (站点, 种子) 可成功下载时，清除其死种持久化记录。"""
        async with self._lock:
            async with aiosqlite.connect(str(self._db_path)) as db:
                await db.execute(
                    "DELETE FROM dead_torrents WHERE site_name = ? AND torrent_id = ?",
                    (site_name, torrent_id),
                )
                await db.commit()
