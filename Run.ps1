# PlanGrab — Windows launcher (no install, no admin).
#
# Runs entirely in place from this folder. It uses, in order of preference:
#   1. .\python\python.exe   — the bundled portable CPython (produced by the
#      build step; see README "Portable packaging"). This is the intended,
#      zero-dependency path for the locked-down target PC.
#   2. py -3 / python on PATH — a convenience fallback for dev machines.
#
# Vendored dependencies live in .\lib (pip --target). We add it to PYTHONPATH so
# nothing needs to be installed. Close this window to stop PlanGrab.

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $Here

# Make the bundled libs and the source tree importable without installation.
$env:PYTHONPATH = "$Here\lib;$Here"

function Resolve-Python {
    $bundled = Join-Path $Here "python\python.exe"
    if (Test-Path $bundled) { return $bundled }
    foreach ($cmd in @("py", "python")) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found) {
            if ($cmd -eq "py") { return @("py", "-3") }
            return @($found.Source)
        }
    }
    throw "No Python found. Expected .\python\python.exe (see README) or Python on PATH."
}

$py = Resolve-Python
Write-Host "Starting PlanGrab…"
& $py -m plangrab.web.server
