# mememage.art/install.ps1 — one-line Windows install for the Mememage app.
#
#   irm https://mememage.art/install.ps1 | iex
#
# PowerShell downloads don't carry the "mark-of-the-web" that a browser download
# does, so this skips the SmartScreen "unrecognized app" prompt. No signing cert
# needed. The app bundles the mint server and the full web UI — no Python.
#
# Want the library for your own code instead?   pip install mememage
$ErrorActionPreference = 'Stop'

$repo = 'sememtac/mememage-provenance'
$url  = "https://github.com/$repo/releases/latest/download/Mememage-Provenance-Windows.exe"
$dir  = Join-Path $env:LOCALAPPDATA 'Mememage'
$exe  = Join-Path $dir 'Mememage.exe'

function Note($m) { Write-Host "> $m" -ForegroundColor Magenta }

New-Item -ItemType Directory -Force -Path $dir | Out-Null

Note 'Downloading Mememage for Windows...'
try {
    Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
} catch {
    Write-Host "Download failed. Get it manually: https://github.com/$repo/releases/latest" -ForegroundColor Red
    return
}

# Belt-and-suspenders: clear the mark-of-the-web if anything set it.
Unblock-File -Path $exe -ErrorAction SilentlyContinue

# Start Menu shortcut, so it's easy to relaunch later.
try {
    $lnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'Mememage.lnk'
    $ws  = New-Object -ComObject WScript.Shell
    $s   = $ws.CreateShortcut($lnk)
    $s.TargetPath = $exe
    $s.Save()
} catch { }

Note "Installed -> $exe"
Start-Process $exe
Note 'Launched. First run opens the dashboard in your browser.'
