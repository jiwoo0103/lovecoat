$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'dist-releases'))
$releasePointer = Join-Path $releaseRoot 'latest.txt'
$relativeExe = (Get-Content -LiteralPath $releasePointer -Raw).Trim()
$sourceExe = [IO.Path]::GetFullPath((Join-Path $projectRoot $relativeExe))
if (-not $sourceExe.StartsWith($releaseRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The release path must stay inside dist-releases.'
}
if (-not (Test-Path -LiteralPath $sourceExe -PathType Leaf)) {
    throw 'Build the app before copying it to the desktop.'
}
$sourceFolder = Split-Path -Parent $sourceExe
$desktopFolder = [Environment]::GetFolderPath('Desktop')
if ([string]::IsNullOrWhiteSpace($desktopFolder)) {
    throw 'The Windows Desktop folder could not be found.'
}
$installFolder = Join-Path $desktopFolder '외투 작업실 작업'
New-Item -ItemType Directory -Path $installFolder -Force | Out-Null
$taskHashes = @{}
Get-ChildItem -LiteralPath $installFolder -Filter '*.coat.json' -File | ForEach-Object {
    $taskHashes[$_.FullName] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
}
# Copy only the build's files. Saved work is never a copy/delete target.
Get-ChildItem -LiteralPath $sourceFolder -Force | Where-Object { $_.FullName -ne $sourceExe } | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $installFolder -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'README.md') -Destination (Join-Path $installFolder '사용법.md') -Force
$installedExe = Join-Path $installFolder '외투 작업실.exe'
Copy-Item -LiteralPath $sourceExe -Destination $installedExe -Force
foreach ($taskFile in $taskHashes.Keys) {
    if ((Get-FileHash -LiteralPath $taskFile -Algorithm SHA256).Hash -ne $taskHashes[$taskFile]) {
        throw "A saved task changed during the copy: $taskFile"
    }
}
Write-Output $installedExe
