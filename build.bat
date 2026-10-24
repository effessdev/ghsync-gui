@echo off
cd /d "%~dp0"

python -m pip install -r requirements.txt pyinstaller

python -m PyInstaller --noconfirm --onedir --windowed --name ghsync-gui --collect-all customtkinter ghsync_gui.py

echo.
echo Built: dist\ghsync-gui\ghsync-gui.exe
pause
