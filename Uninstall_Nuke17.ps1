[CmdletBinding()]
param([string]$NukeUserDir = (Join-Path $env:USERPROFILE '.nuke'))

$ErrorActionPreference = 'Stop'
$NukeUserDir = [Environment]::ExpandEnvironmentVariables($NukeUserDir)
if (-not [System.IO.Path]::IsPathRooted($NukeUserDir)) { $NukeUserDir = Join-Path (Get-Location).Path $NukeUserDir }
$initPath = Join-Path ([System.IO.Path]::GetFullPath($NukeUserDir)) 'init.py'
if (-not (Test-Path -LiteralPath $initPath -PathType Leaf)) {
    Write-Host 'No init.py found. No changes made.'
    exit 0
}

# ISO-8859-1 round-trips every byte, preserving unrelated encodings and newlines.
$byteEncoding = [System.Text.Encoding]::GetEncoding(28591)
$oldBytes = [System.IO.File]::ReadAllBytes($initPath)
$oldText = $byteEncoding.GetString($oldBytes)
$markerPattern = '(?s)\r?\n# >>> UNISHARP_NUKE_BOOTSTRAP >>>\r?\n.*?# <<< UNISHARP_NUKE_BOOTSTRAP <<<\r?\n?'
$newText = [regex]::Replace($oldText, $markerPattern, '')
if ($newText.Contains('# >>> UNISHARP_NUKE_BOOTSTRAP >>>') -or $newText.Contains('# <<< UNISHARP_NUKE_BOOTSTRAP <<<')) {
    throw "Incomplete UniSHARP markers in $initPath. Original file retained."
}
if ($newText -eq $oldText) {
    Write-Host 'UniSHARP startup block is not installed. No changes made.'
    exit 0
}

$backupPath = $initPath + '.unisharp-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
if (Test-Path -LiteralPath $backupPath) { $backupPath += '-' + [guid]::NewGuid().ToString('N') }
Copy-Item -LiteralPath $initPath -Destination $backupPath
[System.IO.File]::WriteAllBytes($initPath, $byteEncoding.GetBytes($newText))
Write-Host "UniSHARP startup block removed from: $initPath"
Write-Host "Backup: $backupPath"
Write-Host 'Project files, model, Python environment, and generated outputs were retained.'
Write-Host 'Restart Nuke to finish unloading the menu.'
