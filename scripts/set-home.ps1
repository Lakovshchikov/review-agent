<#
.SYNOPSIS
    Sets up this machine (Windows) to run review-agent from any console:
    installs the `review-agent` command and stores this repository as its
    home in the user environment variable REVIEW_AGENT_HOME.

.DESCRIPTION
    The home is the parent of the folder this script lives in, so there is
    nothing to ask or pass. review-agent then looks for its config.yaml in
    that folder, and every relative path in the config (work_dir, reports,
    skills, ...) is resolved from it - wherever the command is started.

    The script:
      1. checks that `pipx` exists (stops before changing anything if not);
      2. runs `pipx install --force -e <home>` [--python <-Python>] (editable: code
         is taken from the repository) and `pipx ensurepath` (puts the command into PATH);
      3. sets REVIEW_AGENT_HOME at USER level (no administrator rights).

    Re-running is safe: the variable is overwritten, the installation
    refreshed. Run it again after moving the repository.

    Consoles that were already open do NOT see the new variable or PATH:
    open a new one.

    The file is saved as UTF-8 WITH BOM on purpose: Windows PowerShell 5.1
    reads a .ps1 without BOM in the ANSI code page and would garble the
    Cyrillic messages.

.EXAMPLE
    .\scripts\set-home.ps1 -DryRun
    Show what would be done, change nothing.

.EXAMPLE
    .\scripts\set-home.ps1
    Install the command and set REVIEW_AGENT_HOME.

.EXAMPLE
    .\scripts\set-home.ps1 -Python 3.13
    Use Python 3.13 for the installation (when the default Python is too new
    for the dependencies to have ready-made packages).

.EXAMPLE
    .\scripts\set-home.ps1 -Remove
    Delete the variable and uninstall the command.
#>
[CmdletBinding()]
param(
    # Python for the pipx environment: a version ("3.13") or a path to python.exe.
    [string]$Python,
    [switch]$DryRun,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$VarName = "REVIEW_AGENT_HOME"
$PackageName = "review-agent"
$HomeDir = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path

function Step([string]$text) {
    if ($DryRun) { Write-Host "[dry-run] $text" } else { Write-Host $text }
}

# -- pipx: checked before anything is changed ----------------------------------

# `pipx` as a command if it is in PATH; otherwise `py -m pipx` / `python -m pipx`:
# `pip install --user pipx` puts pipx.exe into a folder that is not in PATH yet.
function Find-Pipx {
    $command = Get-Command pipx -ErrorAction SilentlyContinue
    if ($command) { return @{ Exe = $command.Source; Prefix = @() } }
    foreach ($name in @("py", "python")) {
        $pyCmd = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $pyCmd) { continue }
        $saved = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $pyCmd.Source -m pipx --version 2>&1 | Out-Null
            $works = ($LASTEXITCODE -eq 0)
        } catch {
            $works = $false
        } finally {
            $ErrorActionPreference = $saved
        }
        if ($works) { return @{ Exe = $pyCmd.Source; Prefix = @("-m", "pipx") } }
    }
    return $null
}

$pipx = Find-Pipx
if ($pipx) {
    $PipxExe = $pipx.Exe
    $PipxPrefix = $pipx.Prefix
} else {
    $hint = "pipx не найден (ни как команда, ни как py -m pipx). Установите его: py -m pip install --user pipx, " +
            "затем py -m pipx ensurepath, откройте новую консоль и запустите скрипт снова."
    if ($DryRun) {
        Write-Host "[dry-run] ВНИМАНИЕ: $hint Настоящий запуск остановится здесь."
    } else {
        throw $hint
    }
}

# -- removal --------------------------------------------------------------------

if ($Remove) {
    Step "Удаляю пользовательскую переменную $VarName"
    if (-not $DryRun) {
        [Environment]::SetEnvironmentVariable($VarName, $null, "User")
    }
    if ($pipx) {
        $installed = (& $PipxExe @PipxPrefix list --short) -match "^$PackageName\s"
        if ($installed) {
            Step "Удаляю команду: pipx uninstall $PackageName"
            if (-not $DryRun) {
                & $PipxExe @PipxPrefix uninstall $PackageName
                if ($LASTEXITCODE -ne 0) { throw "pipx uninstall завершился с кодом $LASTEXITCODE." }
            }
        } else {
            Write-Host "Команды $PackageName среди установленных pipx нет — удалять нечего."
        }
    }
    if (-not $DryRun) { Write-Host "Готово. Откройте новую консоль, чтобы изменения вступили в силу." }
    return
}

# -- setup ----------------------------------------------------------------------

Step "Дом агента: $HomeDir"
$pythonNote = if ($Python) { " --python $Python" } else { "" }
Step "Устанавливаю команду: pipx install --force -e `"$HomeDir`"$pythonNote"
if (-not $DryRun) {
    $installArgs = @("install", "--force", "-e", $HomeDir)
    if ($Python) { $installArgs += @("--python", $Python) }
    & $PipxExe @PipxPrefix @installArgs
    if ($LASTEXITCODE -ne 0) {
        throw ("pipx install завершился с кодом $LASTEXITCODE. Если сборка зависимости упала (для новой версии Python " +
               "нет готовых пакетов, а компилятора нет) — укажите интерпретатор: -Python 3.13")
    }
}
Step "Добавляю каталог pipx в PATH: pipx ensurepath"
if (-not $DryRun) {
    & $PipxExe @PipxPrefix ensurepath
    if ($LASTEXITCODE -ne 0) { throw "pipx ensurepath завершился с кодом $LASTEXITCODE." }
}
Step "Задаю пользовательскую переменную $VarName = $HomeDir"
if (-not $DryRun) {
    [Environment]::SetEnvironmentVariable($VarName, $HomeDir, "User")
}

if (-not (Test-Path -LiteralPath (Join-Path $HomeDir "config.yaml"))) {
    Write-Host "Подсказка: в $HomeDir нет config.yaml — скопируйте config.example.yaml в config.yaml и отредактируйте."
}
if (-not $DryRun) {
    Write-Host ""
    Write-Host "Готово. Откройте НОВУЮ консоль (уже открытые не видят переменную и PATH) и проверьте: review-agent --help"
}
