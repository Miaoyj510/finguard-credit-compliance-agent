<#
.SYNOPSIS
    把本地分支推送到 GitHub。

.DESCRIPTION
    这台机器上两个 git 都有问题：
      · 系统 git（D:\Git\Git）缺 git-remote-https 助手，push 直接报
        "remote-https is not a git command"；
      · Codex 运行时 git 的 git-remote-https 在「认证重试」阶段会静默崩溃
        （exit 128、零输出）。

    所以这里绕开 git 的凭据协商：从 gh 的凭据存储里取到 OAuth 令牌，
    拼成 Basic 认证头，用 http.extraHeader 预置——令牌只存在于本进程的
    环境变量里，不落盘、不进命令行、不进 shell 历史。

.PARAMETER Branches
    要推送的分支，默认三个都推。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/push.ps1
    powershell -ExecutionPolicy Bypass -File scripts/push.ps1 -Branches feat/impl
#>
[CmdletBinding()]
param(
    [string[]]$Branches = @("main", "feat/impl", "review/impl")
)

$ErrorActionPreference = "Stop"

function Resolve-GitExe {
    # 选一个真正带 git-remote-https 助手的 git
    $candidates = @()
    $runtimeGit = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe"
    if (Test-Path $runtimeGit) { $candidates += $runtimeGit }
    $cmd = Get-Command git -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    $candidates += @("D:\Git\Git\cmd\git.exe", "C:\Program Files\Git\cmd\git.exe")

    foreach ($exe in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path $exe)) { continue }
        $execPath = (& $exe --exec-path 2>$null)
        if (-not $execPath) { continue }
        $dirs = @($execPath, (Join-Path (Split-Path (Split-Path $execPath -Parent) -Parent) "bin"))
        foreach ($d in $dirs) {
            if (Test-Path (Join-Path $d "git-remote-https.exe")) { return $exe }
        }
    }
    throw "找不到带 git-remote-https 助手的 git，请检查安装。"
}

function Get-GhCredential {
    # 不依赖 PATH：Windows PowerShell 5.1 常常拿不到新装的程序目录
    $ghExe = $null
    foreach ($c in @(
            (Get-Command gh -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
            "C:\Program Files\GitHub CLI\gh.exe",
            (Join-Path $env:LOCALAPPDATA "Programs\GitHub CLI\gh.exe"))) {
        if ($c -and (Test-Path $c)) { $ghExe = $c; break }
    }
    if (-not $ghExe) { throw "未找到 gh，请先安装 GitHub CLI 并执行 gh auth login。" }
    $lines = "protocol=https`nhost=github.com`n`n" | & $ghExe auth git-credential get 2>$null
    $user = ($lines | Where-Object { $_ -like "username=*" }) -replace "^username=", ""
    $pass = ($lines | Where-Object { $_ -like "password=*" }) -replace "^password=", ""
    if (-not $pass) { throw "gh 没有可用凭据，请先执行 gh auth login。" }
    return @{ User = $user; Pass = $pass }
}

$git = Resolve-GitExe
$cred = Get-GhCredential
Write-Host "使用 git : $git"
Write-Host "使用账号: $($cred.User)"

# 令牌只进环境变量，绝不进命令行参数
$env:GIT_CONFIG_COUNT = "1"
$env:GIT_CONFIG_KEY_0 = "http.extraHeader"
$env:GIT_CONFIG_VALUE_0 = "Authorization: Basic " + [Convert]::ToBase64String(
    [Text.Encoding]::ASCII.GetBytes("$($cred.User):$($cred.Pass)"))

try {
    foreach ($b in $Branches) {
        Write-Host ""
        Write-Host "=== push $b ===" -ForegroundColor Cyan
        $ok = $false
        for ($try = 1; $try -le 3 -and -not $ok; $try++) {
            if ($try -gt 1) {
                Write-Host "  第 $try 次重试（上次可能被网络重置）..." -ForegroundColor Yellow
                Start-Sleep -Seconds (5 * $try)
            }
            # http.version=HTTP/1.1：绕开部分网络环境下的 HTTP/2 多路复用断连
            & $git -c http.version=HTTP/1.1 -c http.postBuffer=524288000 push -u origin $b
            if ($LASTEXITCODE -eq 0) { $ok = $true }
        }
        if (-not $ok) { throw "推送 $b 失败（连续 3 次）" }
    }
    Write-Host ""
    Write-Host "完成。远端分支：" -ForegroundColor Green
    & $git ls-remote --heads origin | ForEach-Object { "  " + ($_ -split "\s+")[1] }
}
finally {
    $env:GIT_CONFIG_COUNT = $null
    $env:GIT_CONFIG_KEY_0 = $null
    $env:GIT_CONFIG_VALUE_0 = $null
}
