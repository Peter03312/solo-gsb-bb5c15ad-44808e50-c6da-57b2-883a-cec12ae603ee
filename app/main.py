"""通联日志互证服务 FastAPI 入口。

环境变量：
- QSO_DATABASE: SQLite 文件路径（默认 /data/qso.db，可设为 :memory:）
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from .errors import service_error_handler, validation_error_handler, ServiceError
from .schemas import (
    BatchResult,
    DrillCreate,
    DrillOut,
    HealthOut,
    LogBatch,
    LogOut,
    LogSubmit,
    ResultOut,
    SealOut,
    SubmitResult,
)
from . import services
from .storage import Database

DB_PATH = os.environ.get("QSO_DATABASE", "/data/qso.db")
API = "/api/v1"

db: Database  # lifespan 中初始化


@asynccontextmanager
async def lifespan(app: FastAPI):
    global db
    db = Database(DB_PATH)  # 建库 + 建表即数据初始化
    try:
        yield
    finally:
        db.close()


app = FastAPI(
    title="应急无线电演练 · 通联日志互证服务",
    version="1.0.0",
    description=(
        "记录各台站通联日志，按“相互指认、同波段同模式、时差不超限、"
        "每条记录至多配对一次”计算全场最优互证（最大配对数 → 最小总时差 → "
        "配对列表字典序最小），支持版本化重算与封存。"
    ),
    lifespan=lifespan,
)
app.add_exception_handler(ServiceError, service_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)


@app.get("/health", response_model=HealthOut, tags=["meta"])
def health():
    return {"status": "ok"}


# ---- 演练 -----------------------------------------------------------------


@app.post(f"{API}/drills", response_model=DrillOut, status_code=201, tags=["drills"])
def create_drill(body: DrillCreate):
    return services.create_drill(db, body.drill_id, body.allowed_time_diff_seconds)


@app.get(f"{API}/drills", response_model=list[DrillOut], tags=["drills"])
def get_drills():
    return services.list_drills(db)


@app.get(f"{API}/drills/{{drill_id}}", response_model=DrillOut, tags=["drills"])
def get_drill(drill_id: str):
    return services.get_drill(db, drill_id)


@app.post(f"{API}/drills/{{drill_id}}/seal", response_model=SealOut, tags=["drills"])
def seal_drill(drill_id: str):
    return services.seal_drill(db, drill_id)


# ---- 日志 -----------------------------------------------------------------


@app.post(
    f"{API}/drills/{{drill_id}}/logs",
    response_model=SubmitResult,
    tags=["logs"],
)
def submit_log(drill_id: str, body: LogSubmit):
    return services.submit_log(db, drill_id, body.model_dump())


@app.put(
    f"{API}/drills/{{drill_id}}/logs/{{record_id}}",
    response_model=SubmitResult,
    tags=["logs"],
)
def put_log(drill_id: str, record_id: str, body: LogSubmit):
    return services.submit_log(
        db, drill_id, body.model_dump(), path_record_id=record_id
    )


@app.post(
    f"{API}/drills/{{drill_id}}/logs/batch",
    response_model=BatchResult,
    tags=["logs"],
)
def submit_batch(drill_id: str, body: LogBatch):
    return services.submit_batch(db, drill_id, list(body.logs))


@app.get(
    f"{API}/drills/{{drill_id}}/logs",
    response_model=list[LogOut],
    tags=["logs"],
)
def get_logs(drill_id: str):
    return services.list_logs(db, drill_id)


# ---- 互证结果 -------------------------------------------------------------


@app.get(
    f"{API}/drills/{{drill_id}}/result",
    response_model=ResultOut,
    tags=["result"],
)
def get_result(drill_id: str):
    return services.build_result(db, drill_id)
