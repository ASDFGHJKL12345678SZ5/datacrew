"""应用配置：所有环境变量经 pydantic-settings 统一管理。

设计要点：
1. 代码里禁止出现 os.getenv —— 配置只有一个入口（本模块），便于审计与测试覆盖；
2. 管理连接（PG_ADMIN_DSN）与只读连接（PG_RO_DSN）分离：
   灌数据/建评测集用管理员身份，Agent 执行 SQL 永远只用只读角色；
3. LLM_* 走 OpenAI 兼容协议，DeepSeek/Qwen/本地 vLLM 一套客户端，改 BASE_URL 即切换。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # ---- 应用 ----
    app_env: str = "dev"
    log_level: str = "INFO"

    # ---- PostgreSQL ----
    pg_admin_dsn: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/ecommerce"
    )
    pg_ro_dsn: str = Field(
        default="postgresql://datacrew_ro:datacrew_ro_pwd@localhost:5432/ecommerce"
    )
    pg_pool_min: int = 5
    pg_pool_max: int = 20

    # ---- Redis ----
    redis_url: str = "redis://localhost:6379/0"

    # ---- 对象存储（适配器模式：local / minio 可切换）----
    # local：本地磁盘（当前网络环境 MinIO 镜像不可达时的默认选择）
    # minio：网络恢复后在 .env 改此值即可，业务代码零改动
    storage_backend: str = "local"
    storage_local_root: str = "data/storage"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "datacrew"
    minio_secure: bool = False

    # ---- LLM（OpenAI 兼容）----
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"        # 主力：SQL 生成、结论解读
    llm_small_model: str = "deepseek-chat"  # 轻任务：意图分类、歧义检测（可换更便宜模型）
    llm_temperature: float = 0.0
    llm_max_concurrency: int = 8
    llm_timeout_s: float = 60.0
    # mock：规则假模型，无 API Key 跑通全链路（CI 默认）；real：真实 LLM
    llm_mode: str = "mock"

    # ---- 安全 ----
    api_keys: str = "dev-key-001"           # 逗号分隔的 API Key 白名单
    rate_limit_per_min: int = 20            # 每 key 每分钟请求数（token bucket）
    sql_row_limit: int = 1000               # SQL 结果行数硬上限
    sql_timeout_ms: int = 3000              # 语句超时

    # ---- 可观测（Langfuse，留空则降级为本地日志）----
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    @property
    def api_key_set(self) -> set[str]:
        """API Key 白名单集合。"""
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    """进程内单例。测试中可用 get_settings.cache_clear() 重置。"""
    return Settings()
