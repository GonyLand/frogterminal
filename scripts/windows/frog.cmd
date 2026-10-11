@echo off
rem frog - attach this console to a frogterminal bridge session. Detach: Ctrl+]
rem Put a copy (with absolute paths) in a folder on PATH, or call this file by its full path.
"%~dp0..\..\.venv\Scripts\python.exe" -X utf8 "%~dp0frog.py" %*
