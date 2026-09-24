@echo off
REM DF_Small\VERIFY.cmd -- hashes, manifest, schemas, citations, core selfcheck, the VM's own gate (scratch copy), the adapter battery
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"
if "%PYTHON%"=="" set PYTHON=python
"%PYTHON%" -B adapter\dfabric\cli.py node-verify %*
endlocal
