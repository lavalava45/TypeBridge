# Local personal build. The bundled RU/EN word lists require a separate
# redistribution-rights review before any public release.
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
$oldPythonPath = $env:PYTHONPATH
try {
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
    & $python -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name TypeBridge --add-data 'data;data' --add-data 'dictionary_packs;dictionary_packs' typebridge_app.py
    if ($LASTEXITCODE -ne 0) { throw "TypeBridge EXE build failed" }
    Copy-Item -LiteralPath "typebridge-settings.json" `
        -Destination "dist\typebridge-settings.json" -Force
    Write-Host "EXE: $PSScriptRoot\dist\TypeBridge.exe"
}
finally {
    $env:PYTHONPATH = $oldPythonPath
    Pop-Location
}
