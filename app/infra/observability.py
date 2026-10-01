"""Langfuse 可观测集成（可选依赖，失败静默降级）。

为什么包这一层（面试可讲）：
    1. 可观测不是强依赖：Langfuse 云服务不可用时，问数主链路毫发无伤——
       观测系统挂掉不该把业务也带走
    2. SDK 版本隔离：langfuse v3+ 迁到 OpenTelemetry，v4 直接移除了旧版
       client.trace() API。我们把 SDK 关在这一个小文件里，换版本/换
       Langsmith 只改这里，业务代码零改动
    3. 降级是默认路径：没配 key 的开发者本地零配置可跑（CI 就是这条路径，
       无需任何密钥）

版本决策（ADR）：固定 langfuse>=2.60,<3——v2 的 client.trace/generation
API 最直接（每次 LLM 调用一条 trace + 一个 generation，带 model/token/
延迟/成本）；迁移 v3+ 时只需把 _record 换成 OTel span + LangfuseSpanProcessor。

记录内容：每次真实 LLM 调用的 model / tier（main/small 分级）/ messages /
content / token 用量 / 延迟 / 估算成本 / session_id。mock 模式不记录
（本地规则假模型没有观测价值）。
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

_client: Any | None = None
_init_attempted = False


def get_langfuse() -> Any | None:
    """按配置返回 Langfuse 客户端；未配置/未安装/初始化失败都返回 None（降级）。"""
    global _client, _init_attempted
    if _init_attempted:
        return _client
    _init_attempted = True
    from app.core.config import get_settings

    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
        log.info("langfuse.enabled", extra={"context": {"host": settings.langfuse_host}})
    except Exception as e:  # SDK 缺失/网络不通/版本不兼容：降级为本地日志
        log.warning("langfuse.degraded", extra={"context": {"reason": str(e)[:120]}})
        _client = None
    return _client


def record_generation(
    *,
    model: str,
    messages: list[dict[str, str]],
    content: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    cost_yuan: float,
    session_id: str | None = None,
    tier: str = "main",
) -> None:
    """记录一次 LLM 调用（trace + generation）。任何失败都只降级不抛出。"""
    client = get_langfuse()
    if client is None:
        return
    try:
        trace = client.trace(
            name="llm.chat",
            input=messages,
            metadata={"session_id": session_id, "model": model},
        )
        generation = trace.generation(
            name="chat_completion",
            model=model,
            input=messages,
            output=content,
            usage={
                "input": prompt_tokens,
                "output": completion_tokens,
                "unit": "TOKENS",
            },
            metadata={
                "latency_ms": latency_ms,
                "cost_yuan": round(cost_yuan, 6),
                "tier": tier,
            },
        )
        generation.end()
        client.flush()
    except Exception as e:
        # 观测失败绝不能影响业务：记一条本地日志就够了
        log.warning("langfuse.record_failed", extra={"context": {"reason": str(e)[:120]}})


def shutdown() -> None:
    """进程退出前刷完缓冲区（FastAPI shutdown 调用）。"""
    global _client
    if _client is not None:
        try:
            _client.flush()
        except Exception:
            pass
