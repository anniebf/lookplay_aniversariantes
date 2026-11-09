@echo off
rem Sobe a API e o dashboard na porta 8765, acessível pelas máquinas da rede.
cd /d %~dp0
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8765
