# Research OS one-click installer for Windows (Windows execution: NOT RUN on the dev box; verify with selftest.ps1).
# Installs into $InstallDir (default D:\AI\ResearchOS): app\ (code + venv), data\ (your research data).
# Needs internet once for uv + Python 3.12 + Python packages (exact versions pinned by uv.lock). No admin rights.
param(
  [string]$InstallDir = 'D:\AI\ResearchOS',
  [switch]$Lan,          # also configure HTTPS for iPad access on this computer's LAN address
  [switch]$NoStart,
  [switch]$NoShortcuts,  # the setup .exe creates (and uninstalls) its own shortcuts
  [string]$LogFile = ''  # full transcript, e.g. <InstallDir>\install.log (the setup .exe always sets it)
)
$ErrorActionPreference = 'Stop'
if ($LogFile) { New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogFile) | Out-Null; Start-Transcript -Path $LogFile -Append | Out-Null }
$Src = $PSScriptRoot
$App = Join-Path $InstallDir 'app'
$Data = Join-Path $InstallDir 'data'
Write-Host "Installing Research OS into $InstallDir"
New-Item -ItemType Directory -Force -Path $App, $Data | Out-Null

# 1. update = stop a running server, replace the code directories (keep app\.venv); data\ is never touched
$env:RESEARCHOS_DATA_DIR = $Data   # so stop.ps1 finds this install's pid files
$stop = Join-Path $App 'scripts\stop.ps1'
if (Test-Path $stop) { try { & $stop } catch { Write-Host "note: could not stop a running server: $_" } }
$Items = 'services','workers','templates','packages','plugins','examples','scripts','tests','apps','pyproject.toml','uv.lock','PACKAGE_MANIFEST.json','docs'
foreach ($item in $Items) {
  $old = Join-Path $App $item
  if (Test-Path $old) { Remove-Item -Recurse -Force $old }
  $from = Join-Path $Src $item
  if (Test-Path $from) { Copy-Item -Recurse -Force $from $App }
}

# 2. uv (official installer) + managed Python 3.12 + locked dependencies
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host 'Installing uv (https://astral.sh/uv) for the current user...'
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Push-Location $App
try {
  uv python install 3.12
  uv sync --frozen --python 3.12 --extra experiments --extra plugins
  if ($LASTEXITCODE -ne 0) { throw 'dependency installation failed (see output above)' }
} finally { Pop-Location }

# 3. data dir + config
[Environment]::SetEnvironmentVariable('RESEARCHOS_DATA_DIR', $Data, 'User')
$env:RESEARCHOS_DATA_DIR = $Data
$cfgDir = Join-Path $Data 'config'
New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
$cfg = Join-Path $cfgDir 'researchos.toml'
if (-not (Test-Path $cfg)) {
  $relayHint = "# Optional relay (docs/OPS.md#model-relay): uncomment both lines; key goes in the RESEARCHOS_RELAY_API_KEY env var`n" +
               "# relay_base_url = `"https://relay.example.com/v1`"`n# relay_for = [`"anthropic`", `"openai`"]`n"
  Set-Content -Path $cfg -Value ("host = `"127.0.0.1`"`nport = 8443`n" + $relayHint) -Encoding UTF8
}
$py = Join-Path $App '.venv\Scripts\python.exe'
& $py -m researchos.app.cli migrate

# 4. optional LAN HTTPS for iPad
if ($Lan) {
  $ip = (& $py -m researchos.app.cli lan-urls | Select-Object -First 1) -replace '^https?://','' -replace ':\d+$',''
  if (-not $ip) { throw 'no LAN address found; connect to your network or use Tailscale (docs/OPS.md#ipad)' }
  & $py -m researchos.app.cli make-cert $ip
  $tls = Join-Path $cfgDir 'tls'
  Set-Content -Path $cfg -Encoding UTF8 -Value @"
host = "$ip"
port = 8443
tls_cert_file = "$($tls -replace '\\','\\')\\server.crt"
tls_key_file = "$($tls -replace '\\','\\')\\server.key"
public_origins = ["https://${ip}:8443"]
"@
  Write-Host "iPad: open https://${ip}:8443/ca.crt in Safari, install + trust the profile (docs/OPS.md#ipad)."
}

& $py -m researchos.app.cli doctor

# 5. "Research OS" shortcuts on the Desktop and in the Start Menu (hidden launcher, opens the browser)
if (-not $NoShortcuts) {
  $sh = New-Object -ComObject WScript.Shell
  $menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Research OS'
  New-Item -ItemType Directory -Force -Path $menu | Out-Null
  foreach ($lnk in (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Research OS.lnk'), (Join-Path $menu 'Research OS.lnk')) {
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $s.Arguments = '"' + (Join-Path $App 'scripts\launch.vbs') + '"'
    $s.WorkingDirectory = $App
    $ico = Join-Path $App 'scripts\researchos.ico'
    if (Test-Path $ico) { $s.IconLocation = $ico }
    $s.Save()
  }
}
if (-not $NoStart) { & (Join-Path $App 'scripts\launch.ps1') }
Write-Host "Done. Start later with the 'Research OS' shortcut (or $App\scripts\start.ps1)."
if ($LogFile) { Stop-Transcript | Out-Null }
