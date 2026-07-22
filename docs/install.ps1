# mememage.art/install.ps1 — one-line Windows install for the Mememage app.
#
#   irm https://mememage.art/install.ps1 | iex
#
# PowerShell downloads don't carry the "mark-of-the-web" that a browser download
# does, so this skips the SmartScreen "unrecognized app" prompt. No signing cert
# needed. The app bundles the mint server and the full web UI — no Python.
#
# The download is VERIFIED against the release SHA256SUMS.txt before it runs.
# Want the library for your own code instead?   pip install mememage
$ErrorActionPreference = 'Stop'

$repo    = 'sememtac/mememage-provenance'
$asset   = 'Mememage-Provenance-Windows.exe'
$base    = "https://github.com/$repo/releases/latest/download"
$latest  = "https://github.com/$repo/releases/latest"
$dir     = Join-Path $env:LOCALAPPDATA 'Mememage'
$exe     = Join-Path $dir 'Mememage.exe'

function Note($m) { Write-Host "> $m" -ForegroundColor Magenta }
function Die($m)  { Write-Host "x $m" -ForegroundColor Red; exit 1 }

$tmp = Join-Path $env:TEMP ('mememage-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$dl   = Join-Path $tmp $asset
$sums = Join-Path $tmp 'SHA256SUMS.txt'

Note 'Downloading Mememage for Windows...'
try {
    Invoke-WebRequest -Uri "$base/$asset" -OutFile $dl -UseBasicParsing
} catch {
    Die "Download failed. Get it manually: $latest"
}

# Verify against the release SHA256SUMS.txt. Fail closed: a missing checksum
# file, an unlisted asset, or a mismatch all abort the install.
Note 'Verifying checksum...'
try {
    Invoke-WebRequest -Uri "$base/SHA256SUMS.txt" -OutFile $sums -UseBasicParsing
} catch {
    Die "Could not fetch the checksum file (SHA256SUMS.txt). Aborting for safety. Verify manually: $latest"
}
$want = $null
foreach ($line in Get-Content $sums) {
    if ($line -match '^\s*([0-9A-Fa-f]{64})\s+\*?(.+?)\s*$' -and $matches[2] -eq $asset) {
        $want = $matches[1].ToLower(); break
    }
}
if (-not $want) { Die "No checksum published for $asset. Aborting. See: $latest" }
$actual = (Get-FileHash -Path $dl -Algorithm SHA256).Hash.ToLower()
if ($want -ne $actual) {
    Write-Host "x Checksum mismatch - do NOT run it." -ForegroundColor Red
    Write-Host "  expected $want"
    Write-Host "  got      $actual"
    exit 1
}
Note 'Checksum verified (SHA-256).'

New-Item -ItemType Directory -Force -Path $dir | Out-Null
Move-Item -Force $dl $exe
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue

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
