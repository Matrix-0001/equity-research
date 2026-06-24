<#
  tour.ps1 - run one representative command from EVERY capability, with section banners, so you can
  see the whole platform's output in a single run: v1 (screen / valuation / flags / report),
  v2 (portfolio construction + macro + exposures), v3 Phase A (free indicative backtest), and
  v3 Phase B (survivorship-free SimFin lane, if data_simfin is set up).

  Read-only: it only runs analysis/backtest commands on already-loaded data (no pulls, no commits).
  Usage:
    powershell -ExecutionPolicy Bypass -File scripts\tour.ps1
    powershell -ExecutionPolicy Bypass -File scripts\tour.ps1 -Ticker MSFT
#>
param(
    [string]$Ticker = "AAPL",
    [string]$FreeStart = "2012-01-01",      # deep Yahoo history -> a multi-regime free/indicative run
    [string]$LockStart = "2022-01-01",      # Phase C: hold out 2022+ as the locked out-of-sample tail
    [string]$SimfinStart = "2021-07-01"     # SimFin free history starts ~2019
)

$ErrorActionPreference = "Continue"          # one failing command must not abort the whole tour
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "ERROR: venv python not found at $py" -ForegroundColor Red; exit 1 }

function Section($title) {
    Write-Host ""
    Write-Host ("=" * 78) -ForegroundColor Cyan
    Write-Host ("  " + $title) -ForegroundColor Cyan
    Write-Host ("=" * 78) -ForegroundColor Cyan
}
function Run($desc, [string[]]$cmdArgs) {
    Write-Host ""
    Write-Host ">>> $desc" -ForegroundColor Yellow
    Write-Host ("    python -m equity_research.cli " + ($cmdArgs -join ' ')) -ForegroundColor DarkGray
    & $py -m equity_research.cli @cmdArgs
}

Section "WHAT IS LOADED"
Run "Universe (fundamentals + prices loaded, and how many pass the liquid screen)" @('universe')

Section "v1 - SCREEN / VALUATION / FLAGS"
Run "Multi-factor percentile screen (top 10 by composite)" @('screen', '--top', '10')
Run "Full SecurityReport for $Ticker (valuation range, factor profile, quality flags, risks)" @('report', $Ticker)

Section "v2 - PORTFOLIO CONSTRUCTION + MACRO"
Run "Risk-based portfolio (HRP) with position/sector/liquidity caps" @('construct', '--method', 'hrp')
Run "Fractional-Kelly sizing from YOUR explicit edges (never invented)" `
    @('construct', '--method', 'kelly', '--edge', "$Ticker=0.06", '--edge', 'MSFT=0.04')
Run "Macro values known as-of today (ALFRED vintage; needs pull-macro + FRED_API_KEY)" @('macro')
Run "Macro rate/recession exposures for $Ticker (PIT OLS betas + NBER base rate)" @('exposures', $Ticker)

Section "v3 Phase A - WALK-FORWARD BACKTEST (free lane, INDICATIVE ONLY)"
Run "HRP backtest vs equal-weight benchmark, net of costs" `
    @('backtest', '--strategy', 'hrp', '--start', $FreeStart, '--lane', 'free')

Section "v3 Phase C - OUT-OF-SAMPLE DISCIPLINE + STRESS (free lane, INDICATIVE ONLY)"
Run "HRP backtest with a locked test set (in-sample vs out-of-sample tail) + PSR + regime stress" `
    @('backtest', '--strategy', 'hrp', '--start', $FreeStart, '--lane', 'free', '--locked-test-start', $LockStart)
Run "Deflated-Sharpe race across strategies (multiple-testing-correct; selects IN-SAMPLE only)" `
    @('backtest-compare', '--strategies', 'hrp', 'risk_parity', 'inverse_variance', 'equal_weight', `
      '--start', $FreeStart, '--lane', 'free', '--locked-test-start', $LockStart)

Section "v3 Phase B - SURVIVORSHIP-FREE BACKTEST (SimFin lane)"
$simfinDir = Join-Path $repo "data_simfin"
if (Test-Path $simfinDir) {
    $env:EQR_DATA_DIR = ".\data_simfin"
    Run "Survivorship-free backtest (equal_weight) - includes delisted names" `
        @('backtest', '--lane', 'simfin', '--strategy', 'equal_weight', '--start', $SimfinStart)
    Remove-Item Env:EQR_DATA_DIR
} else {
    Write-Host "`n  (skipped: .\data_simfin not set up yet.)" -ForegroundColor DarkGray
    Write-Host "  To enable: set EQR_DATA_DIR=.\data_simfin  then  pull-index-history  then  pull-simfin" -ForegroundColor DarkGray
}

Section "TOUR COMPLETE"
Write-Host "Every output above is research-only, not advice. Free-lane backtests are INDICATIVE;" -ForegroundColor Green
Write-Host "the simfin lane is best-effort survivorship-free (watch the 'Universe: N names' coverage line)." -ForegroundColor Green
