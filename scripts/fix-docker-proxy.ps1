# 一次性修复脚本：让 Docker Desktop 守护进程走 Windows 宿主机代理
# 用法：管理员权限 pwsh -File scripts\fix-docker-proxy.ps1
param([switch]$Restore)

$ErrorActionPreference = "Stop"
$store = Join-Path $env:APPDATA "Docker\settings-store.json"

if ($Restore) {
    if (Test-Path "$store.bak") { Copy-Item "$store.bak" $store -Force; Write-Host "已从备份恢复" }
    exit 0
}

# 停止 Docker Desktop
Get-Process "Docker Desktop", "com.docker.backend" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 8

# 备份并修改
Copy-Item $store "$store.bak" -Force
$j = Get-Content $store -Raw | ConvertFrom-Json
$j | Add-Member -NotePropertyName "proxyHttpMode" -NotePropertyValue "manual" -Force
$j | Add-Member -NotePropertyName "proxyHttpUrl" -NotePropertyValue "http://host.docker.internal:7897" -Force
$j | Add-Member -NotePropertyName "proxyHttpsUrl" -NotePropertyValue "http://host.docker.internal:7897" -Force
$j | Add-Member -NotePropertyName "proxyExclude" -NotePropertyValue "hubproxy.docker.internal" -Force
$j | ConvertTo-Json -Depth 10 | Set-Content $store -Encoding utf8
Write-Host "代理配置已写入 settings-store.json"

# 重启 Docker Desktop
Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
Write-Host "Docker Desktop 重启中，等待引擎就绪..."
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 5
    $v = docker info --format "{{.ServerVersion}}" 2>$null
    if ($LASTEXITCODE -eq 0) { Write-Host "引擎就绪: $v"; exit 0 }
}
Write-Host "引擎等待超时，请手动检查 Docker Desktop"
