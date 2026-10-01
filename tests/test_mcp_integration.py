"""MCP 集成测试：真实 spawn stdio server，走 JSON-RPC 协议调 4 个工具。

为什么值得单独立一个文件（D7 踩坑记）：
    2026-10-01 冒烟发现 python_sandbox 在 MCP server 里必超时、本机直跑却
    正常——根因是子进程默认继承父进程 stdin，而 MCP server 的 stdin 是
    stdio 协议管道，子进程拿着这个句柄永远完不成（communicate 死等到
    timeout）。**单元测试覆盖不到这个问题，因为触发条件是"运行在协议
    管道环境里"**——只有完整起一次 stdio 会话才能复现。这就是本文件的存在理由。

依赖：schema_search/sql_execute 需要 PG（CI 有 service 容器；本地没有则跳过）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class McpStdio:
    """最小 MCP stdio 客户端：换行分隔的 JSON-RPC（协议版本 2024-11-05）。"""

    def __init__(self) -> None:
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "app.tools.mcp_server"],
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        self._next_id = 0
        self._send({
            "jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "pytest", "version": "1"}},
        })
        assert "result" in self._recv()
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _recv(self) -> dict:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            if line.strip():
                return json.loads(line)
        raise AssertionError("stream closed")

    def call(self, name: str, arguments: dict) -> dict:
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": self._next_id, "method": "tools/call",
                    "params": {"name": name, "arguments": arguments}})
        resp = self._recv()
        assert "result" in resp, resp
        return json.loads(resp["result"]["content"][0]["text"])

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.terminate()
        self.proc.wait(timeout=5)


@pytest.fixture()
def mcp():
    client = McpStdio()
    yield client
    client.close()


def _pg_reachable() -> bool:
    try:
        import asyncio

        import asyncpg

        from app.core.config import get_settings

        asyncio.run(asyncpg.connect(get_settings().pg_ro_dsn, timeout=2))
        return True
    except Exception:
        return False


needs_pg = pytest.mark.skipif(not _pg_reachable(), reason="PG 不可达（本地无库时跳过）")


def test_four_tools_discovered(mcp: McpStdio) -> None:
    """协议层：4 个工具全部被发现（发现 + 注册，零 DB 依赖）。"""
    mcp._next_id += 1
    mcp._send({"jsonrpc": "2.0", "id": mcp._next_id, "method": "tools/list"})
    resp = mcp._recv()
    names = sorted(t["name"] for t in resp["result"]["tools"])
    assert names == ["chart_gen", "python_sandbox", "schema_search", "sql_execute"]


def test_sandbox_compute_inside_mcp(mcp: McpStdio) -> None:
    """D7 回归：沙箱在 MCP server 进程里必须正常返回（stdin 继承事故的守卫）。"""
    started = time.perf_counter()
    r = mcp.call("python_sandbox", {"code": "print(sum(range(101)))"})
    assert r["ok"] is True
    assert r["stdout"].strip() == "5050"
    assert time.perf_counter() - started < 5  # 死等现象下这里会是 timeout_s


def test_sandbox_network_blocked_inside_mcp(mcp: McpStdio) -> None:
    r = mcp.call("python_sandbox", {
        "code": "import socket; socket.create_connection(('8.8.8.8', 53), timeout=2)",
        "timeout_s": 5,
    })
    assert r["ok"] is False
    assert r["error_type"] == "blocked"


def test_sandbox_timeout_inside_mcp(mcp: McpStdio) -> None:
    r = mcp.call("python_sandbox", {"code": "import time; time.sleep(30)", "timeout_s": 2})
    assert r["ok"] is False
    assert r["error_type"] == "timeout"


def test_chart_gen_inside_mcp(mcp: McpStdio) -> None:
    r = mcp.call("chart_gen", {
        "chart_type": "bar",
        "data": [["app", 100], ["h5", 20]],
        "title": "回归",
    })
    assert r["ok"] is True
    assert r["url"].startswith("/files/")


@needs_pg
def test_schema_search_inside_mcp(mcp: McpStdio) -> None:
    # 注意：schema_search 没有 {"ok": ...} 信封——它没有失败路径，
    # 检索不到就兜底返回全部表（命中数趋近 0 的失败由 LLM 侧感知）。
    # 契约不对称是刻意为之：错误信封只写给"会失败"的工具。
    r = mcp.call("schema_search", {"question": "各渠道的实付销售额"})
    assert r["tables"], r
    assert any("orders" == tb["name"] for tb in r["tables"])


@needs_pg
def test_sql_execute_attack_blocked_inside_mcp(mcp: McpStdio) -> None:
    r = mcp.call("sql_execute", {"sql": "SELECT gold_sql FROM eval.queries"})
    assert r["ok"] is False
    assert r["error_type"] == "unsafe"
