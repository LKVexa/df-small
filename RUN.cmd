@echo off
REM DF_Small\RUN.cmd -- a .pal bundle -> its row-sequence witness on this VM; a native guest program -> compile/sign/verify/run
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"
if "%PYTHON%"=="" set PYTHON=python
"%PYTHON%" -B adapter\dfabric\cli.py node-run %*
endlocal
