@echo off
setlocal
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm --clean --onefile --windowed --name "商家素材管家" main.py
if exist dist\商家素材管家.exe (
  echo Build success: dist\商家素材管家.exe
) else (
  echo Build failed
  exit /b 1
)
