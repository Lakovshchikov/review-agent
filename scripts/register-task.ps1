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
      - runs `poll --all --config <ConfigPath> [--dry-run] [--debug]` WITHOUT a
        console window by default: through `pythonw.exe -m review_agent` of the
        same Python as <ReviewAgentPath> (no window to close by accident; the
        exit code 0/1/2 still reaches Task Scheduler). With -ShowConsole it runs
        <ReviewAgentPath> directly and a console window shows the pass
      - starts in <WorkingDirectory> (relative paths in the config, including
        storage.work_dir, are resolved from there)
      - also runs <LogonDelayMinutes> after the user logs on (0 = no logon
        trigger), so a pass missed while logged off is made up at the next
        logon; the delay gives the VPN time to come up
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
    [ValidateRange(0, 240)]
    [int]$LogonDelayMinutes = 5,
    [switch]$DryRun,
    # Not "-Debug": that name is a PowerShell common parameter.
    [switch]$DebugMode,
    [switch]$RunWhetherLoggedOn,
    # Show a console window during each pass (default: no window, see .DESCRIPTION).
    [switch]$ShowConsole,
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

if ($ShowConsole) {
    $execute = $ReviewAgentPath
    $taskArguments = $argumentLine
    $windowMode = "с консольным окном"
} else {
    # pythonw.exe of the Python that review-agent.exe belongs to: next to it
    # in a venv (.venv\Scripts\), one level up for a plain install
    # (Python310\Scripts\review-agent.exe -> Python310\pythonw.exe).
    # NOT conhost.exe --headless: verified that it always exits 0, so the
    # task result would hide failures.
    $scriptsDir = Split-Path -Parent $ReviewAgentPath
    $pythonw = @((Join-Path $scriptsDir "pythonw.exe"), (Join-Path (Split-Path -Parent $scriptsDir) "pythonw.exe")) |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $pythonw) {
        throw ("pythonw.exe не найден рядом с '$ReviewAgentPath' (ни в той же папке, ни уровнем выше). " +
               "Запустите с -ShowConsole, чтобы задача запускала review-agent.exe напрямую (с окном).")
    }
    $execute = $pythonw
    $taskArguments = "-m review_agent $argumentLine"
    $windowMode = "без окна (pythonw)"
}

$action = New-ScheduledTaskAction -Execute $execute -Argument $taskArguments -WorkingDirectory $WorkingDirectory
# Starts now and repeats every IntervalMinutes. No -RepetitionDuration:
# on current Windows versions that means "indefinitely" (check after
# registering: Get-ScheduledTask <name> | Select -Expand Triggers).
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$triggers = @($trigger)
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($LogonDelayMinutes -gt 0) {
    # A pass missed while logged off (Interactive tasks only run in the
    # user's session) is made up at the next logon. StartWhenAvailable
    # alone does not reliably cover that case.
    $logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $logonTrigger.Delay = "PT$($LogonDelayMinutes)M"
    $triggers += $logonTrigger
}
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours $ExecutionTimeLimitHours) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
$logonType = if ($RunWhetherLoggedOn) { "S4U" } else { "Interactive" }
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType $logonType -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal -Force `
    -Description "review-agent: один проход опроса GitLab и ревью MR (poll --all). Создано scripts/register-task.ps1." | Out-Null

Write-Host "Задача '$TaskName' зарегистрирована (или обновлена):"
Write-Host "  команда:        `"$execute`" $taskArguments"
Write-Host "  окно:           $windowMode"
Write-Host "  рабочая папка:  $WorkingDirectory"
Write-Host "  интервал:       каждые $IntervalMinutes мин, без параллельных экземпляров, лимит $ExecutionTimeLimitHours ч"
Write-Host "  пользователь:   $user ($logonType)"
if ($LogonDelayMinutes -gt 0) { Write-Host "  при входе:      проход через $LogonDelayMinutes мин после входа в Windows" }
Write-Host ""
Write-Host "Как смотреть результат (подробно — docs\scheduling.md, «Как следить за задачей»):"
Write-Host "  Get-ScheduledTaskInfo -TaskName $TaskName   # LastTaskResult: 0 ок, 1 какой-то MR упал, 2 проход не стартовал"
Write-Host "  taskschd.msc                                 # Планировщик заданий: состояние, журнал, Выполнить/Завершить"
Write-Host "  логи проходов: <storage.work_dir>\logs\ (по умолчанию $WorkingDirectory\.review-agent\logs)"
Write-Host ""
Write-Host "Важно: gitlab.claim_ttl_minutes в конфиге должен быть не меньше $($ExecutionTimeLimitHours * 60) мин (лимит задачи)."
