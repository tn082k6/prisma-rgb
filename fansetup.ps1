# Runs elevated once: registers the Prisma fan service task, retires FanControl (disabled, not deleted), starts the service.
param([string]$User = "$env:USERDOMAIN\$env:USERNAME", [string]$UserProfile = $env:USERPROFILE)
$log = Join-Path $UserProfile '.prisma\fansetup.log'
Start-Transcript -Path $log -Force | Out-Null
$app = Join-Path $UserProfile '.prisma\app'
$pyw = Join-Path $UserProfile '.prisma\venv\Scripts\pythonw.exe'

# 1) task: elevated, at logon + on demand, restart on failure
$action = New-ScheduledTaskAction -Execute $pyw -Argument "`"$app\fanservice.py`"" -WorkingDirectory $app
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskPath '\Prisma\' -TaskName 'Fans' -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Description 'Prisma Luefter-Dienst (Adminrechte)' -Force | Out-Null
"Aufgabe \Prisma\Fans angelegt"

# 2) FanControl: stop and disable its autostart (all usual places)
Get-Process FanControl -ErrorAction SilentlyContinue | Stop-Process -Force
foreach ($k in 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run') {
    $p = Get-ItemProperty $k -ErrorAction SilentlyContinue
    if (-not $p) { continue }
    foreach ($n in $p.PSObject.Properties.Name) {
        if ($n -match 'FanControl' -or "$($p.$n)" -match 'FanControl\.exe') {
            $sa = ($k -replace '\\Run$', '\Explorer\StartupApproved\Run') -replace 'WOW6432Node\\', ''
            if (-not (Test-Path $sa)) { New-Item $sa -Force | Out-Null }
            Set-ItemProperty $sa -Name $n -Value ([byte[]](3,0,0,0,0,0,0,0,0,0,0,0)) -Type Binary
            "Autostart deaktiviert: $n"
        }
    }
}
Get-ScheduledTask | Where-Object { ($_.Actions | ForEach-Object Execute) -match 'FanControl' -or $_.TaskName -match 'FanControl' } | ForEach-Object {
    Disable-ScheduledTask -TaskPath $_.TaskPath -TaskName $_.TaskName | Out-Null; "Aufgabe deaktiviert: $($_.TaskPath)$($_.TaskName)" }
$startup = Join-Path $UserProfile 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup'
Get-ChildItem $startup -Filter '*FanControl*' -ErrorAction SilentlyContinue | ForEach-Object {
    Move-Item $_.FullName (Join-Path $UserProfile '.prisma\FanControl-Autostart-Backup.lnk') -Force; "Startup-Verknuepfung gesichert und entfernt" }

# 3) start the service
Start-ScheduledTask -TaskPath '\Prisma\' -TaskName 'Fans'
"Dienst gestartet"
Stop-Transcript | Out-Null
