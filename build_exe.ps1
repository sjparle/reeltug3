Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "python is not available on PATH."
}

$args = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "ReelTug",
    "--icon", "reeltug_icon.ico"
)

$dataFiles = @(
    "gui_main.ui;.",
    "gui_settings.ui;.",
    "gui_render.ui;.",
    "gui_queue.ui;.",
    "gui/reeltug_icon.png;gui",
    "reeltug_icon.ico;."
)

foreach ($data in $dataFiles) {
    $args += @("--add-data", $data)
}

$binaryFiles = @(
    "ffmpeg.exe",
    "ffprobe.exe"
)

foreach ($binary in $binaryFiles) {
    if (Test-Path $binary) {
        $args += @("--add-binary", "$binary;.")
    } else {
        Write-Host "Skipping optional binary: $binary (not found in project root)"
    }
}

$args += "run.py"

Write-Host "Building ReelTug exe with PyInstaller..."
python @args
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Build complete."
Write-Host "Executable: $projectRoot\dist\ReelTug.exe"
