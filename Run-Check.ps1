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
& $py -m plangrab.selftest
Write-Host ""
Read-Host "Press Enter to close"   # keep the window open when double-clicked
