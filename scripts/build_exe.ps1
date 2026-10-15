param(
    [string]$Name = "dingding-attendance",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path
$releaseDir = Join-Path $repoRoot (Join-Path "dist" $Name)
$buildDir = Join-Path $repoRoot (Join-Path "build" $Name)

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

    New-Item -ItemType Directory -Force -Path $releaseDir, $buildDir | Out-Null
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --console `
        --name $Name `
        --distpath $releaseDir `
        --workpath $buildDir `
        --specpath $buildDir `
        --paths $repoRoot `
        (Join-Path $repoRoot "main.py")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed with exit code $LASTEXITCODE"
    }

    foreach ($legacyConfigName in @("config.toml", "config.example.toml")) {
        $legacyConfigPath = Join-Path $releaseDir $legacyConfigName
        if (Test-Path -LiteralPath $legacyConfigPath -PathType Leaf) {
            Remove-Item -LiteralPath $legacyConfigPath -Force
        }
    }
    foreach ($configName in @("config.csv", "config.example.csv", "公司放假.csv", "公司放假.example.csv")) {
        Copy-Item (Join-Path $repoRoot $configName) (Join-Path $releaseDir $configName) -Force
    }
    Copy-Item (Join-Path $PSScriptRoot "run_attendance.bat") (Join-Path $releaseDir "run_attendance.bat") -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $releaseDir "input"), (Join-Path $releaseDir "output") | Out-Null

    Write-Host "Build complete: $releaseDir"
    Write-Host "Copy the entire directory to a Windows machine without Python."
}
finally {
    Pop-Location
}
