@echo off
REM DF_Small\BUILD.cmd -- compile the embedded VM in place (vm/<package>/.build); no-op where nothing compiles
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"
if "%PYTHON%"=="" set PYTHON=python
"%PYTHON%" -B adapter\dfabric\cli.py node-build %*
endlocal
