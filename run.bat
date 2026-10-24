@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [!] 没有找到 Python，请先安装 Python 3.9 或更高版本：
    echo       https://www.python.org/downloads/
    echo       安装时请勾选 "Add Python to PATH"。
    echo.
    pause
    exit /b 1
)

rem 优先用 pythonw 启动（不显示黑色命令行窗口），并重定向输出
where pythonw >nul 2>nul
if errorlevel 1 (
    start "" python "main.py" >nul 2>&1
) else (
    start "" pythonw "main.py" >nul 2>&1
)

exit /b 0
