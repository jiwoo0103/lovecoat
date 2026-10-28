Unicode True
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"

Name "외투 작업실 2"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\CoatDesk"
RequestExecutionLevel user
SetCompressor /FINAL zlib
VIProductVersion "2.4.1.0"
VIAddVersionKey /LANG=1042 "ProductName" "외투 작업실 2"
VIAddVersionKey /LANG=1042 "FileDescription" "외투 작업실 2 설치 프로그램"
VIAddVersionKey /LANG=1042 "FileVersion" "2.4.1"
VIAddVersionKey /LANG=1042 "LegalCopyright" "CoatDesk"

Var Sandbox
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
    MessageBox MB_OK|MB_ICONSTOP "외투 작업실 2를 종료한 뒤 다시 실행하세요."
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
    CreateDirectory "$SMPROGRAMS\외투 작업실 2"
    CreateShortcut "$SMPROGRAMS\외투 작업실 2\외투 작업실 2.lnk" "$INSTDIR\CoatDesk2.exe"
    CreateShortcut "$SMPROGRAMS\외투 작업실 2\호환 모드.lnk" "$INSTDIR\CoatDesk2.exe" "--software-rendering"
    CreateShortcut "$SMPROGRAMS\외투 작업실 2\프로그램 제거.lnk" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CoatDeskV2" "DisplayName" "외투 작업실 2"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CoatDeskV2" "DisplayVersion" "2.4.1"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CoatDeskV2" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CoatDeskV2" "InstallLocation" "$INSTDIR"
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CoatDeskV2" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CoatDeskV2" "NoRepair" 1
  ${EndIf}
SectionEnd

Section /o "바탕화면 바로가기" DesktopShortcut
  ${If} $Sandbox != "1"
    CreateShortcut "$DESKTOP\외투 작업실 2.lnk" "$INSTDIR\CoatDesk2.exe"
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
    Delete "$DESKTOP\외투 작업실 2.lnk"
    Delete "$SMPROGRAMS\외투 작업실 2\외투 작업실 2.lnk"
    Delete "$SMPROGRAMS\외투 작업실 2\호환 모드.lnk"
    Delete "$SMPROGRAMS\외투 작업실 2\프로그램 제거.lnk"
    RMDir "$SMPROGRAMS\외투 작업실 2"
    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\CoatDeskV2"
  sandbox_cleanup:
    !include "${UNINSTALL_MANIFEST}"
    Delete "$INSTDIR\.sandbox-install"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir "$INSTDIR"
SectionEnd
