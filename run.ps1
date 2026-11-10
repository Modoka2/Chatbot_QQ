# 一键启动（PowerShell）
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "没有找到 python，请先安装 Python 3.10+ 并加入 PATH" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "已生成 .env，请填入 DEEPSEEK_API_KEY 等密钥后重新运行。" -ForegroundColor Yellow
    Write-Host "（只想先看看效果的话，可以直接运行：python main.py --mock）"
    exit 0
}

python main.py @args
