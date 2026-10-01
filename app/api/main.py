"""FastAPI 接入层：鉴权、限流、SSE 流式问数端点。

端点：
    GET  /health          探活（DB/Redis/存储）
    POST /ask             问数（SSE 流式返回节点进度/澄清/审批/结果）
    POST /ask/resume      恢复执行（回答澄清 / 审批决定）

安全（与简历叙事对应）：
    - X-API-Key 鉴权：白名单制，拒绝即 401
    - token bucket 限流：每 key 20 次/分钟（Redis 实现，多 worker 一致）
    - 输入长度限制：防超长 prompt 攻击
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.application.ask_service import ask, resume
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.infra.cache import healthcheck as redis_health
from app.infra.cache import token_bucket
from app.infra.db import close_pools, init_pools
from app.infra.db import healthcheck as pg_health
from app.infra.storage import get_storage

log = get_logger(__name__)

MAX_QUESTION_LEN = 500


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    setup_logging(get_settings().log_level)
    await init_pools()
    log.info("api.started")
    yield
    await close_pools()
    from app.agents.supervisor import close_checkpointer
    from app.infra.cache import close_redis

    await close_checkpointer()
    await close_redis()
    from app.infra.observability import shutdown as shutdown_observability

    shutdown_observability()
    log.info("api.stopped")


app = FastAPI(title="DataCrew API", version="0.2.0", lifespan=lifespan)

# /files 静态挂载：本地存储适配器产出的图表 URL（/files/charts/xxx.svg）
# 需要它才能被浏览器/前端直接访问。MinIO 模式下由对象存储网关提供 URL，
# 不挂本地目录（storage_backend=minio 时此处静默跳过）。
try:
    get_storage()  # 实例化即确保本地根目录存在
    app.mount(
        "/files",
        StaticFiles(directory=get_settings().storage_local_root),
        name="files",
    )
except Exception:
    pass


async def require_api_key(x_api_key: str = Header(default="")) -> str:
    """API Key 白名单鉴权。"""
    settings = get_settings()
    if x_api_key not in settings.api_key_set:
        raise HTTPException(status_code=401, detail="invalid api key")
    return x_api_key


async def rate_limit(api_key: str = Depends(require_api_key)) -> str:
    """token bucket 限流：每 key 每分钟 RATE_LIMIT_PER_MIN 次。"""
    settings = get_settings()
    allowed = await token_bucket(
        f"ratelimit:{api_key}", settings.rate_limit_per_min, settings.rate_limit_per_min
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    return api_key


@app.get("/health")
async def health() -> dict:
    """探活：DB 双池 / Redis / 对象存储。"""
    pg = await pg_health()
    redis_ok = await redis_health()
    storage_ok = True
    try:
        get_storage()
    except Exception:
        storage_ok = False
    ok = all(pg.values()) and redis_ok and storage_ok
    return {"ok": ok, "postgres": pg, "redis": redis_ok, "storage": storage_ok}


def _sse(event: dict) -> str:
    """SSE 帧：event: <type> + data: <json>。"""
    return f"event: {event['event']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/ask")
async def ask_endpoint(
    request: Request, api_key: str = Depends(rate_limit)
) -> StreamingResponse:
    """问数入口（SSE）。body: {"question": "...", "session_id": "..."}"""
    body = await request.json()
    question = (body.get("question") or "").strip()
    session_id = body.get("session_id") or "default"
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    if len(question) > MAX_QUESTION_LEN:
        raise HTTPException(status_code=400, detail=f"question too long (max {MAX_QUESTION_LEN})")

    async def gen() -> AsyncIterator[str]:
        try:
            async for event in ask(question, session_id):
                yield _sse(event)
        except Exception as e:  # 兜底：任何未预期错误都以 SSE 错误事件返回，不挂断连接
            log.exception("ask.failed")
            yield _sse({"event": "error", "message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/ask/resume")
async def resume_endpoint(
    request: Request, api_key: str = Depends(rate_limit)
) -> StreamingResponse:
    """恢复执行（SSE）。body: {"session_id": "...", "value": "澄清回答" 或 {"approved": true}}"""
    body = await request.json()
    session_id = body.get("session_id") or "default"
    if "value" not in body:
        raise HTTPException(status_code=400, detail="value is required")

    async def gen() -> AsyncIterator[str]:
        try:
            async for event in resume(session_id, body["value"]):
                yield _sse(event)
        except Exception as e:
            log.exception("resume.failed")
            yield _sse({"event": "error", "message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")
