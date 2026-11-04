"""Durable per-bot, per-group delivery cursors. No live bot state is stored here."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

DAY = 86400


class PetStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS pets (
                bot TEXT NOT NULL, group_id INTEGER NOT NULL, player TEXT NOT NULL,
                nickname TEXT NOT NULL, requester INTEGER NOT NULL, token TEXT NOT NULL,
                last_match INTEGER, last_time REAL, next_check REAL NOT NULL,
                expires REAL NOT NULL, failures INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '', inactivity_match INTEGER,
                state TEXT NOT NULL DEFAULT 'active', pushes INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(bot, group_id, player))""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self, bot: str, group: int | None = None):
        with self.connect() as db:
            sql, args = "SELECT * FROM pets WHERE bot=?", [bot]
            if group is not None:
                sql += " AND group_id=?"
                args.append(group)
            return [dict(row) for row in db.execute(sql, args)]

    def add(self, bot, group, requester, snapshot, now, *, next_check=None):
        player = snapshot.nickname.casefold()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT player FROM pets WHERE bot=? AND group_id=? "
                "AND state='active' AND expires>?",
                (bot, group, now),
            ).fetchall()
            if player in {r[0] for r in active}:
                return False
            if len(active) >= 3:
                raise ValueError("我在每个群最多追踪 3 位玩家，请先取消旧订阅。")
            db.execute(
                """INSERT INTO pets
                (bot, group_id, player, nickname, requester, token, last_match,
                 last_time, next_check, expires)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot, group_id, player) DO UPDATE SET
                nickname=excluded.nickname, requester=excluded.requester, token=excluded.token,
                last_match=excluded.last_match, last_time=excluded.last_time,
                next_check=excluded.next_check, expires=excluded.expires,
                failures=0, error='', inactivity_match=NULL, state='active'""",
                (
                    bot,
                    group,
                    player,
                    snapshot.nickname,
                    requester,
                    uuid.uuid4().hex,
                    snapshot.match_id,
                    snapshot.started_at,
                    now if next_check is None else next_check,
                    now + 7 * DAY,
                ),
            )
        return True

    def remove(self, bot, group, nickname):
        with self.connect() as db:
            result = db.execute(
                "UPDATE pets SET state='cancelled', token=? WHERE bot=? AND group_id=? "
                "AND player=? AND state IN ('active', 'expired', 'missing')",
                (uuid.uuid4().hex, bot, group, nickname.strip().casefold()),
            )
            return result.rowcount > 0

    def current(self, row):
        with self.connect() as db:
            found = db.execute(
                "SELECT * FROM pets WHERE bot=? AND group_id=? AND player=? AND token=?",
                (row["bot"], row["group_id"], row["player"], row["token"]),
            ).fetchone()
            return dict(found) if found else None

    def update(self, row, **values):
        allowed = {
            "last_match",
            "last_time",
            "next_check",
            "expires",
            "failures",
            "error",
            "inactivity_match",
            "state",
            "nickname",
            "pushes",
        }
        if not values or not values.keys() <= allowed:
            raise ValueError("Invalid pet state update")
        with self.connect() as db:
            db.execute(
                "UPDATE pets SET "
                + ", ".join(f"{key}=?" for key in values)
                + " WHERE bot=? AND group_id=? AND player=? AND token=?",
                (*values.values(), row["bot"], row["group_id"], row["player"], row["token"]),
            )
