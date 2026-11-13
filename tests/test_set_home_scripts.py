"""Checks of scripts/set-home.ps1 (static) and scripts/set-home.sh (run against a stub pipx)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PS1_PATH = ROOT / "scripts" / "set-home.ps1"
SH_PATH = ROOT / "scripts" / "set-home.sh"
PS1 = PS1_PATH.read_text(encoding="utf-8-sig")


# -- set-home.ps1 (verified live on Windows, task 3.1) --------------------------


def test_ps1_parameters_are_two_switches_and_the_interpreter():
    import re

    block = re.search(r"^param\((.*?)^\)", PS1, re.MULTILINE | re.DOTALL).group(1)
    assert sorted(re.findall(r"\[switch\]\$(\w+)", block)) == ["DryRun", "Remove"]
    # The only string parameter is the interpreter; the home path is never asked for.
    assert re.findall(r"\[string\]\$(\w+)", block) == ["Python"]


def test_ps1_home_comes_from_the_script_location_and_sets_a_user_variable():
    assert "Split-Path -Parent $PSScriptRoot" in PS1
    assert '[Environment]::SetEnvironmentVariable($VarName, $HomeDir, "User")' in PS1
    assert '"User")' in PS1 and '"Machine"' not in PS1


def test_ps1_installs_editable_and_checks_pipx_before_any_change():
    assert "pipx install --force -e" in PS1
    assert "pipx ensurepath" in PS1
    assert PS1.index("pipx не найден") < PS1.index("@PipxPrefix @installArgs")
    assert '"--python", $Python' in PS1
    assert PS1.index("pipx не найден") < PS1.index("SetEnvironmentVariable")


def test_ps1_falls_back_to_python_m_pipx_when_pipx_is_not_in_path():
    # `pip install --user pipx` leaves pipx.exe in a folder that is not in PATH.
    assert '@("py", "python")' in PS1
    assert '"-m", "pipx"' in PS1


def test_ps1_removal_deletes_variable_and_uninstalls_command():
    assert 'SetEnvironmentVariable($VarName, $null, "User")' in PS1
    assert "pipx uninstall" in PS1


def test_ps1_tells_to_open_a_new_console():
    assert "НОВУЮ консоль" in PS1


def test_ps1_saved_as_utf8_with_bom_for_windows_powershell():
    assert PS1_PATH.read_bytes().startswith(b"\xef\xbb\xbf")


# -- set-home.sh ----------------------------------------------------------------


def _bash():
    bash = shutil.which("bash")
    if bash is None:
        return None
    try:
        ostype = subprocess.run(
            [bash, "-c", "echo $OSTYPE"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    # On Windows only Git Bash (msys) shares the filesystem with the test; WSL does not.
    if sys.platform == "win32":
        return bash if ostype.startswith("msys") else None
    return bash


BASH = _bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="no usable bash")


def test_sh_keeps_lf_line_endings():
    assert b"\r" not in SH_PATH.read_bytes()


def _stub_pipx(directory: Path, installed: bool = False) -> Path:
    """A fake `pipx` that logs its calls to <directory>/pipx.log."""
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / "pipx"
    stub.write_text(
        "#!/bin/sh\n"
        'echo "$@" >> "$(dirname "$0")/pipx.log"\n'
        'if [ "$1" = "list" ] && [ "' + ("1" if installed else "0") + '" = "1" ]; then\n'
        '    echo "review-agent 0.1.0"\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    stub.chmod(0o755)
    return stub


def _run(tmp_path, *args, with_pipx=True, installed=False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if with_pipx:
        _stub_pipx(bin_dir, installed)
    rc = tmp_path / "rc"
    env = {**os.environ, "HOME": str(tmp_path)}
    if with_pipx:
        env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    result = subprocess.run(
        [BASH, SH_PATH.as_posix(), "--rc-file", rc.as_posix(), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )
    log = bin_dir / "pipx.log"
    return result, rc, log.read_text(encoding="utf-8") if log.exists() else ""


@needs_bash
def test_sh_writes_one_block_and_repeated_run_replaces_it(tmp_path):
    rc = tmp_path / "rc"
    rc.write_text("alias x=y\n", encoding="utf-8")

    for _ in range(2):
        result, _, log = _run(tmp_path)
        assert result.returncode == 0, result.stderr

    text = rc.read_text(encoding="utf-8")
    assert text.startswith("alias x=y\n")
    assert text.count("# >>> review-agent >>>") == 1
    assert text.count("export REVIEW_AGENT_HOME=") == 1
    assert f"{ROOT.name}'" in text  # the home is the repository folder, quoted
    assert "install --force -e" in log and "ensurepath" in log
    assert "НОВЫЙ терминал" in result.stdout


@needs_bash
def test_sh_remove_deletes_only_the_block(tmp_path):
    _run(tmp_path)
    rc = tmp_path / "rc"
    rc.write_text("alias x=y\n" + rc.read_text(encoding="utf-8") + "alias z=w\n", encoding="utf-8")

    result, _, log = _run(tmp_path, "--remove", installed=True)

    assert result.returncode == 0, result.stderr
    assert rc.read_text(encoding="utf-8") == "alias x=y\nalias z=w\n"
    assert "uninstall review-agent" in log


@needs_bash
def test_sh_dry_run_changes_nothing(tmp_path):
    result, rc, log = _run(tmp_path, "--dry-run")

    assert result.returncode == 0, result.stderr
    assert not rc.exists()
    assert log == ""
    assert "[dry-run]" in result.stdout


@needs_bash
def test_sh_without_pipx_stops_before_changing_anything(tmp_path):
    result, rc, _ = _run(tmp_path, with_pipx=False)

    # The real PATH may contain a pipx; only assert the stop when there is none.
    if shutil.which("pipx") or _python3_has_pipx():
        pytest.skip("pipx is installed on this machine")
    assert result.returncode == 1
    assert "pipx не найден" in result.stderr
    assert not rc.exists()


def _python3_has_pipx():
    python3 = shutil.which("python3")
    if python3 is None:
        return False
    return subprocess.run([python3, "-m", "pipx", "--version"], capture_output=True).returncode == 0


@needs_bash
def test_sh_uses_python_m_pipx_when_pipx_is_not_in_path(tmp_path):
    if shutil.which("pipx"):
        pytest.skip("pipx is in PATH on this machine")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "python3"
    lines = [
        "#!/bin/sh",
        '[ "$1" = "-m" ] && [ "$2" = "pipx" ] || exit 1',
        'echo "$@" >> "$(dirname "$0")/python.log"',
        "exit 0",
    ]
    stub.write_text(chr(10).join(lines) + chr(10), encoding="utf-8", newline=chr(10))
    stub.chmod(0o755)
    rc = tmp_path / "rc"
    result = subprocess.run(
        [BASH, SH_PATH.as_posix(), "--rc-file", rc.as_posix()],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "HOME": str(tmp_path), "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    log = (bin_dir / "python.log").read_text(encoding="utf-8")
    assert "-m pipx install --force -e" in log and "-m pipx ensurepath" in log
    assert "export REVIEW_AGENT_HOME=" in rc.read_text(encoding="utf-8")


@needs_bash
def test_sh_rejects_unknown_option(tmp_path):
    result, rc, _ = _run(tmp_path, "--install")
    assert result.returncode == 2 and not rc.exists()


@needs_bash
def test_sh_passes_the_chosen_python_to_pipx(tmp_path):
    result, _, log = _run(tmp_path, "--python", "3.13")

    assert result.returncode == 0, result.stderr
    assert "install --force -e" in log and "--python 3.13" in log
