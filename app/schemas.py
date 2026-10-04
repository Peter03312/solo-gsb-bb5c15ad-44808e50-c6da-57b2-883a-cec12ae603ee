"""HTTP 请求/响应模型。

入参刻意使用宽松类型（Any / List[Any]），把“必须带时区、时差非负、
本台≠对方”等业务规则放在服务层显式校验，以便返回语义明确的错误码。
"""

from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DrillCreate(StrictModel):
    drill_id: Any = None
    allowed_time_diff_seconds: Any


class LogSubmit(StrictModel):
    record_id: Any
    home_callsign: Any
    peer_callsign: Any
    timestamp: Any
    band: Any
    mode: Any


class LogBatch(StrictModel):
    logs: List[Any]


# ---- 响应模型 -------------------------------------------------------------


class PairOut(BaseModel):
    a: str
    b: str
    time_diff_seconds: float
    time_diff_micros: int
    band: str
    mode: str
    home_a: str
    peer_a: str
    home_b: str
    peer_b: str
    timestamp_a: str
    timestamp_b: str


class LogOut(BaseModel):
    record_id: str
    home_callsign: str
    peer_callsign: str
    timestamp: str
    band: str
    mode: str


class ResultOut(BaseModel):
    drill_id: str
    version: int
    sealed: bool
    allowed_time_diff_seconds: float
    pair_count: int
    unpaired_count: int
    total_time_diff_seconds: float
    pairs: List[PairOut]
    unpaired: List[LogOut]


class DrillOut(BaseModel):
    drill_id: str
    allowed_time_diff_seconds: float
    version: int
    sealed: bool
    created_at: str
    sealed_at: Optional[str] = None
    log_count: int
    pair_count: int


class SubmitResult(BaseModel):
    drill_id: str
    record_id: str
    retransmitted: bool
    corrected: bool
    version: int
    sealed: bool
    pair_count: int
    unpaired_count: int
    pairs: List[PairOut]
    unpaired: List[LogOut]


class BatchResult(BaseModel):
    drill_id: str
    version: int
    sealed: bool
    accepted: int
    created: int
    retransmitted: int
    corrected: int
    pair_count: int
    unpaired_count: int
    pairs: List[PairOut]
    unpaired: List[LogOut]


class SealOut(BaseModel):
    drill_id: str
    sealed: bool
    version: int
    sealed_at: Optional[str] = None


class HealthOut(BaseModel):
    status: str
