@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0" || goto missing
set "COATDESK_V2_EXE="
if not exist "dist-releases\latest-v2.txt" goto missing
set /p "COATDESK_V2_EXE="<"dist-releases\latest-v2.txt"
if not defined COATDESK_V2_EXE goto missing
if not exist "%~dp0%COATDESK_V2_EXE%" goto missing
if /i "%~1"=="/check" goto checked
start "" "%~dp0%COATDESK_V2_EXE%" %*
exit /b %errorlevel%
:checked
echo 실행 파일 경로를 확인했습니다.
exit /b 0
:missing
echo 새 버전의 실행 파일을 찾을 수 없습니다.
echo dist-releases 폴더를 함께 보관하거나 새 버전을 다시 빌드하세요.
if /i not "%~1"=="/check" pause
exit /b 1
