# Polymarket Edge Agent — Start All Services
# Usage: .\scripts\start_all.ps1
# Stops any existing services, starts all 5 in background, tails logs.

$ErrorActionPreference = "Continue"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ROOT

Write-Host "`n=== Polymarket Edge Agent ===" -ForegroundColor Cyan
Write-Host "Starting all services...`n" -ForegroundColor Yellow

# Activate venv if present
$venvActivate = Join-Path $ROOT ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    Write-Host "[venv] Activating .venv..." -ForegroundColor DarkGray
    & $venvActivate
}

# Kill any existing ploy processes
$existing = Get-Process -Name python -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "ploy" }
if ($existing) {
    Write-Host "[cleanup] Stopping existing ploy processes..." -ForegroundColor DarkGray
    $existing | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

# Ensure artifacts dir for logs
$artifactsDir = Join-Path $ROOT "artifacts"
if (-not (Test-Path $artifactsDir)) { New-Item -ItemType Directory -Path $artifactsDir | Out-Null }

# Start services
$services = @(
    @{ Name = "ploy-ingest";  Cmd = "ploy-ingest" },
    @{ Name = "ploy-enrich";  Cmd = "ploy-enrich" },
    @{ Name = "ploy-reason";  Cmd = "ploy-reason" },
    @{ Name = "ploy-notify";  Cmd = "ploy-notify" },
    @{ Name = "ploy-web";     Cmd = "ploy-web" }
)

$pids = @()
foreach ($svc in $services) {
    $logFile = Join-Path $artifactsDir "$($svc.Name).log"
    $proc = Start-Process -FilePath $svc.Cmd -NoNewWindow -PassThru `
        -RedirectStandardOutput $logFile -RedirectStandardError (Join-Path $artifactsDir "$($svc.Name).err.log")
    $pids += $proc.Id
    Write-Host "  [OK] $($svc.Name) started (PID $($proc.Id))" -ForegroundColor Green
}

Write-Host "`n=== All services running ===" -ForegroundColor Cyan
Write-Host "  Dashboard:  http://127.0.0.1:8765" -ForegroundColor White
Write-Host "  Slack bot:  listening on port 8766" -ForegroundColor White
Write-Host "  PIDs:       $($pids -join ', ')" -ForegroundColor DarkGray
Write-Host "`nPress Ctrl+C to stop all services.`n" -ForegroundColor Yellow

# Trap Ctrl+C to kill all
try {
    while ($true) { Start-Sleep -Seconds 5 }
} finally {
    Write-Host "`nStopping services..." -ForegroundColor Yellow
    foreach ($pid in $pids) {
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
    Write-Host "All services stopped." -ForegroundColor Green
}
