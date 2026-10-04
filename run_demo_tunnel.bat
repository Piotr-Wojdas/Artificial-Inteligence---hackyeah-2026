@echo off
title Solari Demo Public Tunnel
echo ===================================================
echo     SOLARI - Uruchamianie publicznego tunelu HTTPS
echo ===================================================
echo.
echo Uruchamianie tunelu Cloudflare do http://127.0.0.1:8000...
echo.
cloudflared.exe tunnel --url http://127.0.0.1:8000
pause
