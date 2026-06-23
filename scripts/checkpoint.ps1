<#
  checkpoint.ps1 - verify the project, then commit + push the current state.

  Runs the full test suite; only if it is GREEN does it stage everything, commit, and push to
  GitHub. Safe to run anytime: .gitignore excludes .env (your API keys) and all data
  (data/, data_simfin/, *.parquet/*.duckdb/*.csv), so secrets and market data are never committed.

  Usage (from anywhere):
    powershell -ExecutionPolicy Bypass -File scripts\checkpoint.ps1 "your commit message"
    scripts\checkpoint.ps1                       # uses a timestamped default message
    scripts\checkpoint.ps1 "msg" -NoPush         # commit locally only, don't push
#>
param(
    [Parameter(Position = 0)]
    [string]$Message = "checkpoint: $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
    [switch]$NoPush
)

$repo = Split-Path -Parent $PSScriptRoot      # scripts\ -> repo root
Set-Location $repo

$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "ERROR: venv python not found at $py" -ForegroundColor Red; exit 1 }

Write-Host "== Running test suite ==" -ForegroundColor Cyan
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Host "Tests FAILED - not committing." -ForegroundColor Red; exit 1 }

Write-Host "`n== Staging changes ==" -ForegroundColor Cyan
git add -A
if (-not (git status --porcelain)) {
    Write-Host "Working tree clean - nothing to commit." -ForegroundColor Yellow
    exit 0
}
git status --short

git commit -m $Message
if ($LASTEXITCODE -ne 0) { Write-Host "Commit failed." -ForegroundColor Red; exit 1 }

if ($NoPush) { Write-Host "`nCommitted locally (push skipped)." -ForegroundColor Green; exit 0 }

Write-Host "`n== Pushing to origin/main ==" -ForegroundColor Cyan
git push origin main
if ($LASTEXITCODE -ne 0) { Write-Host "Push failed (commit is saved locally)." -ForegroundColor Red; exit 1 }
Write-Host "`nDone: verified, committed, and pushed." -ForegroundColor Green
