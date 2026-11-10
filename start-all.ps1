# 一键启动：先拉起 NapCat（QQ），再启动机器人
#
# 机器相关路径写在 deploy.local.ps1（已在 .gitignore），模板见 deploy.local.example.ps1。
# 没配也能用：脚本会在 napcat/ 目录里自动找 napcat-quick.bat，并从里面解析 QQ 号。
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$NapCatDir = ""
$QQ = ""

$localDeploy = Join-Path $PSScriptRoot "deploy.local.ps1"
if (Test-Path $localDeploy) {
    . $localDeploy
}

function Test-Port([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect("127.0.0.1", $Port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

# ---------- 自动定位 NapCat ----------
if (-not $NapCatDir -or -not (Test-Path $NapCatDir)) {
    $found = Get-ChildItem -Path (Join-Path $PSScriptRoot "napcat") -Filter "napcat-quick.bat" `
        -Recurse -Depth 4 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) {
        $NapCatDir = $found.DirectoryName
        if (-not $QQ) {
            $line = Select-String -Path $found.FullName -Pattern "-q\s+(\d{5,12})" -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($line) { $QQ = $line.Matches[0].Groups[1].Value }
        }
    }
}

Write-Host "===== 微信 / QQ AI 聊天机器人 =====" -ForegroundColor Cyan

# ---------- 第一步：NapCat ----------
if (Test-Port 3000) {
    Write-Host "[1/2] NapCat 已在运行（3000 已监听），跳过。" -ForegroundColor Yellow
} else {
    if (-not $NapCatDir -or -not (Test-Path (Join-Path $NapCatDir "napcat-quick.bat"))) {
        Write-Host "找不到 NapCat：请把 deploy.local.example.ps1 复制成 deploy.local.ps1 并填好路径。" -ForegroundColor Red
        exit 1
    }
    Write-Host "[1/2] 启动 NapCat（QQ $QQ，快捷登录，免扫码）..." -ForegroundColor Cyan
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList '/c', 'napcat-quick.bat' `
        -WorkingDirectory $NapCatDir `
        -WindowStyle Normal

    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline -and -not (Test-Port 3000)) {
        Start-Sleep -Seconds 2
    }
    if (-not (Test-Port 3000)) {
        Write-Host "X NapCat 90 秒内没起来，去看它那个黑窗口里的报错。" -ForegroundColor Red
        exit 1
    }
    Write-Host "    NapCat 就绪：127.0.0.1:3000" -ForegroundColor Green
}

# ---------- 第二步：机器人 ----------
if (Test-Port 8080) {
    Write-Host "[2/2] 8080 已被占用，机器人可能已经在运行。" -ForegroundColor Yellow
    Write-Host "      要重启机器人，先执行：" -ForegroundColor Yellow
    Write-Host '      Get-NetTCPConnection -LocalPort 8080 -State Listen | % { Stop-Process -Id $_.OwningProcess -Force }' -ForegroundColor DarkGray
    exit 0
}

Write-Host "[2/2] 启动机器人（Ctrl+C 停止）..." -ForegroundColor Cyan
python main.py
