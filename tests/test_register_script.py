"""Static checks of scripts/register-task.ps1 (it is verified live on Windows, task 7.5)."""

from pathlib import Path

SCRIPT = (Path(__file__).resolve().parents[1] / "scripts" / "register-task.ps1").read_text(encoding="utf-8-sig")


def test_always_automatic_mode_and_never_include_closed():
    assert '@("poll", "--all"' in SCRIPT
    assert "--include-closed" not in SCRIPT.replace("never with\n    the test-only --include-closed", "").replace(
        "--include-closed is deliberately not supported", ""
    )


def test_no_overlapping_instances_and_time_limit():
    assert "-MultipleInstances IgnoreNew" in SCRIPT
    assert "-ExecutionTimeLimit" in SCRIPT


def test_working_directory_dry_run_debug_and_remove():
    assert "-WorkingDirectory $WorkingDirectory" in SCRIPT
    assert '"--dry-run"' in SCRIPT and "[switch]$DryRun" in SCRIPT
    assert '"--debug"' in SCRIPT and "[switch]$DebugMode" in SCRIPT
    assert "Unregister-ScheduledTask" in SCRIPT and "[switch]$Remove" in SCRIPT
    assert "Register-ScheduledTask" in SCRIPT and "-Force" in SCRIPT


def test_checks_executable_and_config_before_registering():
    register_at = SCRIPT.index("Register-ScheduledTask -TaskName")
    assert SCRIPT.index("review-agent не найден в PATH") < register_at
    assert SCRIPT.index("Конфиг '$configFull' не найден") < register_at


def test_saved_as_utf8_with_bom_for_windows_powershell():
    raw = (Path(__file__).resolve().parents[1] / "scripts" / "register-task.ps1").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")


def test_no_window_by_default_with_show_console_option():
    assert "[switch]$ShowConsole" in SCRIPT
    assert '"pythonw.exe"' in SCRIPT and '"-m review_agent $argumentLine"' in SCRIPT
    # conhost --headless loses the exit code - must not be used to hide the window.
    assert "conhost.exe --headless" not in SCRIPT.replace("NOT conhost.exe --headless", "")
