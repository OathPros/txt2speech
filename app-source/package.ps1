$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$python = "C:\KCS-Voiceover\.venv\Scripts\python.exe"
$sourceModels = "C:\KCS-Voiceover"
$runtimeSource = Join-Path $root "release-assets\runtime"
$appName = "KokoroVoiceoverStudio"
$distApp = Join-Path $root "dist\$appName"

if (-not (Test-Path $python)) {
    throw "Kokoro Python environment not found: $python"
}
foreach ($file in @("kokoro-v1.0.onnx", "voices-v1.0.bin")) {
    if (-not (Test-Path (Join-Path $sourceModels $file))) {
        throw "Required installed model asset not found: $(Join-Path $sourceModels $file)"
    }
}
foreach ($backend in @("cpu", "cuda")) {
    $runtimeDirectory = Join-Path $runtimeSource $backend
    if (-not (Test-Path (Join-Path $runtimeDirectory "llama-server.exe"))) {
        throw "The pinned llama.cpp $backend runtime is missing: $runtimeDirectory"
    }
}

& $python -m PyInstaller --noconfirm --clean --windowed --onedir `
    --name $appName `
    --collect-all kokoro_onnx `
    --collect-all onnxruntime `
    --collect-all phonemizer `
    --collect-all espeakng_loader `
    --collect-all soundfile `
    --collect-all ttkbootstrap `
    --collect-all winrt `
    (Join-Path $root "studio.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

$modelsDir = Join-Path $distApp "models"
New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null
Copy-Item (Join-Path $sourceModels "kokoro-v1.0.onnx") $modelsDir
Copy-Item (Join-Path $sourceModels "voices-v1.0.bin") $modelsDir
foreach ($backend in @("cpu", "cuda")) {
    $destination = Join-Path $distApp "runtime\$backend"
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    Get-ChildItem (Join-Path $runtimeSource $backend) -File |
        Where-Object { $_.Extension -eq ".dll" -or $_.Name -eq "llama-server.exe" } |
        Copy-Item -Destination $destination -Force
    $openmpLicense = Join-Path $runtimeSource "$backend\LICENSE-LLVM-OpenMP"
    if (Test-Path $openmpLicense) {
        Copy-Item $openmpLicense $destination -Force
    }
}
foreach ($document in @("README.md", "RELEASE_NOTES.md", "THIRD_PARTY_NOTICES.md", "DEPENDENCIES.json")) {
    Copy-Item (Join-Path $root $document) $distApp -Force
}
New-Item -ItemType Directory -Force -Path (Join-Path $root "dist\installer") | Out-Null

$compilerCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$compiler = $compilerCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $compiler) {
    throw "Inno Setup 6 is required to create the installer. Install JRSoftware.InnoSetup, then rerun package.ps1."
}

Push-Location $root
try {
    & $compiler ".\installer.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

Write-Host "Application: $distApp\$appName.exe"
$standardInstaller = Join-Path $root "dist\installer\Text2Speech-Setup-1.1.0.exe"
Write-Host "Installer:   $standardInstaller"
Write-Host "Release gate: publish the installer and matching checksum as a stable OathPros/txt2speech GitHub Release, then verify its workflow updates dxluke.com/text2speech."

$modelPath = Join-Path $root "release-assets\models\Qwen3.5-9B-Q4_K_M.gguf"
$offlineReady = $false
if (Test-Path $modelPath) {
    & $python -c "from pathlib import Path; from qwen_runtime import verify_model; p=Path(r'$modelPath'); raise SystemExit(0 if verify_model(p) else 1)"
    $offlineReady = ($LASTEXITCODE -eq 0)
}
if ($offlineReady) {
    & $compiler "/DOfflineDistribution=1" ".\installer.iss"
    if ($LASTEXITCODE -ne 0) {
        throw "Offline Inno Setup build failed with exit code $LASTEXITCODE."
    }
    $offlineInstaller = Join-Path $root "dist\offline\KokoroVoiceoverStudio-Offline-1.1.0.exe"
    $offlineZip = Join-Path $root "dist\KokoroVoiceoverStudio-Offline-1.1.0.zip"
    @'
import sys, zipfile
from pathlib import Path
installer = Path(sys.argv[1])
archive = Path(sys.argv[2])
with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
    for path in sorted(installer.parent.glob(installer.stem + "*")):
        if path.is_file():
            zf.write(path, path.name)
    for name in ("README.md", "RELEASE_NOTES.md", "THIRD_PARTY_NOTICES.md"):
        path = installer.parent.parent.parent / name
        if path.is_file():
            zf.write(path, name)
'@ | & $python - $offlineInstaller $offlineZip
    if ($LASTEXITCODE -ne 0) {
        throw "Offline ZIP creation failed with exit code $LASTEXITCODE."
    }
    Write-Host "Offline package: $offlineZip"
}
else {
    Write-Warning "Offline package not built: the pinned Qwen GGUF is missing or failed checksum verification."
}
