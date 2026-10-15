param(
    [string]$Name = "dingding-attendance",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
$outputRoot = Join-Path $repoRoot "build"
$releaseDir = Join-Path $outputRoot $Name
$buildDir = Join-Path $outputRoot (Join-Path "pyinstaller" $Name)

if (-not (Test-Path (Join-Path $repoRoot "main.py"))) {
    throw "Project entry point not found: $repoRoot\main.py"
}

Push-Location $repoRoot
try {
    # Install dependencies on the build machine only.
    & $Python -m pip install -r (Join-Path $repoRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Python dependency installation failed with exit code $LASTEXITCODE"
    }

    & $Python -X utf8 -c "from pathlib import Path; from attendance_analyzer.config import AppConfig; AppConfig.load(Path('config.json')); import tkinter; print('JSON configuration and Tk runtime OK')"
    if ($LASTEXITCODE -ne 0) {
        throw "Build preflight failed. Check the JSON configuration and install Python with Tcl/Tk support."
    }
    & $Python -X utf8 -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) {
        throw "Tests failed; release packaging stopped."
    }

    New-Item -ItemType Directory -Force -Path $releaseDir, $buildDir | Out-Null
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name $Name `
        --distpath $releaseDir `
        --workpath $buildDir `
        --specpath $buildDir `
        --paths $repoRoot `
        (Join-Path $repoRoot "main.py")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE"
    }

    foreach ($legacyConfigName in @("config.toml", "config.example.toml", "config.csv", "公司放假.csv", "config.example.csv", "公司放假.example.csv", "run_attendance.bat", "配置模板/config.csv", "配置模板/公司放假.csv")) {
        $legacyConfigPath = Join-Path $releaseDir $legacyConfigName
        if (Test-Path -LiteralPath $legacyConfigPath -PathType Leaf) {
            Remove-Item -LiteralPath $legacyConfigPath -Force
        }
    }
    $templatesDir = Join-Path $releaseDir "配置模板"
    New-Item -ItemType Directory -Force -Path $templatesDir | Out-Null
    Copy-Item (Join-Path $repoRoot "config.json") (Join-Path $releaseDir "config.json") -Force
    Copy-Item (Join-Path $repoRoot "config.example.json") (Join-Path $templatesDir "config.json") -Force
    Copy-Item (Join-Path $repoRoot "docs\HR使用说明.txt") (Join-Path $releaseDir "使用说明.txt") -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $releaseDir "input"), (Join-Path $releaseDir "output"), (Join-Path $releaseDir "logs") | Out-Null

    & $Python -X utf8 (Join-Path $PSScriptRoot "package_release.py") $releaseDir
    if ($LASTEXITCODE -ne 0) {
        throw "ZIP packaging failed with exit code $LASTEXITCODE"
    }

    Write-Host "Build complete: $releaseDir"
    Write-Host "Send this ZIP to HR: $releaseDir.zip"
    Write-Host "Extract the entire ZIP, then double-click $Name.exe. No Python is required."
}
finally {
    Pop-Location
}
