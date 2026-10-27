"""Static checks of the Windows setup script (no Windows here; ADR-212): review #79 / #80 on their triggers."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NSI = (ROOT / "packaging" / "windows" / "ResearchOS-Setup.nsi").read_text(encoding="utf-8")


def _code(text: str) -> list[str]:
    return [line.split(";")[0] for line in text.splitlines()]


def _section(name: str) -> str:
    start = NSI.index(f'Section "{name}"')
    return NSI[start:NSI.index("SectionEnd", start)]


def test_79_plugins_dir_initialised_before_use() -> None:
    lines = _code(_section("Research OS"))
    init = next(i for i, ln in enumerate(lines) if "InitPluginsDir" in ln)
    first_use = next(i for i, ln in enumerate(lines) if "$PLUGINSDIR" in ln)
    assert init < first_use


def test_80_stop_uses_file_args_and_failures_are_not_ignored() -> None:
    macro = NSI[NSI.index("!macro STOP_SERVER"):NSI.index("!macroend", NSI.index("!macro STOP_SERVER"))]
    calls = [ln for ln in _code(macro) if "stop.ps1" in ln and "!insertmacro RUN" in ln]
    assert calls and all("-File" in c and "-Command" not in c and '-DataDir "$INSTDIR\\data"' in c for c in calls)
    assert not re.search(r"'\$INSTDIR", macro.replace("'powershell.exe", ""))  # no install path in single quotes
    un = _section("Uninstall")
    after = un[un.index("!insertmacro STOP_SERVER"):]
    assert re.search(r'!insertmacro STOP_SERVER\s+\$\{If\} \$0 != "0"', after)  # exit code checked
    assert after.index('RMDir /r "$INSTDIR\\app"') < after.index('${If} ${FileExists} "$INSTDIR\\app\\*.*"')
    assert after.index('${If} ${FileExists} "$INSTDIR\\app\\*.*"') < after.index('Delete "$INSTDIR\\Uninstall.exe"')


def test_data_is_deleted_only_after_an_explicit_yes() -> None:
    un = _section("Uninstall")
    data_rm = [i for i, ln in enumerate(un.splitlines()) if "RMDir" in ln and "\\data" in ln]
    label = next(i for i, ln in enumerate(un.splitlines()) if ln.strip() == "deldata:")
    assert data_rm and all(i > label for i in data_rm)
    assert "MB_DEFBUTTON2" in un and "/SD IDNO IDYES deldata" in un
    assert "RMDir" not in _section("Research OS").split("Section")[0] or "\\data" not in _section("Research OS")


def test_stop_ps1_accepts_the_data_dir() -> None:
    stop = (ROOT / "scripts" / "stop.ps1").read_text(encoding="utf-8")
    assert re.search(r"param\(\[string\]\$DataDir", stop)
    assert stop.index("param(") < stop.index('. "$PSScriptRoot')


def test_offline_install_without_powershell_or_download() -> None:
    main = "\n".join(_code(_section("Research OS")))
    pip = [ln for ln in main.splitlines() if "\\pip\" install" in ln or "pip\" install" in ln]
    assert pip and all("--no-index" in ln and "--find-links" in ln and "--no-deps" in ln for ln in pip)
    execs = [ln for ln in main.splitlines() if "Exec" in ln]
    assert not any("http" in ln.lower() for ln in execs) and "install.ps1" not in "\n".join(execs)
    assert not re.search(r"NSISdl|inetc|Invoke-WebRequest|irm ", NSI)  # no download in setup
    assert "powershell" not in main.replace("!insertmacro STOP_SERVER", "")  # only the stop macro uses it
    assert 'MUI_FINISHPAGE_RUN_TEXT "Launch Research OS' in NSI
    assert "${IfNot} ${FileExists} \"$INSTDIR\\data\\config\\researchos.toml\"" in main  # config never overwritten
    assert 'RMDir /r "$INSTDIR\\data' not in main  # setup/update never removes data



def _macro(name: str) -> str:
    start = NSI.index(f"!macro {name}")
    return NSI[start:NSI.index("!macroend", start)]


def test_81_update_aborts_before_deleting_when_stop_fails() -> None:
    """Review #81: a failed stop on update must abort with nothing deleted (previously ignored, then RMDir /r app)."""
    main = "\n".join(_code(_section("Research OS")))
    upd = main[main.index("!insertmacro STOP_SERVER"):]
    check = upd.index('${If} $0 != "0"')
    move = upd.index('Rename "$INSTDIR\\app" "$INSTDIR\\app.prev"')  # ADR-224: moved aside, no longer deleted
    assert check < upd.index("!insertmacro FAIL") < move
    assert "Nothing was changed" in upd[:move]
    assert 'StrCpy $0 "0"' in _macro("STOP_SERVER")  # defined result when there is nothing to stop


def test_82_install_log_exists_from_the_first_step_and_records_every_subprocess() -> None:
    """Review #82: failures before pip logs (python.exe cannot start, pip bootstrap) must still leave install.log."""
    main = [ln.strip() for ln in _code(_section("Research OS")) if ln.strip()]
    first_log = next(i for i, ln in enumerate(main) if ln.startswith("!insertmacro LOG"))
    first_exec = next(i for i, ln in enumerate(main) if "!insertmacro RUN" in ln or "STOP_SERVER" in ln or "Exec" in ln)
    assert first_log < first_exec
    assert not [ln for ln in main if "nsExec::" in ln]  # every subprocess goes through RUN (output + exit code)
    run = _macro("RUN")
    assert 'LOG "${LABEL}: exit code $0"' in run and "Pop $0" in run
    assert '"$INSTDIR\\install.log" a' in _macro("LOG") and "!insertmacro LOG" in _macro("FAIL")
    for step in ("pip install (offline)", '"migrate"', "STOP_SERVER"):
        assert any(step in ln for ln in main)


def test_83_85_output_goes_to_a_file_independent_of_ui_and_python() -> None:
    """Review #83/#85: output must survive silent installs (no details window) and a Python that cannot start."""
    run = _macro("RUN")
    assert "ExecToStack" not in NSI and "ExecToLog" not in run and "FindWindow" not in NSI and "LVM_" not in NSI
    assert 'nsExec::Exec `cmd.exe /d /s /c "${CMD} > "$PLUGINSDIR\\step.out" 2>&1"`' in run
    assert run.index("nsExec::Exec") < run.index('!insertmacro APPEND_OUT "$PLUGINSDIR\\step.out"')
    assert "InitPluginsDir" in _section("Uninstall")  # the stop step also captures to $PLUGINSDIR there
    assert "run_logged" not in NSI and not (ROOT / "scripts" / "run_logged.py").exists()  # one mechanism


def test_86_long_output_is_copied_as_bytes_not_strings() -> None:
    """Review #86: per-line NSIS_MAX_STRLEN buffers cut long lines; the copy is now raw bytes -> UTF-16."""
    app = "\n".join(_code(_macro("APPEND_OUT")))
    for call in ("kernel32::ReadFile", "kernel32::MultiByteToWideChar(i 65001", "kernel32::WriteFile", "System::Alloc"):
        assert call in app, call
    assert "FileRead" not in app and "NSIS_MAX_STRLEN" not in app


def test_84_87_one_encoding_and_a_fresh_log_per_setup() -> None:
    """Review #84/#87: UTF-16LE with BOM only; an older (ANSI/UTF-8) log is rotated, never appended to."""
    log_macro = _macro("LOG")
    assert "FileWriteUTF16LE" in log_macro and "FileWriteWord $9 0xFEFF" in log_macro
    assert not re.search(r"FileWrite \$(9|R7)\b", NSI)  # no ANSI writes to install.log
    main = [ln.strip() for ln in _code(_section("Research OS")) if ln.strip()]
    rot = next(i for i, ln in enumerate(main) if ln.startswith('Rename "$INSTDIR\\install.log"'))
    first_log = next(i for i, ln in enumerate(main) if ln.startswith("!insertmacro LOG"))
    first_run = next(i for i, ln in enumerate(main) if "!insertmacro RUN" in ln or "STOP_SERVER" in ln)
    assert rot < first_log < first_run and '"$INSTDIR\\install.prev.log"' in main[rot]
    assert 'Delete "$INSTDIR\\install.prev.log"' in _section("Uninstall")
    stop = (ROOT / "scripts" / "stop.ps1").read_text(encoding="utf-8")
    assert "[Console]::OutputEncoding = [Text.Encoding]::UTF8" in stop  # captured as UTF-8 like Python's output


def test_88_failed_log_rotation_aborts_before_any_write_or_change() -> None:
    """Review #88: a failed Rename (old log held open) was ignored and UTF-16LE was appended to the old log."""
    main = [ln.strip() for ln in _code(_section("Research OS")) if ln.strip()]
    rot = next(i for i, ln in enumerate(main) if ln.startswith('Rename "$INSTDIR\\install.log"'))
    guard = main[rot + 1]
    assert guard.startswith('${If} ${FileExists} "$INSTDIR\\install.log"')
    block = main[rot + 1:main.index("${EndIf}", rot + 1)]
    assert any(ln.startswith("Abort") for ln in block) and not any("!insertmacro" in ln for ln in block)
    first_log = next(i for i, ln in enumerate(main) if ln.startswith("!insertmacro LOG"))
    assert rot < first_log and all("!insertmacro RUN" not in ln and "STOP_SERVER" not in ln for ln in main[:first_log])


def test_224_update_keeps_the_previous_app_until_complete_and_restores_it_on_failure() -> None:
    """ADR-224: an update moves app\\ to app.prev (never deletes it first); FAIL restores it; success removes it."""
    main = [ln.strip() for ln in _code(_section("Research OS")) if ln.strip()]
    move = main.index('Rename "$INSTDIR\\app" "$INSTDIR\\app.prev"')
    assert main[move + 1].startswith('${If} ${FileExists} "$INSTDIR\\app\\*.*"')  # move failed -> nothing changed
    assert main.index('StrCpy $Rollback "1"') > move
    assert not any(ln == 'RMDir /r "$INSTDIR\\app"' for ln in main)  # the running version is never deleted first
    first_copy = next(i for i, ln in enumerate(main) if ln.startswith("File /r"))
    assert move < first_copy
    fail = "\n".join(_code(_macro("FAIL")))
    assert fail.index('${If} $Rollback == "1"') < fail.index('Rename "$INSTDIR\\app.prev" "$INSTDIR\\app"')
    assert fail.index('Rename "$INSTDIR\\app.prev"') < fail.index("Abort")
    last_fail = max(i for i, ln in enumerate(main) if "!insertmacro FAIL" in ln)
    cleanup = main.index('RMDir /r "$INSTDIR\\app.prev"', move + 1)
    assert cleanup > last_fail and main[cleanup - 1] == 'StrCpy $Rollback "0"'
    assert 'RMDir /r "$INSTDIR\\app.prev"' in _section("Uninstall")


def test_224_silent_update_restarts_the_app_only_with_runafter() -> None:
    main = [ln.strip() for ln in _code(_section("Research OS")) if ln.strip()]
    i = main.index('${GetOptions} $1 "/RUNAFTER" $2')
    assert main[i - 2] == "${GetParameters} $1" and main[i - 1] == "ClearErrors"
    assert main[i + 1:i + 4] == ["${If} ${Silent}", "${AndIfNot} ${Errors}", "Call LaunchApp"]
    assert main.index('!insertmacro LOG "setup finished OK"') < i
    assert '!include "FileFunc.nsh"' in NSI
    # every message box answers itself in silent mode
    boxes = [ln for ln in _code(NSI) if "MessageBox" in ln]
    assert boxes and all("/SD " in ln for ln in boxes)
