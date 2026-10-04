"""业务服务层：演练管理、日志 upsert、封存、互证结果构建。

版本语义：
- version 初始为 0；
- 新记录号入库（新日志）或异内容更正 -> version+1，并在同一事务内
  重算全场配对、整体替换 pairs 表；
- 同记录号同内容重传 -> 幂等，不推进版本、不重算；
- 封存不改版本（日志集未变，最终结果即当前结果）。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from .errors import ServiceError
from .matching import LogRecord, match_all
from .storage import Database, utc_now_iso
from .validation import (
    content_hash,
    normalize_band_mode,
    normalize_callsign,
    normalize_id,
    parse_timestamp,
    parse_tolerance,
    to_micros,
)

_LOG_FIELDS = (
    "record_id",
    "home_callsign",
    "peer_callsign",
    "timestamp",
    "band",
    "mode",
)


# ---- 规范化 ---------------------------------------------------------------


def normalize_log_payload(payload: Any, index: Optional[int] = None) -> dict:
    """把单条日志入参规范化；index 用于批量提交时报错定位。"""
    prefix = f"logs[{index}]." if index is not None else ""

    def err(code: str, msg: str, status: int = 400) -> ServiceError:
        return ServiceError(code, prefix + msg, status, None if index is None else {"index": index})

    if not isinstance(payload, dict):
        raise err("INVALID_LOG", "每条日志必须是 JSON 对象")
    extra = set(payload) - set(_LOG_FIELDS)
    if extra:
        raise err("INVALID_LOG", f"存在未知字段: {sorted(extra)}")
    missing = [f for f in _LOG_FIELDS if f not in payload]
    if missing:
        raise err("INVALID_LOG", f"缺少必填字段: {missing}")

    record_id = normalize_id(
        payload["record_id"], f"{prefix}record_id", "INVALID_RECORD_ID"
    )
    home = normalize_callsign(payload["home_callsign"], f"{prefix}home_callsign")
    peer = normalize_callsign(payload["peer_callsign"], f"{prefix}peer_callsign")
    if home == peer:
        raise err(
            "SAME_CALLSIGN",
            f"本台呼号与对方呼号不能相同: {home}",
        )
    try:
        ts_iso, ts_micros = parse_timestamp(payload["timestamp"])
    except ServiceError as e:
        if index is None:
            raise
        raise ServiceError(
            e.code, prefix + e.message, e.status_code, {"index": index}
        )
    band = normalize_band_mode(payload["band"], f"{prefix}band")
    mode = normalize_band_mode(payload["mode"], f"{prefix}mode")

    normalized = {
        "record_id": record_id,
        "home_callsign": home,
        "peer_callsign": peer,
        "timestamp_utc": ts_iso,
        "time_micros": ts_micros,
        "band": band,
        "mode": mode,
    }
    normalized["content_hash"] = content_hash(
        {
            "home_callsign": home,
            "peer_callsign": peer,
            "timestamp_utc": ts_iso,
            "time_micros": ts_micros,
            "band": band,
            "mode": mode,
        }
    )
    return normalized


# ---- 演练 -----------------------------------------------------------------


def create_drill(db: Database, drill_id: Any, tolerance_raw: Any) -> dict:
    did = normalize_id(drill_id, "drill_id", "INVALID_DRILL_ID")
    tolerance_seconds = parse_tolerance(tolerance_raw)
    tolerance_micros = to_micros(tolerance_seconds)

    def tx(conn: sqlite3.Connection):
        exists = conn.execute(
            "SELECT 1 FROM drills WHERE drill_id=?", (did,)
        ).fetchone()
        if exists:
            raise ServiceError(
                "DRILL_EXISTS", f"演练 {did} 已存在", 409
            )
        conn.execute(
            "INSERT INTO drills (drill_id, allowed_diff_micros, version, sealed, created_at)"
            " VALUES (?,? ,0, 0, ?)",
            (did, tolerance_micros, utc_now_iso()),
        )
        return None

    db.write_transaction(tx)
    return get_drill(db, did)


def _get_drill_row(conn: sqlite3.Connection, drill_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM drills WHERE drill_id=?", (drill_id,)
    ).fetchone()
    if row is None:
        raise ServiceError("DRILL_NOT_FOUND", f"未知演练: {drill_id}", 404)
    return row


def get_drill(db: Database, drill_id: str) -> dict:
    row = db.query_one("SELECT * FROM drills WHERE drill_id=?", (drill_id,))
    if row is None:
        raise ServiceError("DRILL_NOT_FOUND", f"未知演练: {drill_id}", 404)
    return _drill_out(db, row)


def list_drills(db: Database) -> list[dict]:
    rows = db.query_all("SELECT * FROM drills ORDER BY drill_id")
    return [_drill_out(db, r) for r in rows]


def _drill_out(db: Database, row: sqlite3.Row) -> dict:
    log_count = db.query_one(
        "SELECT COUNT(*) c FROM logs WHERE drill_id=?", (row["drill_id"],)
    )["c"]
    pair_count = db.query_one(
        "SELECT COUNT(*) c FROM pairs WHERE drill_id=?", (row["drill_id"],)
    )["c"]
    return {
        "drill_id": row["drill_id"],
        "allowed_time_diff_seconds": row["allowed_diff_micros"] / 1_000_000,
        "version": row["version"],
        "sealed": bool(row["sealed"]),
        "created_at": row["created_at"],
        "sealed_at": row["sealed_at"],
        "log_count": log_count,
        "pair_count": pair_count,
    }


# ---- 日志写入 -------------------------------------------------------------


def _upsert_log(conn: sqlite3.Connection, drill_id: str, log: dict, version: int) -> str:
    """返回 'new' | 'retransmitted' | 'corrected'。调用方负责版本与重算。"""
    existing = conn.execute(
        "SELECT content_hash FROM logs WHERE drill_id=? AND record_id=?",
        (drill_id, log["record_id"]),
    ).fetchone()
    if existing is not None:
        if existing["content_hash"] == log["content_hash"]:
            return "retransmitted"
        conn.execute(
            "UPDATE logs SET home_callsign=?, peer_callsign=?, timestamp_utc=?,"
            " time_micros=?, band=?, mode=?, content_hash=?, version_updated=?"
            " WHERE drill_id=? AND record_id=?",
            (
                log["home_callsign"],
                log["peer_callsign"],
                log["timestamp_utc"],
                log["time_micros"],
                log["band"],
                log["mode"],
                log["content_hash"],
                version,
                drill_id,
                log["record_id"],
            ),
        )
        return "corrected"
    conn.execute(
        "INSERT INTO logs (drill_id, record_id, home_callsign, peer_callsign,"
        " timestamp_utc, time_micros, band, mode, content_hash,"
        " version_added, version_updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            drill_id,
            log["record_id"],
            log["home_callsign"],
            log["peer_callsign"],
            log["timestamp_utc"],
            log["time_micros"],
            log["band"],
            log["mode"],
            log["content_hash"],
            version,
            version,
        ),
    )
    return "new"


def _recompute_pairs(
    conn: sqlite3.Connection, drill_id: str, tolerance_micros: int, version: int
) -> None:
    rows = conn.execute(
        "SELECT * FROM logs WHERE drill_id=?", (drill_id,)
    ).fetchall()
    records = [
        LogRecord(
            record_id=r["record_id"],
            home=r["home_callsign"],
            peer=r["peer_callsign"],
            band=r["band"],
            mode=r["mode"],
            time_micros=r["time_micros"],
        )
        for r in rows
    ]
    result = match_all(records, tolerance_micros)
    conn.execute("DELETE FROM pairs WHERE drill_id=?", (drill_id,))
    conn.executemany(
        "INSERT INTO pairs (drill_id, version, record_a, record_b, time_diff_micros)"
        " VALUES (?,?,?,?,?)",
        [
            (drill_id, version, p.a, p.b, p.time_diff_micros)
            for p in result.pairs
        ],
    )


def submit_log(db: Database, drill_id: str, log_in: dict, path_record_id: Optional[str] = None) -> dict:
    log = normalize_log_payload(log_in)
    if path_record_id is not None and log["record_id"] != path_record_id:
        raise ServiceError(
            "RECORD_ID_MISMATCH",
            f"路径记录号 {path_record_id} 与请求体记录号 {log['record_id']} 不一致",
            400,
        )

    def tx(conn: sqlite3.Connection):
        drill = _get_drill_row(conn, drill_id)
        if drill["sealed"]:
            raise ServiceError("DRILL_SEALED", f"演练 {drill_id} 已封存，拒绝写入", 409)
        status = _upsert_log(conn, drill_id, log, drill["version"] + 1)
        if status == "retransmitted":
            return {"status": status, "version": drill["version"]}
        new_version = drill["version"] + 1
        conn.execute(
            "UPDATE drills SET version=? WHERE drill_id=?",
            (new_version, drill_id),
        )
        _recompute_pairs(conn, drill_id, drill["allowed_diff_micros"], new_version)
        return {"status": status, "version": new_version}

    outcome = db.write_transaction(tx)
    result = build_result(db, drill_id)
    result["record_id"] = log["record_id"]
    result["retransmitted"] = outcome["status"] == "retransmitted"
    result["corrected"] = outcome["status"] == "corrected"
    return result


def submit_batch(db: Database, drill_id: str, logs_in: list) -> dict:
    if not isinstance(logs_in, list) or not logs_in:
        raise ServiceError("INVALID_LOG", "logs 必须是非空数组", 400)
    normalized = [normalize_log_payload(item, i) for i, item in enumerate(logs_in)]
    # 同批次内记录号重复：后者与前者内容相同则视为重复提交（重传），
    # 内容不同则属于自相矛盾的更正，明确报错。
    seen: dict[str, str] = {}
    for i, log in enumerate(normalized):
        rid = log["record_id"]
        if rid in seen:
            if seen[rid] != log["content_hash"]:
                raise ServiceError(
                    "BATCH_RECORD_CONFLICT",
                    f"logs[{i}] 与批次内更早的同记录号 {rid} 内容不一致",
                    400,
                    {"index": i, "record_id": rid},
                )

    def tx(conn: sqlite3.Connection):
        drill = _get_drill_row(conn, drill_id)
        if drill["sealed"]:
            raise ServiceError("DRILL_SEALED", f"演练 {drill_id} 已封存，拒绝写入", 409)
        counts = {"new": 0, "retransmitted": 0, "corrected": 0}
        # 先用当前版本探测每条状态，确定是否需要版本递增
        probe_version = drill["version"] + 1
        statuses = [_upsert_log(conn, drill_id, log, probe_version) for log in normalized]
        for s in statuses:
            counts[s] += 1
        changed = counts["new"] + counts["corrected"]
        if changed:
            new_version = drill["version"] + 1
            conn.execute(
                "UPDATE drills SET version=? WHERE drill_id=?",
                (new_version, drill_id),
            )
            # 上面插入/更新时已使用 probe_version（== new_version），无需改写
            _recompute_pairs(conn, drill_id, drill["allowed_diff_micros"], new_version)
            return {"version": new_version, "counts": counts}
        return {"version": drill["version"], "counts": counts}

    outcome = db.write_transaction(tx)
    result = build_result(db, drill_id)
    result["accepted"] = len(normalized)
    result["retransmitted"] = outcome["counts"]["retransmitted"]
    result["corrected"] = outcome["counts"]["corrected"]
    result["created"] = outcome["counts"]["new"]
    return result


def seal_drill(db: Database, drill_id: str) -> dict:
    def tx(conn: sqlite3.Connection):
        drill = _get_drill_row(conn, drill_id)
        if drill["sealed"]:
            return {"already": True, "version": drill["version"], "sealed_at": drill["sealed_at"]}
        now = utc_now_iso()
        conn.execute(
            "UPDATE drills SET sealed=1, sealed_at=? WHERE drill_id=?",
            (now, drill_id),
        )
        return {"already": False, "version": drill["version"], "sealed_at": now}

    outcome = db.write_transaction(tx)
    return {
        "drill_id": drill_id,
        "sealed": True,
        "version": outcome["version"],
        "sealed_at": outcome["sealed_at"],
    }


# ---- 读取 -----------------------------------------------------------------


def _log_out(row: sqlite3.Row) -> dict:
    return {
        "record_id": row["record_id"],
        "home_callsign": row["home_callsign"],
        "peer_callsign": row["peer_callsign"],
        "timestamp": row["timestamp_utc"],
        "band": row["band"],
        "mode": row["mode"],
    }


def build_result(db: Database, drill_id: str) -> dict:
    drill_row = db.query_one("SELECT * FROM drills WHERE drill_id=?", (drill_id,))
    if drill_row is None:
        raise ServiceError("DRILL_NOT_FOUND", f"未知演练: {drill_id}", 404)

    log_rows = db.query_all(
        "SELECT * FROM logs WHERE drill_id=? ORDER BY record_id", (drill_id,)
    )
    logs = {r["record_id"]: r for r in log_rows}
    pair_rows = db.query_all(
        "SELECT * FROM pairs WHERE drill_id=? ORDER BY record_a, record_b",
        (drill_id,),
    )

    pairs_out = []
    paired: set[str] = set()
    total_dt = 0
    for pr in pair_rows:
        a, b = pr["record_a"], pr["record_b"]
        paired.add(a)
        paired.add(b)
        total_dt += pr["time_diff_micros"]
        ra, rb = logs[a], logs[b]
        pairs_out.append(
            {
                "a": a,
                "b": b,
                "time_diff_seconds": pr["time_diff_micros"] / 1_000_000,
                "time_diff_micros": pr["time_diff_micros"],
                "band": ra["band"],
                "mode": ra["mode"],
                "home_a": ra["home_callsign"],
                "peer_a": ra["peer_callsign"],
                "home_b": rb["home_callsign"],
                "peer_b": rb["peer_callsign"],
                "timestamp_a": ra["timestamp_utc"],
                "timestamp_b": rb["timestamp_utc"],
            }
        )

    unpaired_out = [_log_out(r) for r in log_rows if r["record_id"] not in paired]

    return {
        "drill_id": drill_id,
        "version": drill_row["version"],
        "sealed": bool(drill_row["sealed"]),
        "allowed_time_diff_seconds": drill_row["allowed_diff_micros"] / 1_000_000,
        "pair_count": len(pairs_out),
        "unpaired_count": len(unpaired_out),
        "total_time_diff_seconds": total_dt / 1_000_000,
        "pairs": pairs_out,
        "unpaired": unpaired_out,
    }


def list_logs(db: Database, drill_id: str) -> list[dict]:
    if db.query_one("SELECT 1 FROM drills WHERE drill_id=?", (drill_id,)) is None:
        raise ServiceError("DRILL_NOT_FOUND", f"未知演练: {drill_id}", 404)
    rows = db.query_all(
        "SELECT * FROM logs WHERE drill_id=? ORDER BY record_id", (drill_id,)
    )
    return [_log_out(r) for r in rows]
