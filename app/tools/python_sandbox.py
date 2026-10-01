"""Python 分析沙箱：在隔离进程里执行数据分析代码。

威胁模型（与 sql_guard 同构，面试可讲）：
    LLM 生成的代码同样不可信——它可能 import 网络库外传数据、可能 sleep
    拖垮 worker、可能写文件搞破坏。所以 sandbox 的设计哲学与七道闸一致：
    **不信任执行体，用确定性边界兜底**。

四道边界（进程级，跨平台）：
    1. 隔离解释器：python -I（无用户 site-packages、无 PYTHONPATH、不把
       CWD 放进 sys.path）——防止通过 .pth/sitecustomize 夹带私货
    2. 网络禁用：子进程内替换 socket.socket / create_connection /getaddrinfo
       为抛异常的守卫——"金融数据不出本机"的硬边界
    3. 资源限额：Linux 下 resource.setrlimit 限制 CPU 时间与文件大小；
       Windows 无 setrlimit，退化为仅超时强杀（见下方诚实边界）
    4. 超时强杀：subprocess timeout 到点 kill 整个进程树，返回 timeout
    5. 显式 stdin=DEVNULL：不继承父进程 stdin（见 _run 注释里的 MCP stdio
       死等事故——这是"子进程不继承协议管道"的通用纪律）

诚实边界（README ADR 有记录，面试主动说）：
    - 进程级禁网对"用 ctypes 直接调 connect syscall"级别的攻击无效——
      那是容器/namespace 级隔离的职责（部署用 network:none）。本实现是
      **纵深防御中的一层**，不是安全产品的竞品。
    - 工作目录给一个空临时目录：代码可读写 tmp，但不该碰业务文件。

返回结构（与 sql_execute 对齐，Agent 消费）：
    {"ok": bool, "stdout": str, "stderr": str, "latency_ms": int,
     "error_type": "timeout|blocked|error|empty|ok"}
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time

from app.core.logging import get_logger

log = get_logger(__name__)

MAX_TIMEOUT_S = 30
MAX_CODE_CHARS = 20_000
MAX_OUTPUT_CHARS = 8_000

# 子进程引导脚本：先装边界再 exec 用户代码。
# 为什么用 -c 传引导而不是 sitecustomize：-I 模式下不加载用户 site，
# 引导脚本自己就是 __main__，边界一定装得上。
_BOOTSTRAP = r'''
import sys

def _blocked(*a, **k):
    raise RuntimeError("sandbox: network is disabled")

import socket
socket.socket = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked

try:  # Linux 资源限额；Windows 没有 resource 模块，跳过
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_limit, fsize_limit))
except Exception:
    pass

code = open(code_path, "r", encoding="utf-8").read()
exec(compile(code, "<sandbox>", "exec"), {"__name__": "__main__"})
'''


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[截断，共 {len(text)} 字符]"


async def run_python(code: str, timeout_s: int = 10) -> dict:
    """在隔离进程执行 Python 代码，返回结构化结果。"""
    started = time.perf_counter()
    if not code or not code.strip():
        return {"ok": False, "error": "代码为空", "error_type": "empty"}
    if len(code) > MAX_CODE_CHARS:
        return {
            "ok": False,
            "error": f"代码超长（{len(code)} > {MAX_CODE_CHARS} 字符）",
            "error_type": "too_long",
        }
    timeout_s = max(1, min(int(timeout_s), MAX_TIMEOUT_S))

    # 最小环境：不继承 LLM/PG/Redis 等任何业务变量
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "HOME": tempfile.gettempdir(),
    }
    with tempfile.TemporaryDirectory(prefix="datacrew-sandbox-") as tmp:
        code_path = os.path.join(tmp, "snippet.py")
        with open(code_path, "w", encoding="utf-8") as f:
            f.write(code)
        bootstrap = _BOOTSTRAP.replace("cpu_limit", str(timeout_s)).replace(
            "fsize_limit", str(64 * 1024 * 1024)
        ).replace("code_path", repr(code_path))

        def _run() -> subprocess.CompletedProcess:
            # stdin 必须显式给 DEVNULL：默认继承父进程 stdin。当本工具跑在
            # MCP stdio server（或任何 stdin/stdout 是协议管道的进程）里时，
            # 子进程继承协议管道的 stdin 句柄后会一直完不成（实测 Python 3.12
            # + Windows：communicate 死等到 timeout；显式 DEVNULL 后 70ms 正常
            # 返回）。顺带的好处：沙箱里的 input() 立刻拿到 EOF 而不是挂住。
            return subprocess.run(
                [sys.executable, "-I", "-c", bootstrap],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout_s,
                cwd=tmp,               # 空临时目录：代码碰不到业务文件
                env=env,
            )

        try:
            proc = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            latency_ms = int((time.perf_counter() - started) * 1000)
            log.warning("sandbox.timeout", extra={"context": {"timeout_s": timeout_s}})
            return {
                "ok": False,
                "error": f"执行超时（>{timeout_s}s），已被强杀",
                "error_type": "timeout",
                "latency_ms": latency_ms,
            }

    latency_ms = int((time.perf_counter() - started) * 1000)
    stdout, stderr = _truncate(proc.stdout or ""), _truncate(proc.stderr or "")
    if proc.returncode != 0:
        # 网络被守卫拦截 / 用户代码抛错 / 资源超限，都归为执行失败回灌给 Agent
        err_type = "blocked" if "network is disabled" in stderr else "error"
        return {
            "ok": False,
            "error": (stderr.strip().splitlines() or ["未知错误"])[-1][:500],
            "error_type": err_type,
            "stdout": stdout,
            "stderr": stderr,
            "latency_ms": latency_ms,
        }
    return {
        "ok": True,
        "stdout": stdout,
        "latency_ms": latency_ms,
        "error_type": "ok",
    }
