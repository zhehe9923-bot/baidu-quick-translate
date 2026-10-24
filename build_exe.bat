@echo off
setlocal
cd /d "%~dp0"

echo === 正在安装 / 检查 PyInstaller ===
python -m pip install --upgrade pyinstaller
if errorlevel 1 goto :fail

echo.
echo === 开始打包（免安装文件夹版，不依赖系统临时目录）===
python -m PyInstaller --noconfirm --clean --windowed --onedir --name "划词翻译" --icon "assets\icon.ico" --add-data "assets;assets" main.py
if errorlevel 1 goto :fail

echo.
echo === 压缩成便于分发的 zip ===
if exist "dist\划词翻译_免安装版.zip" del /q "dist\划词翻译_免安装版.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\划词翻译' -DestinationPath 'dist\划词翻译_免安装版.zip' -CompressionLevel Optimal"

echo.
echo === 完成 ===
echo 免安装文件夹： dist\划词翻译\  （双击 划词翻译.exe 运行）
echo 打包好的 zip ： dist\划词翻译_免安装版.zip
pause
exit /b 0

:fail
echo.
echo [x] 打包失败，请检查上面的错误信息（常见原因：没联网、pip 被代理拦截）。
pause
exit /b 1
