@echo off
REM cc_mine — Launch the interactive AI coding agent
REM Run from the project root, or add this directory to PATH.
REM Requires: .venv with dependencies installed.

setlocal
set "PROJECT_DIR=%~dp0"
set "VENV_PYTHON=%PROJECT_DIR%.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo [cc_mine] Virtual environment not found at .venv\Scripts\python.exe
    echo [cc_mine] Please create one: python -m venv .venv
    echo [cc_mine] Then install deps: .venv\Scripts\pip install openai mcp httpx
    exit /b 1
)

"%VENV_PYTHON%" -m cc_mine_cli %*
