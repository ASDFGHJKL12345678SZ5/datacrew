"""Chat 函数抽象：Agent 节点只依赖这个接口，不关心背后是真实 LLM 还是 Mock。

切换方式：.env 里 LLM_MODE=mock|real（默认 mock，配好 key 后改 real）

为什么绕这一层（面试可讲）：
1. 测试不需要外部服务（见 llm_mock.py）
2. 换 provider / 换模型只改适配层，节点代码零改动
3. 未来加语义缓存、加 Langfuse trace 都只在这一层做，节点无感
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from app.agents.llm_mock import mock_chat
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_FENCE = chr(96) * 3  # 三个反引号：LLM 常把 JSON 包在代码块里


class ChatFn(Protocol):
    async def __call__(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str: ...


def _parse_json(text: str) -> dict[str, Any]:
    """宽容 JSON 解析：LLM 有时会在 JSON 外面包代码块。"""
    text = text.strip()
    if text.startswith(_FENCE):
        text = text.split("\n", 1)[-1].rsplit(_FENCE, 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 兜底：截取第一个 { 到最后一个 }
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


async def _real_chat(messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
    from app.infra.llm import get_llm

    resp = await get_llm().chat(messages, json_mode=json_mode)
    return resp.content


async def _mock_chat_async(messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
    return mock_chat(messages, json_mode=json_mode)


def get_chat_fn() -> ChatFn:
    """按配置返回 chat 实现。mock 模式让全链路测试不依赖 API Key。"""
    mode = get_settings().llm_mode.lower()
    if mode == "real":
        return _real_chat
    return _mock_chat_async


async def chat_json(messages: list[dict[str, str]]) -> dict[str, Any]:
    """调用 LLM 并把输出解析成 dict（所有节点统一走这里）。"""
    chat_fn = get_chat_fn()
    text = await chat_fn(messages, json_mode=True)
    return _parse_json(text)
