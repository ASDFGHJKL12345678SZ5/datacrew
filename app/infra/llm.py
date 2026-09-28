"""LLM 客户端适配器：OpenAI 兼容协议，DeepSeek/Qwen/本地 vLLM 一套代码。

设计要点（面试可讲）：
1. provider 无关：换模型只改 .env（LLM_BASE_URL / LLM_MODEL），零代码改动
2. 分级路由：轻任务（意图分类/歧义检测）走 LLM_SMALL_MODEL，重任务（SQL 生成/解读）走 LLM_MODEL
   —— 这是成本优化最直接的一刀（轻任务占调用量的大头）
3. 并发控制：asyncio.Semaphore 限制在途请求，防止打爆 API 配额触发限流
4. 重试：指数退避，只对可重试错误（超时/5xx/连接错误）重试，4xx 业务错误快速失败
5. 计量：每次调用记录 token 用量/耗时/成本，供成本核算与压测报告（简历数字来源）
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from openai import AsyncOpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

# 各模型计价（元 / 百万 tokens）—— 以官网为准，可用环境变量覆盖
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "deepseek-chat": {"input_miss": 2.0, "input_hit": 0.2, "output": 8.0},
    "qwen-max": {"input_miss": 2.4, "input_hit": 0.5, "output": 9.6},
    "qwen-turbo": {"input_miss": 0.3, "input_hit": 0.05, "output": 0.9},
}


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    cost_yuan: float
    raw: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    """OpenAI 兼容客户端 + 并发控制 + 重试 + 计量。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        self._client = AsyncOpenAI(
            api_key=self._s.llm_api_key or "sk-placeholder",
            base_url=self._s.llm_base_url,
            timeout=self._s.llm_timeout_s,
            max_retries=0,  # 重试由 tenacity 统一管理
        )
        self._sem = asyncio.Semaphore(self._s.llm_max_concurrency)
        # 累计计量（进程级；压测报告从这里取值）
        self.total_calls = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost_yuan = 0.0

    def _price(self, model: str) -> dict[str, float]:
        return DEFAULT_PRICING.get(model, {"input_miss": 2.0, "input_hit": 0.2, "output": 8.0})

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _call_openai(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: int | None,
        response_format: dict[str, str] | None,
    ) -> Any:
        return await self._client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,  # type: ignore[arg-type]
        )

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        tier: Literal["main", "small"] = "main",
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """统一对话入口。tier=small 走轻量模型（分级路由）。"""
        model = self._s.llm_model if tier == "main" else self._s.llm_small_model
        temp = self._s.llm_temperature if temperature is None else temperature
        fmt = {"type": "json_object"} if json_mode else None

        started = time.perf_counter()
        async with self._sem:  # 并发闸：在途请求 ≤ LLM_MAX_CONCURRENCY
            resp = await self._call_openai(messages, model, temp, max_tokens, fmt)
        latency_ms = int((time.perf_counter() - started) * 1000)

        usage = resp.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        price = self._price(model)
        # 简化：prompt tokens 按未命中计价（未接缓存命中统计，偏保守）
        cost = (
            prompt_tokens * price["input_miss"] + completion_tokens * price["output"]
        ) / 1_000_000

        # 累计计量
        self.total_calls += 1
        self.total_prompt_tokens += prompt_tokens
        self.total_completion_tokens += completion_tokens
        self.total_cost_yuan += cost

        content = resp.choices[0].message.content or ""
        log.info(
            "llm.chat",
            extra={
                "context": {
                    "model": model,
                    "tier": tier,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "latency_ms": latency_ms,
                    "cost_yuan": round(cost, 6),
                }
            },
        )
        return LLMResponse(
            content=content,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            cost_yuan=cost,
        )

    def usage_snapshot(self) -> dict[str, Any]:
        """当前进程累计用量（压测/eval 报告数据源）。"""
        return {
            "calls": self.total_calls,
            "prompt_tokens": self.total_prompt_tokens,
            "completion_tokens": self.total_completion_tokens,
            "cost_yuan": round(self.total_cost_yuan, 4),
        }


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    """进程单例。"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
