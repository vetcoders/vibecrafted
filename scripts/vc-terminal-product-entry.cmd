@echo off
REM Generation-local choke point for the host terminal (Alacritty branded as
REM vc-terminal). Native host lives at ..\libexec\vc-terminal.exe; this wrapper
REM always pins --config-file to the product vc-terminal.toml.
REM
REM Vibecrafted. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
setlocal EnableExtensions
set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "NATIVE_HOST=%ROOT%\libexec\vc-terminal.exe"
if not exist "%NATIVE_HOST%" set "NATIVE_HOST=%ROOT%\libexec\vc-terminal"
set "CONFIG=%USERPROFILE%\.config\vibecrafted\vc-terminal\vc-terminal.toml"
if defined LOCALAPPDATA (
  if exist "%LOCALAPPDATA%\Vibecrafted\home\config\vibecrafted\vc-terminal\vc-terminal.toml" (
    set "CONFIG=%LOCALAPPDATA%\Vibecrafted\home\config\vibecrafted\vc-terminal\vc-terminal.toml"
  )
)
if defined XDG_CONFIG_HOME (
  if exist "%XDG_CONFIG_HOME%\vibecrafted\vc-terminal\vc-terminal.toml" (
    set "CONFIG=%XDG_CONFIG_HOME%\vibecrafted\vc-terminal\vc-terminal.toml"
  )
)
if not exist "%NATIVE_HOST%" (
  echo vc-terminal: native engine missing: %NATIVE_HOST% 1>&2
  exit /b 1
)
"%NATIVE_HOST%" --config-file "%CONFIG%" %*
exit /b %ERRORLEVEL%
