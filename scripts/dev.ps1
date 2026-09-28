# DataCrew 开发助手（Windows / pwsh）
# 用法：.\scripts\dev.ps1 <command>
param(
    [Parameter(Position=0)]
    [ValidateSet("install","infra","down","logs","db","test","lint","eval","api","demo","smoke","clean","help")]
    [string]$Command = "help"
)

$ErrorActionPreference = "Stop"
$PY = ".venv\Scripts\python.exe"

function Install-Deps {
    if (-not (Test-Path ".venv")) { python -m venv .venv }
    & $PY -m pip install --upgrade pip
    & $PY -m pip install -e ".[dev]"
    Write-Host "依赖安装完成。激活：.venv\Scripts\Activate.ps1" -ForegroundColor Green
}

function Up-Infra {
    if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "已生成 .env（请填入 LLM_API_KEY）" -ForegroundColor Yellow }
    docker compose up -d
    Write-Host "等待健康检查..." -ForegroundColor Cyan
    docker compose ps
}

function Show-Db {
    docker exec -it datacrew-postgres psql -U postgres -d ecommerce -c "\dt biz.*" -c "\dn" -c "\du"
}

switch ($Command) {
    "install" { Install-Deps }
    "infra"   { Up-Infra }
    "down"    { docker compose down }
    "logs"    { docker compose logs -f --tail 100 }
    "db"      { Show-Db }
    "test"    { & $PY -m pytest -v }
    "lint"    { & $PY -m ruff check . ; & $PY -m mypy app/ }
    "eval"    { & $PY -m eval.runner }
    "api"     { & $PY -m app.main }
    "demo"    { & $PY -m streamlit run demo/app.py }
    "smoke"   { & $PY scripts\smoke_tools.py ; & $PY scripts\smoke_mcp.py }
    "clean"   { docker compose down -v; Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue }
    default {
        Write-Host @"
DataCrew 开发助手
  install  创建 venv 并安装依赖（含 dev）
  infra    启动基础设施（postgres/redis）并生成 .env
  down     停止基础设施
  logs     跟踪容器日志
  db       查看数据库对象与角色
  test     运行测试
  lint     ruff + mypy
  eval     运行评测回归
  api      启动 FastAPI（8000 端口，热重载）
  demo     启动 Streamlit 演示
  smoke    工具层冒烟测试（连真库 + MCP 联通）
  clean    删除容器卷与 venv（慎用）
"@ -ForegroundColor Cyan
    }
}
