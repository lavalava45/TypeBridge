# Local personal build. The bundled RU/EN word lists require a separate
# redistribution-rights review before any public release.
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
$oldPythonPath = $env:PYTHONPATH
try {
    $runningTarget = Get-Process -Name TypeBridge -ErrorAction SilentlyContinue
    if ($runningTarget) {
        throw 'Close running TypeBridge via Exit in the tray before rebuilding.'
    }
    $deps = Join-Path $PSScriptRoot ".build-deps"
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $python = (& $launcher.Source -3.12 -c "import sys; print(sys.executable)")
    } else {
        $python = (Get-Command python.exe -ErrorAction Stop).Source
    }
    if (-not $python -or -not (Test-Path -LiteralPath $python)) {
        throw "Python was not found; install Python 3.12 and requirements-build.txt"
    }
    if (Test-Path (Join-Path $deps "PyInstaller")) {
        $env:PYTHONPATH = $deps
    }
    # Local working-source build: main Public EXE and existing Diagnostics profile.
    # The clean-commit versioned release builder remains a separate workflow.
    & $python build_local.py
    if ($LASTEXITCODE -ne 0) { throw "Local Public/Diagnostics TypeBridge build failed" }

}
finally {
    $env:PYTHONPATH = $oldPythonPath
    Pop-Location
}
