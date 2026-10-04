"""SQLite 存储层。

并发模型：
- WAL 日志 + busy_timeout，读不阻塞写；
- 进程内 threading.Lock 串行化所有写入事务；事务内使用 BEGIN IMMEDIATE
  获取保留锁，多进程/多容器挂载同一数据库文件时也由 SQLite 写锁互斥；
- 版本号递增与 pairs 表重算在同一写事务内提交，任何外部可见状态要么
  是旧版本（version+配对一致），要么是新版本（version+配对一致）。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS drills (
    drill_id     TEXT PRIMARY KEY,
    allowed_diff_micros INTEGER NOT NULL CHECK (allowed_diff_micros >= 0),
    version      INTEGER NOT NULL DEFAULT 0,
    sealed       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    sealed_at    TEXT
);

CREATE TABLE IF NOT EXISTS logs (
    drill_id     TEXT NOT NULL REFERENCES drills(drill_id),
    record_id    TEXT NOT NULL,
    home_callsign TEXT NOT NULL,
    peer_callsign TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    time_micros  INTEGER NOT NULL,
    band         TEXT NOT NULL,
    mode         TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    version_added INTEGER NOT NULL,
    version_updated INTEGER NOT NULL,
    PRIMARY KEY (drill_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_logs_drill ON logs(drill_id);

CREATE TABLE IF NOT EXISTS pairs (
    drill_id     TEXT NOT NULL,
    version      INTEGER NOT NULL,
    record_a     TEXT NOT NULL,
    record_b     TEXT NOT NULL,
    time_diff_micros INTEGER NOT NULL,
    PRIMARY KEY (drill_id, record_a, record_b)
);
CREATE INDEX IF NOT EXISTS idx_pairs_drill ON pairs(drill_id, version);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _split_statements(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn = sqlite3.connect(
            path,
            check_same_thread=False,
            isolation_level=None,  # autocommit；事务手动 BEGIN/COMMIT
            timeout=30.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._write_lock = threading.Lock()
        self._init_pragmas()
        self.init_schema()

    def _init_pragmas(self) -> None:
        c = self._conn
        if self.path != ":memory:":
            c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA foreign_keys=ON")

    def init_schema(self) -> None:
        def _build(conn: sqlite3.Connection) -> None:
            # 注意：不能用 executescript（会隐式 COMMIT 当前事务）
            for stmt in _split_statements(SCHEMA):
                conn.execute(stmt)

        self.write_transaction(_build)

    # ---- 基础 -------------------------------------------------------------

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        return self._conn.execute(sql, tuple(params)).fetchone()

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, tuple(params)).fetchall()

    def write_transaction(self, fn):
        """串行化执行写事务；fn(conn) 的返回值随 COMMIT 一起返回。

        fn 内只允许使用传入的 conn；抛出异常则回滚。
        """
        with self._write_lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = fn(conn)
                conn.execute("COMMIT")
                return result
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self._conn.close()
