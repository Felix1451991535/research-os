; Research OS setup for Windows (ADR-212, offline runtime ADR-215). Built by scripts/build_package.py:
;   makensis -V2 -WX -DVERSION=<v> -DAPPDIR=<app files> -DPYDIR=<embedded python> -DWHEELDIR=<wheels>
;            -DPIPWHEEL=<pip wheel file name> -DOUTFILE=<exe> packaging/windows/ResearchOS-Setup.nsi
; Per-user install, no admin, no network, no console window: extracts the app and an embedded CPython 3.12, installs
; the bundled win_amd64 wheels with pip --no-index (log: <install dir>\install.log), creates the "Research OS"
; Desktop + Start Menu shortcuts (hidden launcher), an uninstaller and an Add/Remove Programs entry.
; data\ is never touched by setup or updates; the uninstaller asks before deleting it (default: keep).
; Silent mode (ADR-224): `/S` installs or updates without any page or message box; `/RUNAFTER` (with /S) starts
; Research OS after a successful setup (used by the in-app updater). An update keeps the previous app\ as app.prev
; until the new version is complete and restores it on any failure (the current install stays usable).
; Windows execution: NOT RUN on the dev box (Linux) - compiled only.

Unicode true
RequestExecutionLevel user
SetCompressor /SOLID lzma

!ifndef VERSION
  !error "define VERSION"
!endif
!ifndef APPDIR | PYDIR | WHEELDIR | PIPWHEEL
  !error "define APPDIR, PYDIR, WHEELDIR and PIPWHEEL"
!endif
!ifndef OUTFILE
  !define OUTFILE "ResearchOS-Setup-${VERSION}.exe"
!endif

!define APPNAME "Research OS"
!define REGKEY "Software\ResearchOS"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\ResearchOS"

Name "${APPNAME} ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\ResearchOS"
InstallDirRegKey HKCU "${REGKEY}" "InstallDir"
ShowInstDetails show
ShowUninstDetails show

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "WinMessages.nsh"
!include "FileFunc.nsh"
!define MUI_ICON "${APPDIR}\scripts\researchos.ico"
!define MUI_UNICON "${APPDIR}\scripts\researchos.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApp
!define MUI_FINISHPAGE_RUN_TEXT "Launch Research OS / 启动 Research OS"
!define MUI_PAGE_CUSTOMFUNCTION_PRE SkipFinishIfFailed

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=${LANG_ENGLISH} "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=${LANG_ENGLISH} "FileDescription" "${APPNAME} setup"
VIAddVersionKey /LANG=${LANG_ENGLISH} "LegalCopyright" "Research OS"

Var Failed
Var Rollback  ; "1" while the previous app\ is kept aside as app.prev (update in progress)
!define PY "$INSTDIR\app\python\python.exe"

!macro LOG TEXT
  ; install.log is our own record, written from the first step on, independent of pip (review #82). One encoding
  ; throughout: UTF-16LE with BOM (subprocess output is converted from UTF-8; review #84), so non-ASCII text survives.
  FileOpen $9 "$INSTDIR\install.log" a
  FileSeek $9 0 END $7
  ${If} $7 == "0"
    FileWriteWord $9 0xFEFF
  ${EndIf}
  FileWriteUTF16LE $9 "${TEXT}$\r$\n"
  FileClose $9
!macroend

!macro APPEND_OUT FILE
  ; append a whole UTF-8 output file to the UTF-16LE install.log via raw ReadFile/WriteFile: no NSIS string buffer,
  ; so no NSIS_MAX_STRLEN limit on long lines (review #86); no UI needed, so it works in silent mode (review #85)
  ${If} ${FileExists} "${FILE}"
    FileOpen $R0 "${FILE}" r
    FileSeek $R0 0 END $R1
    FileSeek $R0 0 SET
    ${If} $R1 > 0
      System::Alloc $R1
      Pop $R2
      System::Call "kernel32::ReadFile(p R0, p R2, i R1, *i .R3, p 0)"
      System::Call "kernel32::MultiByteToWideChar(i 65001, i 0, p R2, i R3, p 0, i 0) i.R4"
      IntOp $R5 $R4 * 2
      System::Alloc $R5
      Pop $R6
      System::Call "kernel32::MultiByteToWideChar(i 65001, i 0, p R2, i R3, p R6, i R4) i.R4"
      FileOpen $R7 "$INSTDIR\install.log" a
      FileSeek $R7 0 END
      System::Call "kernel32::WriteFile(p R7, p R6, i R5, *i .R8, p 0)"
      FileWriteUTF16LE $R7 "$\r$\n"
      FileClose $R7
      System::Free $R6
      System::Free $R2
    ${EndIf}
    FileClose $R0
  ${EndIf}
!macroend

!macro RUN LABEL CMD
  ; run hidden; stdout+stderr go to a file on disk (cmd redirection), independent of the details window and of
  ; Python (review #83/#85), then the whole file is appended to install.log; exit code ("error" if cmd itself could
  ; not start; 9009 if the command was not found) is logged too. Result in $0.
  !insertmacro LOG "== ${LABEL} =="
  Delete "$PLUGINSDIR\step.out"
  nsExec::Exec `cmd.exe /d /s /c "${CMD} > "$PLUGINSDIR\step.out" 2>&1"`
  Pop $0
  !insertmacro APPEND_OUT "$PLUGINSDIR\step.out"
  DetailPrint "${LABEL}: exit code $0"
  !insertmacro LOG "${LABEL}: exit code $0"
!macroend

!macro FAIL MSG
  !insertmacro LOG "FAILED: ${MSG}"
  ${If} $Rollback == "1"  ; update failed: put the previous version back, the install stays as it was (ADR-224)
    RMDir /r "$INSTDIR\app"
    Rename "$INSTDIR\app.prev" "$INSTDIR\app"
    ${If} ${FileExists} "$INSTDIR\app.prev\*.*"
      !insertmacro LOG "ROLLBACK FAILED: the previous version is in $INSTDIR\app.prev"
    ${Else}
      !insertmacro LOG "previous version restored"
    ${EndIf}
    StrCpy $Rollback "0"
  ${EndIf}
  MessageBox MB_ICONSTOP|MB_OK "${MSG}$\r$\nLog: $INSTDIR\install.log$\r$\n$\r$\n安装失败，详见日志：$INSTDIR\install.log" /SD IDOK
  StrCpy $Failed "1"
  SetOutPath "$TEMP"
  Abort "${MSG}"
!macroend

!macro STOP_SERVER
  ; -File with double-quoted arguments: any install path works (review #80). Result in $0 ("0" = stopped/none ran)
  StrCpy $0 "0"
  ${If} ${FileExists} "$INSTDIR\app\scripts\stop.ps1"
    !insertmacro RUN "stop running Research OS" 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\app\scripts\stop.ps1" -DataDir "$INSTDIR\data"'
  ${EndIf}
!macroend

Function SkipFinishIfFailed
  ${If} $Failed == "1"
    Abort  ; no "start now" after a failed install
  ${EndIf}
FunctionEnd

Function LaunchApp
  Exec '"$SYSDIR\wscript.exe" "$INSTDIR\app\scripts\launch.vbs"'
FunctionEnd

Function .onInit
  ; an earlier install (update) keeps its directory; otherwise D:\AI\ResearchOS when a D: drive exists
  ReadRegStr $0 HKCU "${REGKEY}" "InstallDir"
  ${If} $0 != ""
    StrCpy $INSTDIR $0
  ${ElseIf} ${FileExists} "D:\*.*"
    StrCpy $INSTDIR "D:\AI\ResearchOS"
  ${Else}
    StrCpy $INSTDIR "$LOCALAPPDATA\ResearchOS"
  ${EndIf}
FunctionEnd

Section "Research OS" SecMain
  InitPluginsDir  ; $PLUGINSDIR is empty until a plugin or InitPluginsDir ran, e.g. in silent mode (review #79)
  CreateDirectory "$INSTDIR"
  ; a log from an earlier setup (possibly ANSI/UTF-8) is kept aside, never appended to (review #87)
  ${If} ${FileExists} "$INSTDIR\install.log"
    Delete "$INSTDIR\install.prev.log"
    Rename "$INSTDIR\install.log" "$INSTDIR\install.prev.log"
    ${If} ${FileExists} "$INSTDIR\install.log"  ; still there: in use; never append to it (review #88), change nothing
      MessageBox MB_ICONSTOP|MB_OK "The previous $INSTDIR\install.log is in use and cannot be moved aside. Nothing was changed. Close the program using it (or restart Windows), then run setup again.$\r$\n$\r$\n旧的安装日志正被占用，无法移开。未做任何更改。请关闭占用它的程序（或重启电脑）后重新运行安装程序。" /SD IDOK
      StrCpy $Failed "1"
      SetOutPath "$TEMP"
      Abort "install.log in use; nothing changed"
    ${EndIf}
  ${EndIf}
  !insertmacro LOG "=== Research OS ${VERSION} setup -> $INSTDIR ==="
  ; update: stop the running server, replace app\ entirely (code + runtime); data\ is never touched
  ${If} ${FileExists} "$INSTDIR\app\*.*"
    !insertmacro STOP_SERVER
    ${If} $0 != "0"  ; never delete a running install (review #81): nothing has been changed yet
      !insertmacro FAIL "Research OS is still running and could not be stopped (exit code $0). Nothing was changed. Close it or restart Windows, then run setup again."
    ${EndIf}
    ; the previous version is moved aside, not deleted, until the new one is complete (rollback, ADR-224)
    RMDir /r "$INSTDIR\app.prev"
    Rename "$INSTDIR\app" "$INSTDIR\app.prev"
    ${If} ${FileExists} "$INSTDIR\app\*.*"
      !insertmacro FAIL "The previous version could not be moved aside (Research OS still running?). Nothing was changed. Close it or restart Windows, then run setup again."
    ${EndIf}
    StrCpy $Rollback "1"
  ${EndIf}
  DetailPrint "Copying Research OS and its Python runtime..."
  SetOutPath "$INSTDIR\app"
  File /r "${APPDIR}\*.*"
  SetOutPath "$INSTDIR\app\python"
  File /r "${PYDIR}\*.*"
  SetOutPath "$PLUGINSDIR\wheels"
  File /r "${WHEELDIR}\*.*"
  SetOutPath "$INSTDIR"
  System::Call 'Kernel32::SetEnvironmentVariable(t "PYTHONUTF8", t "1")'
  System::Call 'Kernel32::SetEnvironmentVariable(t "RESEARCHOS_DATA_DIR", t "$INSTDIR\data")'

  DetailPrint "Installing Python packages offline (no download)..."
  !insertmacro RUN "pip install (offline)" '"${PY}" "$PLUGINSDIR\wheels\${PIPWHEEL}\pip" install --no-index --find-links "$PLUGINSDIR\wheels" --no-deps --no-warn-script-location --disable-pip-version-check -r "$PLUGINSDIR\wheels\install.txt"'
  ${If} $0 != "0"
    !insertmacro FAIL "Installing the bundled Python packages failed (exit code $0)."
  ${EndIf}

  ; data\ and its config: created once, never overwritten
  CreateDirectory "$INSTDIR\data\config"
  ${IfNot} ${FileExists} "$INSTDIR\data\config\researchos.toml"
    FileOpen $1 "$INSTDIR\data\config\researchos.toml" w
    FileWrite $1 'host = "127.0.0.1"$\r$\nport = 8443$\r$\n# Optional relay (docs/OPS.md#model-relay); key in the RESEARCHOS_RELAY_API_KEY env var$\r$\n'
    FileWrite $1 '# relay_base_url = "https://relay.example.com/v1"$\r$\n# relay_for = ["anthropic", "openai"]$\r$\n'
    FileClose $1
  ${EndIf}
  WriteRegExpandStr HKCU "Environment" "RESEARCHOS_DATA_DIR" "$INSTDIR\data"
  SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
  DetailPrint "Preparing the database..."
  !insertmacro RUN "migrate" '"${PY}" -m researchos.app.cli migrate'
  ${If} $0 != "0"
    !insertmacro FAIL "Preparing the database failed (exit code $0)."
  ${EndIf}

  ; shortcuts: hidden launcher (no console window), icon from the app
  CreateShortCut "$DESKTOP\${APPNAME}.lnk" "$SYSDIR\wscript.exe" '"$INSTDIR\app\scripts\launch.vbs"' "$INSTDIR\app\scripts\researchos.ico" 0
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortCut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$SYSDIR\wscript.exe" '"$INSTDIR\app\scripts\launch.vbs"' "$INSTDIR\app\scripts\researchos.ico" 0
  CreateShortCut "$SMPROGRAMS\${APPNAME}\Uninstall ${APPNAME}.lnk" "$INSTDIR\Uninstall.exe"

  ; uninstaller + Add/Remove Programs (per user)
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${REGKEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "Research OS"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\app\scripts\researchos.ico"
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
  ; complete: the previous version is no longer needed
  StrCpy $Rollback "0"
  RMDir /r "$INSTDIR\app.prev"
  !insertmacro LOG "setup finished OK"
  ; silent update from the app (ADR-224): start Research OS again
  ${GetParameters} $1
  ClearErrors
  ${GetOptions} $1 "/RUNAFTER" $2
  ${If} ${Silent}
  ${AndIfNot} ${Errors}
    Call LaunchApp
  ${EndIf}
SectionEnd

Section "Uninstall"
  InitPluginsDir  ; RUN captures output in $PLUGINSDIR
  ; stop a running server first (files are locked while it runs); failure: cancel before removing anything
  ${If} ${FileExists} "$INSTDIR\app\scripts\stop.ps1"
    !insertmacro STOP_SERVER
    ${If} $0 != "0"
      MessageBox MB_OKCANCEL|MB_ICONEXCLAMATION "Research OS could not be stopped (exit code $0).$\r$\nClose it (or restart Windows), then click OK to continue, or Cancel to stop uninstalling.$\r$\n$\r$\n无法停止 Research OS。请先关闭它（或重启电脑）后点“确定”继续，或点“取消”中止卸载。" /SD IDCANCEL IDOK stopped
      Abort "Uninstall cancelled: Research OS is still running. Nothing was removed."
      stopped:
    ${EndIf}
  ${EndIf}
  Delete "$DESKTOP\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
  Delete "$SMPROGRAMS\${APPNAME}\Uninstall ${APPNAME}.lnk"
  RMDir "$SMPROGRAMS\${APPNAME}"
  RMDir /r "$INSTDIR\app.prev"
  RMDir /r "$INSTDIR\app"
  ${If} ${FileExists} "$INSTDIR\app\*.*"
    ; keep the uninstaller and its entry so it can be run again; data\ is never touched here
    MessageBox MB_OK|MB_ICONEXCLAMATION "Some program files could not be removed (still in use?):$\r$\n$INSTDIR\app$\r$\nRestart Windows and run the uninstaller again. Your data was not touched.$\r$\n$\r$\n部分程序文件无法删除（可能仍在使用）。请重启后再次卸载。你的数据未被改动。" /SD IDOK
    Abort "Program files remain in $INSTDIR\app; data untouched."
  ${EndIf}
  Delete "$INSTDIR\install.log"
  Delete "$INSTDIR\install.prev.log"
  Delete "$INSTDIR\Uninstall.exe"
  DeleteRegKey HKCU "${UNINSTKEY}"
  DeleteRegKey HKCU "${REGKEY}"
  ; your research data: kept unless you explicitly say yes (default button and silent mode: keep)
  ${If} ${FileExists} "$INSTDIR\data\*.*"
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "Also delete your research data in $INSTDIR\data?$\r$\nThis cannot be undone. Choose No to keep it (recommended).$\r$\n$\r$\n是否同时删除研究数据？此操作不可恢复。选择“否”保留（推荐）。" /SD IDNO IDYES deldata
    DetailPrint "Kept your data: $INSTDIR\data"
    Goto done
    deldata:
      RMDir /r "$INSTDIR\data"
      DeleteRegValue HKCU "Environment" "RESEARCHOS_DATA_DIR"
    done:
  ${EndIf}
  RMDir "$INSTDIR"
SectionEnd
