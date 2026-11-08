Unicode True
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

!define APP_NAME "외투작업실"
!define LEGACY_NAME "외투 작업실 2"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\CoatDeskV2"

Name "${APP_NAME}"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\CoatDesk"
RequestExecutionLevel user
SetCompressor /FINAL zlib
!ifndef APP_VERSION
  !define APP_VERSION "2.5.0"
!endif
VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey /LANG=1042 "ProductName" "${APP_NAME}"
VIAddVersionKey /LANG=1042 "FileDescription" "${APP_NAME} 설치 프로그램"
VIAddVersionKey /LANG=1042 "FileVersion" "${APP_VERSION}"
VIAddVersionKey /LANG=1042 "LegalCopyright" "CoatDesk"

Var Sandbox
!define MUI_ICON "${PAYLOAD}\_internal\coatdesk_v2\CoatDesk.ico"
!define MUI_UNICON "${MUI_ICON}"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_NOAUTOCLOSE
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Korean"

!macro CheckRunning
  System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\CoatDeskV2Running") p.r0'
  ${If} $0 != 0
    System::Call 'kernel32::CloseHandle(p r0)'
    MessageBox MB_OK|MB_ICONSTOP "${APP_NAME}을 종료한 뒤 다시 실행하세요."
    Abort
  ${EndIf}
!macroend

Function .onInit
  StrCpy $Sandbox "0"
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/SANDBOX" $1
  ${IfNot} ${Errors}
    StrCpy $Sandbox "1"
  ${EndIf}
  ${If} $Sandbox == "0"
    !insertmacro CheckRunning
  ${Else}
    ; A sandbox may coexist with a running app only in a fresh folder.
    IfFileExists "$INSTDIR\*.*" 0 sandbox_fresh
    MessageBox MB_OK|MB_ICONSTOP "검증 설치는 비어 있는 새 폴더를 선택하세요."
    Abort
    sandbox_fresh:
  ${EndIf}
FunctionEnd

Section "프로그램 (필수)" Main
  SectionIn RO
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  ${If} $Sandbox == "1"
    FileOpen $0 "$INSTDIR\.sandbox-install" w
    FileClose $0
  ${Else}
    ReadRegStr $1 HKCU "${UNINSTALL_KEY}" "InstallLocation"
    ClearErrors
    CreateDirectory "$SMPROGRAMS\${APP_NAME}"
    CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\CoatDesk2.exe"
    CreateShortcut "$SMPROGRAMS\${APP_NAME}\호환 모드.lnk" "$INSTDIR\CoatDesk2.exe" "--software-rendering"
    CreateShortcut "$SMPROGRAMS\${APP_NAME}\프로그램 제거.lnk" "$INSTDIR\Uninstall.exe"
    ${If} $1 == $INSTDIR
      ${IfNot} ${Errors}
        Delete "$SMPROGRAMS\${LEGACY_NAME}\${LEGACY_NAME}.lnk"
        Delete "$SMPROGRAMS\${LEGACY_NAME}\호환 모드.lnk"
        Delete "$SMPROGRAMS\${LEGACY_NAME}\프로그램 제거.lnk"
        RMDir "$SMPROGRAMS\${LEGACY_NAME}"
      ${EndIf}
      ${If} ${FileExists} "$DESKTOP\${LEGACY_NAME}.lnk"
        ClearErrors
        CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\CoatDesk2.exe"
        ${IfNot} ${Errors}
          Delete "$DESKTOP\${LEGACY_NAME}.lnk"
        ${EndIf}
      ${EndIf}
    ${EndIf}
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" '"$INSTDIR\CoatDesk2.exe",0'
    WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
    WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  ${EndIf}
SectionEnd

Section /o "바탕화면 바로가기" DesktopShortcut
  ${If} $Sandbox != "1"
    CreateShortcut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\CoatDesk2.exe"
  ${EndIf}
SectionEnd

Function un.onInit
  !insertmacro CheckRunning
  MessageBox MB_OK|MB_ICONINFORMATION "프로그램 파일을 제거합니다. 작업 기록과 원본 사진은 보존됩니다." /SD IDOK
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  IfFileExists "$INSTDIR\.sandbox-install" sandbox_cleanup regular_cleanup
  regular_cleanup:
    Delete "$DESKTOP\${APP_NAME}.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\호환 모드.lnk"
    Delete "$SMPROGRAMS\${APP_NAME}\프로그램 제거.lnk"
    RMDir "$SMPROGRAMS\${APP_NAME}"
    DeleteRegKey HKCU "${UNINSTALL_KEY}"
  sandbox_cleanup:
    !include "${UNINSTALL_MANIFEST}"
    Delete "$INSTDIR\.sandbox-install"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir "$INSTDIR"
SectionEnd
