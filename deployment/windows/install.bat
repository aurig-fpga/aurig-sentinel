REM SPDX-License-Identifier: Apache-2.0
REM Copyright 2026 LogiMentor S.r.l.

@echo off
REM ============================================================================
REM Sentinel FPGA Pipeline - Windows Task Scheduler Installation Script
REM This script creates a scheduled task to run Sentinel automatically
REM ============================================================================

setlocal EnableDelayedExpansion

echo ========================================================================
echo         Sentinel FPGA Pipeline - Task Scheduler Installation          
echo ========================================================================
echo.

REM Check for administrator privileges
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo ERROR: Administrator privileges required!
    echo Please run this script as Administrator.
    echo.
    echo Right-click the script and select "Run as administrator"
    pause
    exit /b 1
)

echo [INFO] Running with Administrator privileges
echo.

REM Get script directory
set "SCRIPT_DIR=%~dp0"
set "SENTINEL_ROOT=%SCRIPT_DIR%..\.."
set "XML_FILE=%SCRIPT_DIR%sentinel-task.xml"

REM Check if XML file exists
if not exist "%XML_FILE%" (
    echo [ERROR] Task XML file not found: %XML_FILE%
    pause
    exit /b 1
)

echo [INFO] Sentinel root directory: %SENTINEL_ROOT%
echo [INFO] Task XML template: %XML_FILE%
echo.

REM Detect Python installation
echo [INFO] Detecting Python installation...
where python >nul 2>&1
if %errorLevel% equ 0 (
    for /f "delims=" %%i in ('where python') do set "PYTHON_PATH=%%i"
    echo [FOUND] Python: !PYTHON_PATH!
) else (
    echo [WARNING] Python not found in PATH
    set /p "PYTHON_PATH=Enter full path to python.exe: "
)

REM Verify Python path
if not exist "!PYTHON_PATH!" (
    echo [ERROR] Python executable not found at: !PYTHON_PATH!
    pause
    exit /b 1
)

REM Detect Sentinel installation
set "CONFIG_FILE=%SENTINEL_ROOT%\config\sentinel_local.json"
if not exist "%CONFIG_FILE%" (
    echo [WARNING] Config file not found at: %CONFIG_FILE%
    set /p "CONFIG_FILE=Enter full path to config JSON file: "
)

if not exist "!CONFIG_FILE!" (
    echo [ERROR] Configuration file not found: !CONFIG_FILE!
    pause
    exit /b 1
)

echo [FOUND] Config file: !CONFIG_FILE!
echo.

REM Get current user
for /f "tokens=*" %%u in ('whoami') do set "CURRENT_USER=%%u"
echo [INFO] Current user: %CURRENT_USER%
echo.

REM Create customized XML file
set "TEMP_XML=%TEMP%\sentinel-task-custom.xml"
echo [INFO] Creating customized task XML...

REM Replace paths in XML (simple sed-like replacement)
powershell -Command "(Get-Content '%XML_FILE%') -replace 'C:\\Python310\\python.exe', '!PYTHON_PATH!' -replace 'C:\\Sentinel', '%SENTINEL_ROOT%' | Set-Content '%TEMP_XML%'"

REM Installation options
echo.
echo ========================================================================
echo                        Installation Options
echo ========================================================================
echo.
echo Choose when to run Sentinel:
echo   1) Daily at 2:00 AM (recommended)
echo   2) Daily at custom time
echo   3) On system startup only
echo   4) Custom schedule (edit XML manually after install)
echo.
set /p "SCHEDULE_TYPE=Enter choice [1-4]: "

if "%SCHEDULE_TYPE%"=="2" (
    set /p "CUSTOM_TIME=Enter time (HH:MM in 24-hour format, e.g., 14:30): "
    echo [INFO] Will schedule for daily at !CUSTOM_TIME!
    REM Update XML with custom time (would need more complex PowerShell)
    echo [WARNING] Using default 02:00 AM. Edit task after installation if needed.
)

echo.
echo ========================================================================
echo                          Installing Task
echo ========================================================================
echo.

REM Delete existing task if it exists
schtasks /query /tn "Sentinel\FPGA Build Pipeline" >nul 2>&1
if %errorLevel% equ 0 (
    echo [INFO] Removing existing Sentinel task...
    schtasks /delete /tn "Sentinel\FPGA Build Pipeline" /f >nul 2>&1
)

REM Create the scheduled task
echo [INFO] Creating scheduled task...
schtasks /create /xml "%TEMP_XML%" /tn "Sentinel\FPGA Build Pipeline"

if %errorLevel% equ 0 (
    echo.
    echo ========================================================================
    echo                     Installation Successful!
    echo ========================================================================
    echo.
    echo Task Name: Sentinel\FPGA Build Pipeline
    echo Schedule:  Daily at 2:00 AM + On system startup
    echo Python:    !PYTHON_PATH!
    echo Config:    !CONFIG_FILE!
    echo.
    
    REM Show task info
    echo [INFO] Task details:
    schtasks /query /tn "Sentinel\FPGA Build Pipeline" /fo LIST /v | findstr /C:"Task To Run" /C:"Next Run Time" /C:"Status" /C:"Run As User"
    
    echo.
    echo ========================================================================
    echo                         Useful Commands
    echo ========================================================================
    echo.
    echo   Run task immediately:
    echo   schtasks /run /tn "Sentinel\FPGA Build Pipeline"
    echo.
    echo   View task status:
    echo   schtasks /query /tn "Sentinel\FPGA Build Pipeline"
    echo.
    echo   Disable task:
    echo   schtasks /change /tn "Sentinel\FPGA Build Pipeline" /disable
    echo.
    echo   Enable task:
    echo   schtasks /change /tn "Sentinel\FPGA Build Pipeline" /enable
    echo.
    echo   Delete task:
    echo   schtasks /delete /tn "Sentinel\FPGA Build Pipeline" /f
    echo.
    echo   View task logs:
    echo   eventvwr.msc  ^(Task Scheduler logs in Application and Services Logs^)
    echo.
    echo   Edit task in GUI:
    echo   taskschd.msc  ^(then navigate to Sentinel folder^)
    echo.
    
    set /p "RUN_NOW=Run Sentinel task now? [Y/N]: "
    if /i "!RUN_NOW!"=="Y" (
        echo [INFO] Running task...
        schtasks /run /tn "Sentinel\FPGA Build Pipeline"
        echo [INFO] Task started. Check logs for progress.
    )
    
) else (
    echo.
    echo [ERROR] Failed to create scheduled task!
    echo Check the error messages above for details.
    pause
    exit /b 1
)

REM Cleanup
del "%TEMP_XML%" >nul 2>&1

echo.
echo Press any key to exit...
pause >nul
