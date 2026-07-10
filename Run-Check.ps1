# PlanGrab — install self-test (no install, no admin).
#
# Run this FIRST on a new/locked-down PC to confirm PlanGrab will work here:
# it checks the bundled Python, dependencies, the folder picker, the data, and
# whether this machine can actually reach a council planning site (proxies and
# firewalls often block outbound HTTPS). Right-click -> Run with PowerShell.

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $Here
$env:PYTHONPATH = "$Here\lib;$Here"

function Resolve-Python {
    # See Run.ps1's Resolve-Python for why this returns a consistent
    # @{Exe=...; Args=...} shape instead of a bare array to splat.
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
& $pyExe @pyArgs -m plangrab.selftest
Write-Host ""
Read-Host "Press Enter to close"   # keep the window open when double-clicked
