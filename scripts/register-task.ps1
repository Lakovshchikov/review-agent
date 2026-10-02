<#
.SYNOPSIS
    Registers (or updates / removes) a Windows Task Scheduler task that runs
    `review-agent poll --all` on an interval.

.DESCRIPTION
    review-agent itself knows nothing about scheduling (AGENTS.md section 5):
    one invocation = one polling pass. This script only tells Task Scheduler
    to call it periodically, always in automatic mode (--all) and never with
    the test-only --include-closed flag.

    The task:
      - runs `<ReviewAgentPath> poll --all --config <ConfigPath> [--dry-run] [--debug]`
      - starts in <WorkingDirectory> (relative paths in the config, including
        storage.work_dir, are resolved from there)
      - repeats every <IntervalMinutes>, never starts a second instance while
        one is still running (MultipleInstances IgnoreNew), and is stopped
        after <ExecutionTimeLimitHours>
      - runs as the current user: by default only while logged on
        (Interactive); with -RunWhetherLoggedOn as S4U (no window, no stored
        password, but no network credentials of the Windows session either)

    Re-running the script with the same -TaskName replaces the task.

    The file is saved as UTF-8 WITH BOM on purpose: Windows PowerShell 5.1
    reads a .ps1 without BOM in the ANSI code page and would garble the
    Cyrillic messages.

.EXAMPLE
    .\scripts\register-task.ps1 -DryRun -IntervalMinutes 15
    First, a dry-run schedule: reviews run, nothing is written to GitLab.

.EXAMPLE
    .\scripts\register-task.ps1
    Production schedule with defaults (every 30 minutes, config.yaml).

.EXAMPLE
    .\scripts\register-task.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$TaskName = "review-agent-poll",
    [ValidateRange(1, 1440)]
    [int]$IntervalMinutes = 30,
    [string]$ConfigPath = "config.yaml",
    [string]$WorkingDirectory = (Split-Path -Parent $PSScriptRoot),
    [string]$ReviewAgentPath,
    [ValidateRange(1, 72)]
    [int]$ExecutionTimeLimitHours = 4,
    [switch]$DryRun,
    # Not "-Debug": that name is a PowerShell common parameter.
    [switch]$DebugMode,
    [switch]$RunWhetherLoggedOn,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Задача '$TaskName' удалена."
    } else {
        Write-Host "Задачи '$TaskName' нет — удалять нечего."
    }
    return
}

# -- checks before registering anything ---------------------------------------

if (-not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
    throw "Рабочая папка '$WorkingDirectory' не найдена. Укажите -WorkingDirectory <папка проекта>."
}
$WorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path

if (-not $ReviewAgentPath) {
    $command = Get-Command review-agent -ErrorAction SilentlyContinue
    if (-not $command) {
        throw ("review-agent не найден в PATH. Активируйте venv проекта или передайте путь явно: " +
               "-ReviewAgentPath <папка проекта>\.venv\Scripts\review-agent.exe")
    }
    $ReviewAgentPath = $command.Source
}
if (-not (Test-Path -LiteralPath $ReviewAgentPath -PathType Leaf)) {
    throw "Файл '$ReviewAgentPath' не найден. Передайте -ReviewAgentPath <путь к review-agent.exe>."
}
$ReviewAgentPath = (Resolve-Path -LiteralPath $ReviewAgentPath).Path

$configFull = if ([System.IO.Path]::IsPathRooted($ConfigPath)) { $ConfigPath } else { Join-Path $WorkingDirectory $ConfigPath }
if (-not (Test-Path -LiteralPath $configFull -PathType Leaf)) {
    throw "Конфиг '$configFull' не найден (путь считается от рабочей папки '$WorkingDirectory')."
}

# -- the task -----------------------------------------------------------------

# Always automatic mode; --include-closed is deliberately not supported here.
$arguments = @("poll", "--all", "--config", "`"$ConfigPath`"")
if ($DryRun) { $arguments += "--dry-run" }
if ($DebugMode) { $arguments += "--debug" }
$argumentLine = $arguments -join " "

$action = New-ScheduledTaskAction -Execute $ReviewAgentPath -Argument $argumentLine -WorkingDirectory $WorkingDirectory
# Starts now and repeats every IntervalMinutes. No -RepetitionDuration:
# on current Windows versions that means "indefinitely" (check after
# registering: Get-ScheduledTask <name> | Select -Expand Triggers).
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours $ExecutionTimeLimitHours) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$logonType = if ($RunWhetherLoggedOn) { "S4U" } else { "Interactive" }
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType $logonType -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force `
    -Description "review-agent: один проход опроса GitLab и ревью MR (poll --all). Создано scripts/register-task.ps1." | Out-Null

Write-Host "Задача '$TaskName' зарегистрирована (или обновлена):"
Write-Host "  команда:        `"$ReviewAgentPath`" $argumentLine"
Write-Host "  рабочая папка:  $WorkingDirectory"
Write-Host "  интервал:       каждые $IntervalMinutes мин, без параллельных экземпляров, лимит $ExecutionTimeLimitHours ч"
Write-Host "  пользователь:   $user ($logonType)"
Write-Host ""
Write-Host "Как смотреть результат (подробно — README, «Как следить за задачей»):"
Write-Host "  Get-ScheduledTaskInfo -TaskName $TaskName   # LastTaskResult: 0 ок, 1 какой-то MR упал, 2 проход не стартовал"
Write-Host "  taskschd.msc                                 # Планировщик заданий: состояние, журнал, Выполнить/Завершить"
Write-Host "  логи проходов: <storage.work_dir>\logs\ (по умолчанию $WorkingDirectory\.review-agent\logs)"
Write-Host ""
Write-Host "Важно: gitlab.claim_ttl_minutes в конфиге должен быть не меньше $($ExecutionTimeLimitHours * 60) мин (лимит задачи)."
