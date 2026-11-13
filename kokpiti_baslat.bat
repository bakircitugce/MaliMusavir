@echo off
chcp 65001 >nul
title MaliAsistan SMMM Kokpiti
echo ======================================================================
echo    MALI ASISTAN - SMMM KURUMSAL MUHASEBE OTOMASYONU
echo ======================================================================
echo.
if not exist .env (
  echo HATA: .env bulunamadi. .env.example dosyasini .env olarak kopyalayip AUTH_SECRET degerini doldurun.
  pause
  exit /b 1
)
echo [1/2] MaliAsistan sunucusu baslatiliyor: http://localhost:8000/app ...
start "" "http://localhost:8000/app"
python -m uvicorn app.main:app --reload --port 8000
pause
