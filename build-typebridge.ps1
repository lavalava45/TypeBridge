# Local personal build. The bundled RU/EN word lists require a separate
# redistribution-rights review before any public release.
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
$oldPythonPath = $env:PYTHONPATH
try {
    $canonicalExe = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'dist\TypeBridge.exe'))
    $runningCanonical = Get-Process -Name TypeBridge -ErrorAction SilentlyContinue | Where-Object {
        -not $_.Path -or [String]::Equals($_.Path, $canonicalExe, [StringComparison]::OrdinalIgnoreCase)
    }
    if ($runningCanonical) {
        throw 'Close the running dist\TypeBridge.exe via Exit in the tray before rebuilding it.'
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
    & $python -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name TypeBridge --distpath (Join-Path $PSScriptRoot 'dist') --add-data 'dictionary_packs;dictionary_packs' typebridge_app.py
    if ($LASTEXITCODE -ne 0) { throw "TypeBridge EXE build failed" }
    # Update only generated public data. Never overwrite user-maintained
    # excludes, custom words, replacement rules, or other private data.
    New-Item -ItemType Directory -Path "dist\data" -Force | Out-Null
    $managed = @('ru.txt','en.txt','he.txt',
        'confident_ru.txt','confident_en.txt','confident_he.txt',
        'frequency_ru.tsv','frequency_en.tsv','frequency_he.tsv',
        'FREQUENCY-SOURCES.json','FREQUENCY-DATA-LICENSE.txt',
        'RU-SOURCES.json','RU-DATA-LICENSE.txt',
        'SOURCES.json','DATA-LICENSE.txt','HEBREW-SOURCE.md')
    foreach ($name in $managed) {
        $from = Join-Path 'data' $name
        if (Test-Path -LiteralPath $from) {
            Copy-Item -LiteralPath $from -Destination (Join-Path 'dist\data' $name) -Force
        }
    }
    foreach ($file in Get-ChildItem -LiteralPath 'data' -File) {
        $target = Join-Path 'dist\data' $file.Name
        if (-not (Test-Path -LiteralPath $target)) {
            Copy-Item -LiteralPath $file.FullName -Destination $target
        }
    }
    if (-not (Test-Path -LiteralPath "dist\typebridge-settings.json")) {
        Copy-Item -LiteralPath "typebridge-settings.json" -Destination "dist\typebridge-settings.json"
    }
    # The sibling BUILD-INFO belongs to THIS exact EXE. Recreate it on every
    # successful build; otherwise PC comparisons pick up obsolete SHA-256.
    $fingerprints = [ordered]@{}
    foreach ($name in @('ru.txt','en.txt','he.txt',
                         'confident_ru.txt','confident_en.txt','confident_he.txt',
                         'he_custom.txt','exclude.txt')) {
        $file = Join-Path 'dist\data' $name
        if (Test-Path -LiteralPath $file) {
            $fingerprints[$name] = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $source = [ordered]@{}
    foreach ($name in @('typebridge_app.py','engine.py','early_native_edit.py',
                         'early_scintilla.py','input_transport.py','clipboard_tools.py','password_fields.py',
                         'native_transaction_queue.py','word_trace.py','browser_readback.py',
                         'dictionary_diagnostics.py','comparison_diagnostics.py',
                         'windows_shortcuts.py','tray_win32.py')) {
        $source[$name] = (Get-FileHash -LiteralPath $name -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $profilePath = 'dist\TypeBridge-data\settings.json'
    $profileHash = $null
    if (Test-Path -LiteralPath $profilePath) {
        $profileHash = (Get-FileHash -LiteralPath $profilePath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $manifest = [ordered]@{
        format = 1
        built_utc = [DateTime]::UtcNow.ToString('o')
        exe_sha256 = (Get-FileHash -LiteralPath 'dist\TypeBridge.exe' -Algorithm SHA256).Hash.ToLowerInvariant()
        source_sha256 = $source
        dictionary_sha256 = $fingerprints
        profile_settings_sha256 = $profileHash
        profile_settings_note = 'Fingerprint only; no user settings contents in this manifest'
        recognition = 'word validity, confidence, exclusions, configured priority (no numeric frequencies)'
    }
    $manifestPath = Join-Path 'dist' 'BUILD-INFO.json'
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Write-Host "EXE: $PSScriptRoot\dist\TypeBridge.exe"
    Write-Host "BUILD-INFO: $PSScriptRoot\$manifestPath"
}
finally {
    $env:PYTHONPATH = $oldPythonPath
    Pop-Location
}
