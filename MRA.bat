@echo off
rem Launch the Mechanical Reverse Engineering Assistant GUI.
rem Uses pythonw (no console window); works from any location.
start "" /D "%~dp0" "%~dp0.venv\Scripts\pythonw.exe" -m mra
