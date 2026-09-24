@echo off
REM Generation-local product entry. Installer publishes this at bin\vc-frame.cmd
REM and the native engine at libexec\vc-frame.exe. Startup consumes product
REM configuration under the operator config home.
REM
REM Vibecrafted. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
setlocal EnableExtensions
set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "NATIVE_HOST=%ROOT%\libexec\vc-frame.exe"
if not exist "%NATIVE_HOST%" set "NATIVE_HOST=%ROOT%\libexec\vc-frame"
set "CONFIG_DIR=%USERPROFILE%\.config\vibecrafted\vc-frame"
if defined LOCALAPPDATA (
  if exist "%LOCALAPPDATA%\Vibecrafted\home\config\vibecrafted\vc-frame\config.kdl" (
    set "CONFIG_DIR=%LOCALAPPDATA%\Vibecrafted\home\config\vibecrafted\vc-frame"
  )
)
if defined XDG_CONFIG_HOME (
  if exist "%XDG_CONFIG_HOME%\vibecrafted\vc-frame\config.kdl" (
    set "CONFIG_DIR=%XDG_CONFIG_HOME%\vibecrafted\vc-frame"
  )
)
if defined VC_FRAME_CONFIG_DIR set "CONFIG_DIR=%VC_FRAME_CONFIG_DIR%"
if not exist "%NATIVE_HOST%" (
  echo vc-frame: native engine missing: %NATIVE_HOST% 1>&2
  exit /b 1
)
set "VC_FRAME_CONFIG_DIR=%CONFIG_DIR%"
set "PATH=%ROOT%\libexec;%PATH%"
"%NATIVE_HOST%" %*
exit /b %ERRORLEVEL%
