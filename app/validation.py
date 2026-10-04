"""输入解析与规范化：时差、带时区时间、呼号/波段/模式、内容指纹。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .errors import ServiceError

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def parse_tolerance(value: Any, field_name: str = "allowed_time_diff_seconds") -> float:
    """解析非负允许时差（秒），接受 int/float/数字字符串。

    布尔、NaN/Infinity、负数一律拒绝。
    """
    if isinstance(value, bool):
        raise ServiceError(
            "INVALID_TIME_TOLERANCE",
            f"{field_name} 必须是非负数字，不能为布尔值",
            400,
        )
    if isinstance(value, str):
        try:
            num = float(value.strip())
        except (ValueError, AttributeError):
            raise ServiceError(
                "INVALID_TIME_TOLERANCE",
                f"{field_name} 必须是非负数字（秒）",
                400,
            )
    elif isinstance(value, (int, float)):
        num = float(value)
    else:
        raise ServiceError(
            "INVALID_TIME_TOLERANCE",
            f"{field_name} 必须是非负数字（秒）",
            400,
        )
    if num != num or num in (float("inf"), float("-inf")):
        raise ServiceError(
            "INVALID_TIME_TOLERANCE", f"{field_name} 不能为 NaN 或 Infinity", 400
        )
    if num < 0:
        raise ServiceError(
            "INVALID_TIME_TOLERANCE", f"{field_name} 不能为负数", 400
        )
    return num


def to_micros(seconds: float) -> int:
    """秒（非负 float）转整数微秒（四舍五入，避免浮点尾巴）。"""
    return int(round(seconds * 1_000_000))


def parse_timestamp(value: Any) -> tuple[str, int]:
    """解析带时区的 ISO 8601 时间字符串。

    返回 (UTC 规范化 ISO 字符串, UTC epoch 微秒)。
    无时区信息（naive）报 TIMESTAMP_WITHOUT_TIMEZONE；
    格式无法解析报 INVALID_TIMESTAMP。
    """
    if not isinstance(value, str) or not value.strip():
        raise ServiceError("INVALID_TIMESTAMP", "时间必须是 ISO 8601 字符串", 400)
    s = value.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError:
        raise ServiceError(
            "INVALID_TIMESTAMP", f"无法解析的时间格式: {value!r}", 400
        )
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ServiceError(
            "TIMESTAMP_WITHOUT_TIMEZONE",
            f"时间必须带时区信息（如 +08:00 或 Z）: {value!r}",
            400,
        )
    dt_utc = dt.astimezone(timezone.utc)
    micros = (dt_utc - _EPOCH) // timedelta(microseconds=1)
    iso = dt_utc.isoformat().replace("+00:00", "Z")
    return iso, micros


def normalize_callsign(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServiceError("INVALID_CALLSIGN", f"{field_name} 不能为空", 400)
    return value.strip().upper()


def normalize_band_mode(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServiceError("INVALID_BAND_MODE", f"{field_name} 不能为空", 400)
    return value.strip().upper()


def normalize_id(value: Any, field_name: str, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServiceError(code, f"{field_name} 不能为空", 400)
    return value.strip()


def content_hash(payload: dict) -> str:
    """对规范化后的日志内容做稳定指纹（不含 record_id）。"""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
