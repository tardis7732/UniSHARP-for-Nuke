[CmdletBinding()]
param(
    [string]$PythonExe = '',
    [string]$Checkpoint = '',
    [string]$OutputRoot = '',
    [string]$NukeUserDir = (Join-Path $env:USERPROFILE '.nuke'),
    [switch]$CreateEnvironment,
    [string]$BasePythonExe = '',
    [switch]$SkipRuntimeCheck
)

$ErrorActionPreference = 'Stop'
$repoRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$pluginDir = Join-Path $repoRoot 'nuke'
$configPath = Join-Path $pluginDir 'config.json'
$utf8 = New-Object System.Text.UTF8Encoding($false)
$byteEncoding = [System.Text.Encoding]::GetEncoding(28591)
$markerPattern = '(?s)\r?\n# >>> UNISHARP_NUKE_BOOTSTRAP >>>\r?\n.*?# <<< UNISHARP_NUKE_BOOTSTRAP <<<\r?\n?'

function Resolve-AbsolutePath([string]$Path) {
    $expanded = [Environment]::ExpandEnvironmentVariables($Path)
    if (-not [System.IO.Path]::IsPathRooted($expanded)) {
        $expanded = Join-Path (Get-Location).Path $expanded
    }
    return [System.IO.Path]::GetFullPath($expanded)
}

function Assert-LastExit([string]$Operation) {
    if ($LASTEXITCODE -ne 0) { throw "$Operation failed (exit $LASTEXITCODE)." }
}

function Backup-ExistingFile([string]$Path) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $backupPath = $Path + '.unisharp-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
        if (Test-Path -LiteralPath $backupPath) { $backupPath += '-' + [guid]::NewGuid().ToString('N') }
        Copy-Item -LiteralPath $Path -Destination $backupPath
        Write-Host "Backup: $backupPath"
    }
}

foreach ($requiredFile in @('unisharp_nuke.py', 'worker.py', 'init.py', 'menu.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $pluginDir $requiredFile) -PathType Leaf)) {
        throw "Missing plugin file: $requiredFile"
    }
}

$previousConfig = [ordered]@{}
if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    $parsedConfig = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($property in $parsedConfig.PSObject.Properties) {
        $previousConfig[$property.Name] = $property.Value
    }
}

if ($CreateEnvironment) {
    if ($PythonExe) { throw 'Use -BasePythonExe for -CreateEnvironment; -PythonExe selects an existing runtime.' }
    if (-not $BasePythonExe) { throw '-CreateEnvironment requires -BasePythonExe pointing to a 64-bit Python 3.11 executable.' }
    $BasePythonExe = Resolve-AbsolutePath $BasePythonExe
    if (-not (Test-Path -LiteralPath $BasePythonExe -PathType Leaf)) { throw "Python not found: $BasePythonExe" }
    & $BasePythonExe -c "import struct, sys; assert sys.version_info[:2] == (3, 11) and struct.calcsize('P') == 8, '64-bit Python 3.11 is required'"
    Assert-LastExit 'Base Python version check'
    $venvDir = Join-Path $repoRoot '.venv-nuke'
    if (Test-Path -LiteralPath $venvDir) { throw "Environment already exists: $venvDir. Use -PythonExe to reuse it." }
    & $BasePythonExe -m venv $venvDir
    Assert-LastExit 'Virtual environment creation'
    $PythonExe = Join-Path $venvDir 'Scripts\python.exe'
    & $PythonExe -m pip install --upgrade pip
    Assert-LastExit 'pip upgrade'
    & $PythonExe -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
    Assert-LastExit 'PyTorch installation'
    & $PythonExe -m pip install -r (Join-Path $pluginDir 'requirements-nuke.txt')
    Assert-LastExit 'Inference dependency installation'
}

if (-not $PythonExe) {
    $pythonCandidates = @(
        $previousConfig['python_exe'],
        (Join-Path $repoRoot '.venv-nuke\Scripts\python.exe'),
        (Join-Path $repoRoot '.venv\Scripts\python.exe')
    )
    foreach ($candidate in $pythonCandidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { $PythonExe = $candidate; break }
    }
}
if (-not $PythonExe) { throw 'No UniSHARP runtime found. Supply -PythonExe or -CreateEnvironment -BasePythonExe. See README.md.' }
$PythonExe = Resolve-AbsolutePath $PythonExe
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { throw "Python not found: $PythonExe" }

if (-not $Checkpoint) {
    $checkpointCandidates = @(
        $previousConfig['checkpoint'],
        (Join-Path $repoRoot 'checkpoints\pretained_model.pt')
    )
    foreach ($candidate in $checkpointCandidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { $Checkpoint = $candidate; break }
    }
    if (-not $Checkpoint) { $Checkpoint = Join-Path $repoRoot 'checkpoints\pretained_model.pt' }
}
$Checkpoint = Resolve-AbsolutePath $Checkpoint
if (-not $OutputRoot) { $OutputRoot = $previousConfig['output_root'] }
if (-not $OutputRoot) { $OutputRoot = Join-Path $repoRoot 'Nuke_Output' }
$OutputRoot = Resolve-AbsolutePath $OutputRoot
$NukeUserDir = Resolve-AbsolutePath $NukeUserDir

if ($SkipRuntimeCheck) {
    Write-Warning 'Runtime validation was explicitly skipped. Generation readiness is unverified.'
} else {
    Push-Location -LiteralPath $repoRoot
    try {
        & $PythonExe (Join-Path $pluginDir 'worker.py') --check --checkpoint $Checkpoint
        Assert-LastExit 'UniSHARP runtime check'
    } finally {
        Pop-Location
    }
}

$newConfig = [ordered]@{}
foreach ($key in $previousConfig.Keys) { $newConfig[$key] = $previousConfig[$key] }
$newConfig['python_exe'] = $PythonExe
$newConfig['checkpoint'] = $Checkpoint
$newConfig['output_root'] = $OutputRoot
$configText = ($newConfig | ConvertTo-Json -Depth 12) + "`r`n"
$initPath = Join-Path $NukeUserDir 'init.py'
$oldBytes = [byte[]]@()
if (Test-Path -LiteralPath $initPath -PathType Leaf) { $oldBytes = [System.IO.File]::ReadAllBytes($initPath) }
$oldText = $byteEncoding.GetString($oldBytes)
if (($oldBytes.Length -ge 2) -and (($oldBytes[0] -eq 255 -and $oldBytes[1] -eq 254) -or ($oldBytes[0] -eq 254 -and $oldBytes[1] -eq 255))) {
    throw "init.py uses UTF-16, which is not a supported Python source encoding. Original file retained: $initPath"
}
$baseText = [regex]::Replace($oldText, $markerPattern, '')
if ($baseText.Contains('# >>> UNISHARP_NUKE_BOOTSTRAP >>>') -or $baseText.Contains('# <<< UNISHARP_NUKE_BOOTSTRAP <<<')) {
    throw "Incomplete UniSHARP markers in $initPath. File retained; repair its marked block before installing."
}
$encodedPluginDir = [Convert]::ToBase64String($utf8.GetBytes($pluginDir))
$bootstrapLines = @(
    '',
    '# >>> UNISHARP_NUKE_BOOTSTRAP >>>',
    'import base64 as _unisharp_b64',
    'import nuke as _unisharp_host',
    'if getattr(_unisharp_host, "NUKE_VERSION_MAJOR", 0) >= 17:',
    ('    _unisharp_host.pluginAddPath(_unisharp_b64.b64decode("' + $encodedPluginDir + '").decode("utf-8"))'),
    'del _unisharp_b64, _unisharp_host',
    '# <<< UNISHARP_NUKE_BOOTSTRAP <<<',
    ''
)
$newText = $baseText + ($bootstrapLines -join "`r`n")
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
New-Item -ItemType Directory -Path $NukeUserDir -Force | Out-Null
if (-not (Test-Path -LiteralPath $configPath) -or [System.IO.File]::ReadAllText($configPath, $utf8) -ne $configText) {
    Backup-ExistingFile $configPath
    [System.IO.File]::WriteAllText($configPath, $configText, $utf8)
}
if ($oldText -ne $newText) {
    Backup-ExistingFile $initPath
    [System.IO.File]::WriteAllBytes($initPath, $byteEncoding.GetBytes($newText))
}

Write-Host ''
Write-Host 'UniSHARP Nuke integration installed.'
Write-Host "Plugin: $pluginDir"
Write-Host "Python: $PythonExe"
Write-Host "Checkpoint: $Checkpoint"
Write-Host "Outputs: $OutputRoot"
Write-Host "Startup: $initPath"
Write-Host 'Restart Nuke 17, then choose Nodes > UniSHARP > UniSHARP (Nuke 17).'
Write-Host 'Keep this project folder and the selected external Python environment in place.'
