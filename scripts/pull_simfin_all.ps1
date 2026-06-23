<#
  pull_simfin_all.ps1 - load the FULL survivorship-free price set once, hands-off.

  SimFin's free tier rate-limits a big pull, so this loops `pull-simfin` (which resumes by skipping
  already-stored names) with a pause between rounds until the universe stops growing. Run it once and
  walk away; when it stops, data_simfin holds the survivorship-free prices and you never pull again
  (re-run only to extend to newer dates later).

  Usage:
    powershell -ExecutionPolicy Bypass -File scripts\pull_simfin_all.ps1
    powershell -ExecutionPolicy Bypass -File scripts\pull_simfin_all.ps1 -SleepSeconds 60 -MaxRounds 60
#>
param(
    [int]$MaxRounds = 50,
    [int]$SleepSeconds = 45,
    [string]$DataDir = ".\data_simfin",
    [string]$Start = "2019-01-01"
)

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$env:EQR_DATA_DIR = $DataDir
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "ERROR: venv python not found at $py" -ForegroundColor Red; exit 1 }

function PriceCount {
    & $py -c "from equity_research.data import storage; c=storage.connect(); print(c.execute('SELECT count(DISTINCT ticker) FROM prices').fetchone()[0] if storage.table_exists(c,'prices') else 0); c.close()"
}

Write-Host "Loading point-in-time membership into $DataDir (once)..." -ForegroundColor Cyan
& $py -m equity_research.cli pull-index-history | Out-Null

$stall = 0
for ($r = 1; $r -le $MaxRounds; $r++) {
    $before = [int](PriceCount)
    Write-Host "`n=== Round $r/$MaxRounds | $before names stored -> pulling ===" -ForegroundColor Cyan
    & $py -m equity_research.cli pull-simfin --start $Start
    $after = [int](PriceCount)
    Write-Host "=== Round $r done: $after names stored (+$($after - $before)) ===" -ForegroundColor Green
    if ($after -le $before) {
        $stall++
        if ($stall -ge 2) {
            Write-Host "`nNo new names for 2 rounds -> universe fully loaded (or the daily free-tier" -ForegroundColor Yellow
            Write-Host "limit is exhausted -- if so, just run this again tomorrow; it resumes)." -ForegroundColor Yellow
            break
        }
    } else { $stall = 0 }
    if ($r -lt $MaxRounds) { Start-Sleep -Seconds $SleepSeconds }
}
Write-Host "`nFinal: $([int](PriceCount)) names stored in $DataDir." -ForegroundColor Green
Write-Host "Now: .\.venv\Scripts\python -m equity_research.cli backtest --lane simfin --strategy hrp --start 2021-07-01 (with EQR_DATA_DIR=$DataDir)" -ForegroundColor Green
