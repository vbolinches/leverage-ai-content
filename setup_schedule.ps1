# Registers the nightly batch run with Windows Task Scheduler.
#
# Generation moved off GitHub Actions on 2026-09-11: the model runs locally
# now and a hosted runner cannot reach it. Publishing, monitoring and token
# refresh are untouched and still run in Actions on their own schedules.
#
#   powershell -ExecutionPolicy Bypass -File setup_schedule.ps1
#   powershell -ExecutionPolicy Bypass -File setup_schedule.ps1 -Time 04:30
#   powershell -ExecutionPolicy Bypass -File setup_schedule.ps1 -Remove
#
# Runs as the current user, only when that user is logged on. That is
# deliberate: the alternative stores this account's password in the task, and
# the job pushes to a public repo. A locked screen still counts as logged on,
# so the ordinary overnight case works.

param(
    [string]$Time = "03:00",
    [string]$TaskName = "Leverage AI - nightly content batch",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
    exit 0
}

$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { throw "python is not on PATH; cannot schedule the batch." }

# pythonw.exe runs without a console window, so a 3am job does not flash one
# up. Everything it would have printed goes to logs/batch-<date>.log anyway.
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }

$action = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "`"$repo\run_local_batch.py`"" -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger -Daily -At $Time

$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -MultipleInstances IgnoreNew `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15)

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
    -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description ("Authors the next content batch with the local Ollama model, " +
                  "renders slides and Reels, and pushes so the GitHub publish " +
                  "workflow has posts. See run_local_batch.py.") | Out-Null

Write-Host "Scheduled '$TaskName' daily at $Time."
Write-Host "  runs:  $pythonw $repo\run_local_batch.py"
Write-Host "  logs:  $repo\logs\batch-<date>.log"
Write-Host ""
Write-Host "Run it once now to check it:  Start-ScheduledTask -TaskName '$TaskName'"
