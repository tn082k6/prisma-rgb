# Installs Prisma to %USERPROFILE%\.prisma\app and creates Start-menu + Desktop shortcuts.
$ErrorActionPreference = 'Stop'
$src  = $PSScriptRoot
$home_ = Join-Path $env:USERPROFILE '.prisma'
$dst  = Join-Path $home_ 'app'
$venv = Join-Path $home_ 'venv'

Write-Host "Prisma wird installiert nach $dst"
New-Item -ItemType Directory -Force $dst | Out-Null
robocopy $src $dst /MIR /XD __pycache__ .venv /XF install.ps1 *.pyc /NFL /NDL /NJH /NJS /NP | Out-Null

if (-not (Test-Path (Join-Path $venv 'Scripts\pythonw.exe'))) {
    Write-Host 'Python-Umgebung wird eingerichtet ...'
    python -m venv $venv
    & (Join-Path $venv 'Scripts\python.exe') -m pip install -q --upgrade pip
    & (Join-Path $venv 'Scripts\python.exe') -m pip install -q -r (Join-Path $dst 'requirements.txt')
}

$shell = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Prisma.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Prisma.lnk')
)
foreach ($lnk in $targets) {
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = 'wscript.exe'
    $s.Arguments = '"' + (Join-Path $dst 'Prisma.vbs') + '"'
    $s.WorkingDirectory = $dst
    $s.IconLocation = (Join-Path $dst 'ui\prisma.ico')
    $s.Description = 'Prisma RGB Studio'
    $s.Save()
    Write-Host "Verknuepfung: $lnk"
}
Write-Host 'Fertig.'
exit 0
