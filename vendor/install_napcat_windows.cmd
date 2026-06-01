@echo off
setlocal

powershell -ExecutionPolicy Bypass -File "%~dp0install_napcat_windows.ps1" %*
