"""基础设施适配层：PostgreSQL/Redis/对象存储/LLM 客户端。接口在此隔离，可替换可 mock。"""
from app.infra.cache import cache_get, cache_set, token_bucket
from app.infra.db import admin_pool, init_pools, ro_pool
from app.infra.llm import LLMClient, get_llm
from app.infra.storage import get_storage

__all__ = [
    "admin_pool", "ro_pool", "init_pools",
    "cache_get", "cache_set", "token_bucket",
    "LLMClient", "get_llm",
    "get_storage",
]
