<#
  ui.ps1 - start (or stop) the local Plotly Dash web UI.

  START:  powershell -ExecutionPolicy Bypass -File scripts\ui.ps1
          Then open http://127.0.0.1:8050 in your browser.
          Leave the window open; press Ctrl+C in it (or close the window) to stop.

  STOP:   powershell -ExecutionPolicy Bypass -File scripts\ui.ps1 -Stop
          Kills whatever is serving on the port. Use this if the server is running in the
          background / a previous one was left orphaned and you can't Ctrl+C it.

  The UI reads the already-loaded data read-only; it does NOT pull data. To refresh data, run
  `pull-universe` from the CLI (see README), then restart the UI.
#>
param([switch]$Stop, [int]$Port = 8050)

$repo = Split-Path -Parent $PSScriptRoot      # scripts\ -> repo root
Set-Location $repo

if ($Stop) {
    $procs = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
             Select-Object -Expand OwningProcess -Unique
    if ($procs) {
        $procs | ForEach-Object { Stop-Process -Id $_ -Force }
        Write-Host "Stopped the UI on port $Port (PID $($procs -join ', '))." -ForegroundColor Green
    } else {
        Write-Host "Nothing is serving on port $Port." -ForegroundColor Yellow
    }
    exit 0
}

$py = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Host "venv python not found at $py" -ForegroundColor Red; exit 1 }

Write-Host "Starting the equity-research UI -> http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "Open that URL in your browser. Press Ctrl+C here (or close this window) to stop." -ForegroundColor DarkGray
& $py -m equity_research.ui
