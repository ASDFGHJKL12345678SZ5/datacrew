"""对象存储适配器：本地磁盘 / MinIO 一键切换。

设计动机（面试可讲）：
    MinIO 镜像在当前网络环境不可达（Docker Hub 对大镜像匿名拉取返回 401）。
    存储能力被抽象为 ObjectStorage 协议，业务代码只依赖协议不依赖实现——
    本地磁盘先跑通全流程，网络恢复后 .env 改一个值即切换到 MinIO，零代码改动。
    这就是"依赖倒置"在基础设施层的具体落地。

用法：
    from app.infra.storage import get_storage
    storage = get_storage()
    # key 不带前导 /；local 模式返回 /files/<key>（API 侧静态挂载），
    # minio 模式返回预签名 URL
    url = storage.put("charts/run-001.svg", svg_bytes, "image/svg+xml")
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.core.config import get_settings


@runtime_checkable
class ObjectStorage(Protocol):
    """对象存储协议：put/get/exists/url 四个能力。"""

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """写入并返回可访问 URL。"""
        ...

    def get(self, key: str) -> bytes:
        """读取对象内容。"""
        ...

    def exists(self, key: str) -> bool:
        """判断对象是否存在。"""
        ...

    def url(self, key: str) -> str:
        """返回对象的访问地址。"""
        ...


class LocalStorage:
    """本地磁盘实现：文件落在 <storage_local_root>/<key>。"""

    def __init__(self, root: str = "data/storage") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return self.url(key)

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()

    def url(self, key: str) -> str:
        # FastAPI 将 /files 静态挂载到 storage root（见 app/api/main.py）
        return f"/files/{key}"


class MinioStorage:
    """MinIO 实现：.env 设 STORAGE_BACKEND=minio 且网络可达时启用。"""

    def __init__(self) -> None:
        from minio import Minio  # 延迟导入：本地模式不依赖 minio 连通性

        s = get_settings()
        self._client = Minio(
            s.minio_endpoint,
            access_key=s.minio_access_key,
            secret_key=s.minio_secret_key,
            secure=s.minio_secure,
        )
        self._bucket = s.minio_bucket
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(
            self._bucket, key, io.BytesIO(data), length=len(data), content_type=content_type
        )
        return self.url(key)

    def get(self, key: str) -> bytes:
        resp = self._client.get_object(self._bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def exists(self, key: str) -> bool:
        try:
            self._client.stat_object(self._bucket, key)
            return True
        except Exception:
            return False

    def url(self, key: str) -> str:
        return f"{get_settings().minio_endpoint}/{self._bucket}/{key}"


@lru_cache
def get_storage() -> ObjectStorage:
    """按配置返回存储实现。测试中可用 get_storage.cache_clear() 重置。"""
    s = get_settings()
    if s.storage_backend == "minio":
        return MinioStorage()
    return LocalStorage(s.storage_local_root)
