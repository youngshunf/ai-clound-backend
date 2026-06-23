#!/usr/bin/env pwsh
# 本地后端开发启动脚本（Windows PowerShell 版，对应 dev.sh）
#   1. 自动激活 venv（.venv\Scripts\Activate.ps1）
#   2. 杀掉占用目标端口（默认 8020）的旧进程
#   3. 用 fba run 启动（默认开启热重载——改代码/schema 自动生效）
#
# 用法（PowerShell）：
#   .\dev.ps1                         # 127.0.0.1:8020，热重载
#   $env:PORT=8030;  .\dev.ps1        # 改端口
#   $env:HOST='0.0.0.0';  .\dev.ps1   # 对局域网开放
#   .\dev.ps1 --no-reload --workers 4 # 透传给 fba run 的额外参数
#
# 注：用 Git Bash 的 Windows 用户可直接跑 dev.sh，无需本脚本。
# 若提示“无法加载脚本，因为在此系统上禁止运行脚本”，先执行一次：
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

$ErrorActionPreference = 'Stop'

# 始终以脚本所在目录（仓库根）为工作目录，无论从哪里调用
Set-Location $PSScriptRoot

$BindHost = if ($env:HOST) { $env:HOST } else { '127.0.0.1' }
$Port     = if ($env:PORT) { [int]$env:PORT } else { 8020 }
$VenvDir  = if ($env:VENV_DIR) { $env:VENV_DIR } else { '.venv' }

# 1. 激活 venv
$Activate = Join-Path $VenvDir 'Scripts\Activate.ps1'
if (-not (Test-Path $Activate)) {
  Write-Error "找不到虚拟环境 $Activate（先创建 venv 并装依赖）"
  exit 1
}
. $Activate
Write-Host "✓ 已激活 venv：$PSScriptRoot\$VenvDir"

if (-not (Get-Command fba -ErrorAction SilentlyContinue)) {
  Write-Error "venv 内找不到 fba 命令（依赖未装全？）"
  exit 1
}

# 2. 杀掉占用端口的旧进程（先优雅 CloseMainWindow，仍在则强制）
function Get-ListeningPids([int] $TcpPort) {
  try {
    return @(Get-NetTCPConnection -LocalPort $TcpPort -State Listen -ErrorAction Stop |
             Select-Object -ExpandProperty OwningProcess -Unique)
  } catch {
    # 兜底：解析 netstat（老系统无 Get-NetTCPConnection 时）
    return @(netstat -ano |
             Select-String "[:.]$TcpPort\s" |
             Select-String 'LISTENING' |
             ForEach-Object { ($_.ToString().Trim() -split '\s+')[-1] } |
             Sort-Object -Unique)
  }
}

$Pids = Get-ListeningPids $Port
if ($Pids) {
  Write-Host "→ 端口 $Port 被旧进程占用（PID: $($Pids -join ', ')），正在终止…"
  foreach ($ProcId in $Pids) {
    Stop-Process -Id $ProcId -ErrorAction SilentlyContinue
  }
  for ($i = 0; $i -lt 10; $i++) {
    Start-Sleep -Milliseconds 300
    $Pids = Get-ListeningPids $Port
    if (-not $Pids) { break }
  }
  if ($Pids) {
    Write-Host "→ 旧进程未优雅退出，强制 KILL（PID: $($Pids -join ', ')）"
    foreach ($ProcId in $Pids) {
      Stop-Process -Id $ProcId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 500
  }
  Write-Host "✓ 端口 $Port 已释放"
} else {
  Write-Host "✓ 端口 $Port 空闲，无需清理"
}

# 3. 启动（fba run 默认开热重载；额外参数原样透传）
Write-Host "→ 启动后端：fba run --host $BindHost --port $Port $args"
fba run --host $BindHost --port $Port @args
