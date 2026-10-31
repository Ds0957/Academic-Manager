# Ejecutar con la sesión de Windows autorizada. No requiere conexión a ChatGPT.
# Sin parámetros registra la instalación de desarrollo de siempre. El asistente de una copia
# instalada pasa -Repo y -Python (y, si hiciera falta, -Runtime): la tarea los reenvía a canvas-daily.ps1.
param(
    [string]$Repo = "C:\AcademicManagerDev\Academic-Manager",
    [string]$Python = "",
    [string]$Runtime = ""
)
$ErrorActionPreference = "Stop"
$taskName = "AcademicManager-CanvasDaily"
$script = "$Repo\scripts\canvas-daily.ps1"
if (-not (Test-Path -LiteralPath $script)) { throw "Falta el script de sincronización." }
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $script + '"'
if ($PSBoundParameters.ContainsKey("Repo")) { $arguments += ' -Repo "' + $Repo + '"' }
if ($Python) { $arguments += ' -Python "' + $Python + '"' }
if ($Runtime) { $arguments += ' -Runtime "' + $Runtime + '"' }
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$morning = New-ScheduledTaskTrigger -Daily -At "08:00"
$evening = New-ScheduledTaskTrigger -Daily -At "18:00"
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($morning,$evening) -Settings $settings -Principal $principal -Force | Out-Null
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State,@{n='Usuario';e={$_.Principal.UserId}},@{n='Disparadores';e={$_.Triggers.Count}} | Format-List
Write-Output "Horario del PC: $(Get-TimeZone | Select-Object -ExpandProperty Id). La ejecución necesita que Windows esté encendido y la sesión de usuario iniciada."
