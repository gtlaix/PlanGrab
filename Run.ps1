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
    # Returns @{ Exe = <path>; Args = <string[]> } — a consistent shape so the
    # call site can splat Args regardless of which branch matched. (Returning a
    # bare @("py","-3") array here and doing `& $py ...` at the call site is a
    # PowerShell trap: `&` stringifies a multi-element array into one command
    # name — literally "py -3" — instead of invoking `py` with `-3` as an
    # argument, so it fails with "term 'py -3' is not recognized" even though
    # `py` is right there on PATH.)
    $bundled = Join-Path $Here "python\python.exe"
    if (Test-Path $bundled) { return @{ Exe = $bundled; Args = @() } }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return @{ Exe = $py.Source; Args = @("-3") } }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return @{ Exe = $python.Source; Args = @() } }
    throw "No Python found. Expected .\python\python.exe (see README) or Python on PATH (either 'py' or 'python')."
}

$resolved = Resolve-Python
$pyExe = $resolved.Exe
$pyArgs = $resolved.Args
Write-Host "Starting PlanGrab…"
& $pyExe @pyArgs -m plangrab.web.server
