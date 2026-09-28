"""结构化 JSON 日志：一行一条，字段稳定，便于检索与聚合。

为什么不用 print：生产排障需要按 request_id / session_id 过滤，纯文本日志做不到。
为什么写 stderr 而不是 stdout：MCP 的 stdio 传输用 stdout 走 JSONRPC 协议，
    任何日志打到 stdout 都会污染协议流导致客户端解析失败。stderr 是日志的安全区。
用法：
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.info("sql.executed", extra={"context": {"rows": 12, "latency_ms": 87}})
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import sys

_STANDARD = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """把 LogRecord 序列化为单行 JSON；extra={"context": {...}} 原样落入 context 字段。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": _dt.datetime.now(_dt.UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload["context"] = context
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    # stderr：保住 stdout 给 MCP stdio 协议（见模块 docstring）
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # 第三方库降噪，避免 trace 被 httpx/uvicorn 淹投
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
