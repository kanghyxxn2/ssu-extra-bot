import aiosqlite
import hashlib
from datetime import datetime
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS programs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT,
    program_type TEXT,
    department TEXT,
    description TEXT,
    apply_start TEXT,
    apply_end TEXT,
    edu_start TEXT,
    edu_end TEXT,
    target TEXT,
    competency TEXT,
    method TEXT,
    mileage INTEGER DEFAULT 0,
    capacity INTEGER DEFAULT 0,
    applicants INTEGER DEFAULT 0,
    status TEXT,
    detail_url TEXT,
    hash TEXT UNIQUE NOT NULL,
    scraped_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    username TEXT,
    notifications_enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    UNIQUE(user_id, category)
);

CREATE TABLE IF NOT EXISTS user_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    keyword TEXT NOT NULL,
    UNIQUE(user_id, keyword)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    sent_at TEXT NOT NULL,
    UNIQUE(user_id, program_id)
);
"""


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self):
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.commit()

    async def close(self):
        if self._conn:
            await self._conn.close()

    async def upsert_program(self, program: dict) -> Optional[int]:
        now = datetime.now().isoformat()
        program_hash = hashlib.md5(
            f"{program['title']}:{program.get('detail_url', '')}".encode()
        ).hexdigest()

        cursor = await self._conn.execute(
            """INSERT INTO programs
               (title, category, program_type, department, description,
                apply_start, apply_end, edu_start, edu_end, target,
                competency, method, mileage, capacity, applicants,
                status, detail_url, hash, scraped_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(hash) DO UPDATE SET
                   title=excluded.title, category=excluded.category,
                   program_type=excluded.program_type, department=excluded.department,
                   description=excluded.description,
                   apply_start=excluded.apply_start, apply_end=excluded.apply_end,
                   edu_start=excluded.edu_start, edu_end=excluded.edu_end,
                   target=excluded.target, competency=excluded.competency,
                   method=excluded.method, mileage=excluded.mileage,
                   capacity=excluded.capacity, applicants=excluded.applicants,
                   status=excluded.status, updated_at=excluded.updated_at
            """,
            (
                program["title"], program.get("category"), program.get("program_type"),
                program.get("department"), program.get("description"),
                program.get("apply_start"), program.get("apply_end"),
                program.get("edu_start"), program.get("edu_end"),
                program.get("target"), program.get("competency"),
                program.get("method"), program.get("mileage", 0),
                program.get("capacity", 0), program.get("applicants", 0),
                program.get("status"), program.get("detail_url"),
                program_hash, now, now,
            ),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_programs(self, status: str = None, category: str = None,
                           limit: int = 20, offset: int = 0):
        query = "SELECT * FROM programs WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY scraped_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cursor = await self._conn.execute(query, params)
        return await cursor.fetchall()

    async def get_new_unnotified_programs(self, user_id: int):
        cursor = await self._conn.execute(
            """SELECT p.* FROM programs p
               WHERE p.status = '모집중'
               AND p.id NOT IN (
                   SELECT program_id FROM notifications WHERE user_id = ?
               )
               ORDER BY p.scraped_at DESC""",
            (user_id,),
        )
        return await cursor.fetchall()

    async def add_user(self, telegram_id: int, username: str = None) -> int:
        now = datetime.now().isoformat()
        try:
            cursor = await self._conn.execute(
                "INSERT INTO users (telegram_id, username, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (telegram_id, username, now, now),
            )
            await self._conn.commit()
            return cursor.lastrowid
        except aiosqlite.IntegrityError:
            await self._conn.execute(
                "UPDATE users SET username = ?, updated_at = ? WHERE telegram_id = ?",
                (username, now, telegram_id),
            )
            await self._conn.commit()
            cursor = await self._conn.execute(
                "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
            )
            row = await cursor.fetchone()
            return row["id"]

    async def get_user(self, telegram_id: int):
        cursor = await self._conn.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        return await cursor.fetchone()

    async def set_user_categories(self, user_id: int, categories: list[str]):
        await self._conn.execute(
            "DELETE FROM user_categories WHERE user_id = ?", (user_id,)
        )
        for cat in categories:
            await self._conn.execute(
                "INSERT OR IGNORE INTO user_categories (user_id, category) VALUES (?, ?)",
                (user_id, cat),
            )
        await self._conn.commit()

    async def get_user_categories(self, user_id: int) -> list[str]:
        cursor = await self._conn.execute(
            "SELECT category FROM user_categories WHERE user_id = ?", (user_id,)
        )
        rows = await cursor.fetchall()
        return [row["category"] for row in rows]

    async def add_user_keyword(self, user_id: int, keyword: str):
        await self._conn.execute(
            "INSERT OR IGNORE INTO user_keywords (user_id, keyword) VALUES (?, ?)",
            (user_id, keyword.strip()),
        )
        await self._conn.commit()

    async def remove_user_keyword(self, user_id: int, keyword: str):
        await self._conn.execute(
            "DELETE FROM user_keywords WHERE user_id = ? AND keyword = ?",
            (user_id, keyword.strip()),
        )
        await self._conn.commit()

    async def get_user_keywords(self, user_id: int) -> list[str]:
        cursor = await self._conn.execute(
            "SELECT keyword FROM user_keywords WHERE user_id = ?", (user_id,)
        )
        rows = await cursor.fetchall()
        return [row["keyword"] for row in rows]

    async def toggle_notifications(self, user_id: int) -> bool:
        cursor = await self._conn.execute(
            "SELECT notifications_enabled FROM users WHERE id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        new_val = 0 if row["notifications_enabled"] else 1
        await self._conn.execute(
            "UPDATE users SET notifications_enabled = ?, updated_at = ? WHERE id = ?",
            (new_val, datetime.now().isoformat(), user_id),
        )
        await self._conn.commit()
        return bool(new_val)

    async def get_users_with_notifications(self):
        cursor = await self._conn.execute(
            "SELECT * FROM users WHERE notifications_enabled = 1"
        )
        return await cursor.fetchall()

    async def log_notification(self, user_id: int, program_id: int):
        await self._conn.execute(
            "INSERT OR IGNORE INTO notifications (user_id, program_id, sent_at) VALUES (?, ?, ?)",
            (user_id, program_id, datetime.now().isoformat()),
        )
        await self._conn.commit()

    async def get_matching_programs(self, user_id: int, limit: int = 10):
        categories = await self.get_user_categories(user_id)
        keywords = await self.get_user_keywords(user_id)

        if not categories and not keywords:
            return []

        conditions = []
        params = []

        if categories:
            placeholders = ",".join("?" * len(categories))
            conditions.append(f"category IN ({placeholders})")
            params.extend(categories)

        if keywords:
            keyword_conds = " OR ".join(["title LIKE ?" for _ in keywords])
            conditions.append(f"({keyword_conds})")
            params.extend([f"%{kw}%" for kw in keywords])

        where = " OR ".join(conditions)
        query = f"SELECT * FROM programs WHERE status = '모집중' AND ({where}) ORDER BY scraped_at DESC LIMIT ?"
        params.append(limit)

        cursor = await self._conn.execute(query, params)
        return await cursor.fetchall()
