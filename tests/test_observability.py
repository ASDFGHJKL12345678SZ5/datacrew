"""observability 单测：降级路径（CI 无 Langfuse key，必须零网络零异常）。"""
from __future__ import annotations

from app.core.config import get_settings
from app.infra.observability import get_langfuse, record_generation, shutdown


def test_disabled_by_default() -> None:
    """.env 未配 key（CI 环境）：get_langfuse 返回 None，不初始化客户端。"""
    settings = get_settings()
    if settings.langfuse_enabled:
        return  # 本地配了 key 的开发者跳过此断言
    assert get_langfuse() is None


def test_record_is_noop_when_disabled() -> None:
    """未配置时 record_generation 静默返回——绝不能抛异常炸主链路。"""
    record_generation(
        model="deepseek-chat",
        messages=[{"role": "user", "content": "hi"}],
        content="hello",
        prompt_tokens=1,
        completion_tokens=1,
        latency_ms=5,
        cost_yuan=0.0,
        session_id="test-session",
    )  # 不抛异常即通过


def test_shutdown_safe_when_never_initialized() -> None:
    shutdown()  # 未初始化时 shutdown 也必须是安全的 no-op
