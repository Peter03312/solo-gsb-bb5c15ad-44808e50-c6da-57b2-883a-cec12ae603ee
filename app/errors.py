"""业务异常与统一错误响应。"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ServiceError(Exception):
    """业务校验错误 -> HTTP 4xx，响应体 {"error": {...}}。"""

    def __init__(self, code: str, message: str, status_code: int = 400, details=None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)


def _error_body(code: str, message: str, details=None) -> dict:
    err = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"error": err}


async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(exc.code, exc.message, exc.details),
    )


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = []
    for item in exc.errors():
        details.append(
            {
                "loc": [str(x) for x in item.get("loc", [])],
                "message": item.get("msg", ""),
                "type": item.get("type", ""),
            }
        )
    return JSONResponse(
        status_code=422,
        content=_error_body("VALIDATION_ERROR", "请求参数校验失败", details),
    )
