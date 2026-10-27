# Local personal build. The bundled RU/EN word lists require a separate
# redistribution-rights review before any public release.
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
$oldPythonPath = $env:PYTHONPATH
try {
    $profilesRoot = Join-Path $PSScriptRoot 'dist\profiles'
    $runningTarget = Get-Process -Name TypeBridge -ErrorAction SilentlyContinue | Where-Object {
        -not $_.Path -or $_.Path.StartsWith(
            ([IO.Path]::GetFullPath($profilesRoot).TrimEnd('\') + '\'),
            [StringComparison]::OrdinalIgnoreCase)
    }
    if ($runningTarget) {
        throw 'Close running TypeBridge Public/Diagnostics EXEs via Exit in the tray before rebuilding.'
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
    # Both from the same commit. The old dist\TypeBridge.exe and local profile
    # files/flags remain untouched. Only separate profile folders are updated.
    & $python build_profiles.py --output-dir $profilesRoot
    if ($LASTEXITCODE -ne 0) { throw "TypeBridge dual-profile build failed" }
    foreach ($profile in @('Public','Diagnostics')) {
        $exe = Join-Path $profilesRoot "$profile\TypeBridge.exe"
        if (-not (Test-Path -LiteralPath $exe)) { throw "Missing $profile EXE" }
        Write-Host "$profile EXE: $exe"
        Write-Host "$profile manifest: $(Join-Path $profilesRoot "$profile\BUILD-PROFILE.json")"
    }

}
finally {
    $env:PYTHONPATH = $oldPythonPath
    Pop-Location
}
