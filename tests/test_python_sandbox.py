"""python_sandbox 单测：四条边界逐一验证（无需 DB / 无需网络）。"""
from __future__ import annotations

import pytest

from app.tools.python_sandbox import MAX_CODE_CHARS, run_python


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_compute_and_stdout(self) -> None:
        r = await run_python("print(sum(range(101)))")
        assert r["ok"] is True
        assert r["stdout"].strip() == "5050"
        assert r["error_type"] == "ok"

    @pytest.mark.asyncio
    async def test_data_structure_usage(self) -> None:
        r = await run_python("xs=[i*i for i in range(5)]; print(xs)")
        assert r["ok"] is True
        assert "[0, 1, 4, 9, 16]" in r["stdout"]

    @pytest.mark.asyncio
    async def test_no_output_ok(self) -> None:
        # Agent 代码忘了 print：执行成功但没有输出，不应判失败
        r = await run_python("x = 1 + 1")
        assert r["ok"] is True
        assert r["stdout"] == ""


class TestBoundaries:
    @pytest.mark.asyncio
    async def test_timeout_kills_process(self) -> None:
        r = await run_python("import time; time.sleep(30)", timeout_s=1)
        assert r["ok"] is False
        assert r["error_type"] == "timeout"

    @pytest.mark.asyncio
    async def test_network_blocked(self) -> None:
        r = await run_python(
            "import socket\n"
            "socket.create_connection(('8.8.8.8', 53), timeout=2)"
        )
        assert r["ok"] is False
        assert r["error_type"] == "blocked"
        assert "network is disabled" in r["error"]

    @pytest.mark.asyncio
    async def test_syntax_error_reported(self) -> None:
        r = await run_python("this is not python!")
        assert r["ok"] is False
        assert r["error_type"] == "error"

    @pytest.mark.asyncio
    async def test_runtime_error_reported(self) -> None:
        r = await run_python("print(1/0)")
        assert r["ok"] is False
        assert "ZeroDivisionError" in r["error"]

    @pytest.mark.asyncio
    async def test_empty_code_rejected(self) -> None:
        r = await run_python("   ")
        assert r["ok"] is False
        assert r["error_type"] == "empty"

    @pytest.mark.asyncio
    async def test_oversized_code_rejected(self) -> None:
        r = await run_python("x = 1  #" + "a" * MAX_CODE_CHARS)
        assert r["ok"] is False
        assert r["error_type"] == "too_long"

    @pytest.mark.asyncio
    async def test_timeout_clamped_not_waiting(self) -> None:
        # timeout_s=0 被夹到下限 1s（不会除以零/不等待）；
        # 超大值被夹到上限——这条用例只验证下限路径不炸
        r = await run_python("print(42)", timeout_s=0)
        assert r["ok"] is True
        assert r["stdout"].strip() == "42"
