@echo off
rem Envio semanal completo. Use este arquivo no Agendador de Tarefas.
cd /d %~dp0
.venv\Scripts\python -m app.executar
