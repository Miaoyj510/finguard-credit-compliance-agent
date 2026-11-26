<#
.SYNOPSIS
    把本地分支推送到 GitHub（推送前自动跑完整回归，不绿不许推）。

.DESCRIPTION
    这台机器上两个 git 都有问题：
      · 系统 git（D:\Git\Git）缺 git-remote-https 助手，push 直接报
        "remote-https is not a git command"；
      · Codex 运行时 git 的 git-remote-https 在「认证重试」阶段会静默崩溃
        （exit 128、零输出）。

    解法：绕开 git 的凭据协商，从 gh 的凭据存储里取到 OAuth 令牌，
    拼成 Basic 认证头，用 http.extraHeader 预置——令牌只存在于本进程的
    环境变量里，不落盘、不进命令行、不进 shell 历史。

    安全说明：令牌放在进程环境变量中，同用户的其他进程理论上可读。
    这是 gh 自身 OAuth 令牌的暴露面，换来的是「不落盘、不进命令行」。
    令牌可用 `gh auth logout` 随时吊销。

.PARAMETER Branches
    要推送的分支，默认三个都推。

.PARAMETER SkipVerify
    跳过推送前的完整回归（不推荐；ROADMAP 要求合并 main 前后都跑完整测试）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/push.ps1
    powershell -ExecutionPolicy Bypass -File scripts/push.ps1 -Branches feat/impl
#>
[CmdletBinding()]
param(
    [string[]]$Branches = @("main", "feat/impl", "review/impl"),
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"

function Resolve-GitExe {
    # 选一个真正带 git-remote-https 助手的 git
    $candidates = @()
    if ($env:FINGUARD_GIT) { $candidates += $env:FINGUARD_GIT }
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

function Resolve-GhExe {
    $candidates = @()
    if ($env:FINGUARD_GH) { $candidates += $env:FINGUARD_GH }
    $cmd = Get-Command gh -ErrorAction SilentlyContinue
    if ($cmd) { $candidates += $cmd.Source }
    $candidates += @("C:\Program Files\GitHub CLI\gh.exe",
                      (Join-Path $env:LOCALAPPDATA "Programs\GitHub CLI\gh.exe"))
    foreach ($exe in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if (Test-Path $exe) { return $exe }
    }
    throw "未找到 gh，请先安装 GitHub CLI 并执行 gh auth login。"
}

function Get-GhCredential {
    $ghExe = Resolve-GhExe
    $lines = "protocol=https`nhost=github.com`n`n" | & $ghExe auth git-credential get 2>$null
    $user = ($lines | Where-Object { $_ -like "username=*" }) -replace "^username=", ""
    $pass = ($lines | Where-Object { $_ -like "password=*" }) -replace "^password=", ""
    if (-not $pass) { throw "gh 没有可用凭据，请先执行 gh auth login。" }
    return @{ User = $user; Pass = $pass }
}

$repoRoot = Split-Path $PSScriptRoot -Parent
$git = Resolve-GitExe

# ---- 推送前的闸门：完整回归不绿就不许推（ROADMAP 检查点要求）----
if (-not $SkipVerify) {
    Write-Host "=== 推送前完整回归 ===" -ForegroundColor Cyan
    Push-Location $repoRoot
    try {
        & python (Join-Path $repoRoot "scripts\verify.py")
        $verifyExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($verifyExit -ne 0) {
        throw "完整回归未通过（退出码 $verifyExit），已中止推送。修复后重试，或用 -SkipVerify 强行跳过（不建议）。"
    }
    Write-Host "回归通过，继续推送。" -ForegroundColor Green
}

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
