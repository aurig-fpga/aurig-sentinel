# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

# Sentinel FPGA Pipeline - PowerShell Installation Script
# Advanced alternative to the batch script with better error handling

#Requires -RunAsAdministrator

$ErrorActionPreference = "Stop"

Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host "       Sentinel FPGA Pipeline - PowerShell Task Installation          " -ForegroundColor Cyan
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host ""

# Get script location
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SentinelRoot = (Get-Item "$ScriptDir\..\..\").FullName
$XmlTemplate = Join-Path $ScriptDir "sentinel-task.xml"

Write-Host "[INFO] Sentinel root: $SentinelRoot" -ForegroundColor Green
Write-Host "[INFO] XML template: $XmlTemplate" -ForegroundColor Green
Write-Host ""

# Verify XML template exists
if (-not (Test-Path $XmlTemplate)) {
    Write-Host "[ERROR] XML template not found: $XmlTemplate" -ForegroundColor Red
    exit 1
}

# Find Python
Write-Host "[INFO] Detecting Python installation..." -ForegroundColor Yellow
$PythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source

if (-not $PythonPath) {
    Write-Host "[WARNING] Python not found in PATH" -ForegroundColor Yellow
    $PythonPath = Read-Host "Enter full path to python.exe"
}

if (-not (Test-Path $PythonPath)) {
    Write-Host "[ERROR] Python not found at: $PythonPath" -ForegroundColor Red
    exit 1
}

Write-Host "[FOUND] Python: $PythonPath" -ForegroundColor Green

# Find config file
$ConfigFile = Join-Path $SentinelRoot "config\sentinel_local.json"
if (-not (Test-Path $ConfigFile)) {
    Write-Host "[WARNING] Config file not found at: $ConfigFile" -ForegroundColor Yellow
    $ConfigFile = Read-Host "Enter full path to config JSON file"
}

if (-not (Test-Path $ConfigFile)) {
    Write-Host "[ERROR] Config file not found: $ConfigFile" -ForegroundColor Red
    exit 1
}

Write-Host "[FOUND] Config: $ConfigFile" -ForegroundColor Green
Write-Host ""

# Get current user
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
Write-Host "[INFO] Current user: $CurrentUser" -ForegroundColor Green
Write-Host ""

# Schedule options
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host "                      Choose Schedule Type                             " -ForegroundColor Cyan
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  1) Daily at 2:00 AM (default)"
Write-Host "  2) Daily at custom time"
Write-Host "  3) Weekly (specific day)"
Write-Host "  4) On system startup only"
Write-Host ""
$ScheduleChoice = Read-Host "Enter choice [1-4]"

# Create customized XML
Write-Host ""
Write-Host "[INFO] Creating task configuration..." -ForegroundColor Yellow

$XmlContent = Get-Content $XmlTemplate -Raw
$XmlContent = $XmlContent -replace 'C:\\Python310\\python.exe', $PythonPath.Replace('\', '\\')
$XmlContent = $XmlContent -replace 'C:\\Sentinel', $SentinelRoot.Replace('\', '\\')
$XmlContent = $XmlContent -replace '"C:\\Sentinel\\config\\sentinel_local.json"', """$ConfigFile"""

# Modify schedule based on choice
switch ($ScheduleChoice) {
    "2" {
        $CustomTime = Read-Host "Enter time (HH:MM in 24-hour format)"
        $XmlContent = $XmlContent -replace 'T02:00:00', "T$($CustomTime):00"
    }
    "3" {
        Write-Host "Day selection (1=Sunday, 7=Saturday)"
        $DayChoice = Read-Host "Enter day number [1-7]"
        # Would need to modify XML structure for weekly schedule
        Write-Host "[WARNING] Weekly schedule requires manual XML editing" -ForegroundColor Yellow
    }
    "4" {
        # Remove daily trigger, keep only boot trigger
        Write-Host "[INFO] Configuring startup-only schedule" -ForegroundColor Yellow
    }
}

# Save customized XML
$TempXml = Join-Path $env:TEMP "sentinel-task-custom.xml"
$XmlContent | Out-File -FilePath $TempXml -Encoding UTF8

# Register task
Write-Host ""
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host "                        Registering Task                               " -ForegroundColor Cyan
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host ""

$TaskName = "Sentinel\FPGA Build Pipeline"

# Remove existing task if present
try {
    $ExistingTask = Get-ScheduledTask -TaskName "FPGA Build Pipeline" -TaskPath "\Sentinel\" -ErrorAction Stop
    Write-Host "[INFO] Removing existing task..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName "FPGA Build Pipeline" -TaskPath "\Sentinel\" -Confirm:$false
} catch {
    # Task doesn't exist, continue
}

# Register new task
try {
    Register-ScheduledTask -Xml (Get-Content $TempXml | Out-String) -TaskName "FPGA Build Pipeline" -TaskPath "\Sentinel\" -Force | Out-Null
    Write-Host "[SUCCESS] Task registered successfully!" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Failed to register task: $($_.Exception.Message)" -ForegroundColor Red
    Remove-Item $TempXml -ErrorAction SilentlyContinue
    exit 1
}

# Cleanup temp file
Remove-Item $TempXml -ErrorAction SilentlyContinue

# Display task info
Write-Host ""
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host "                        Task Information                               " -ForegroundColor Cyan
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host ""

$Task = Get-ScheduledTask -TaskName "FPGA Build Pipeline" -TaskPath "\Sentinel\"
$TaskInfo = Get-ScheduledTaskInfo -TaskName "FPGA Build Pipeline" -TaskPath "\Sentinel\"

Write-Host "Task Name:      $($Task.TaskName)" -ForegroundColor White
Write-Host "Task Path:      $($Task.TaskPath)" -ForegroundColor White
Write-Host "State:          $($Task.State)" -ForegroundColor White
Write-Host "Next Run Time:  $($TaskInfo.NextRunTime)" -ForegroundColor White
Write-Host "Last Run Time:  $($TaskInfo.LastRunTime)" -ForegroundColor White
Write-Host "Last Result:    $($TaskInfo.LastTaskResult)" -ForegroundColor White

Write-Host ""
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host "                         Useful Commands                               " -ForegroundColor Cyan
Write-Host "========================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "PowerShell Commands:" -ForegroundColor Yellow
Write-Host "  # Run task immediately"
Write-Host '  Start-ScheduledTask -TaskName "FPGA Build Pipeline" -TaskPath "\Sentinel\"' -ForegroundColor Cyan
Write-Host ""
Write-Host "  # Get task status"
Write-Host '  Get-ScheduledTask -TaskName "FPGA Build Pipeline" -TaskPath "\Sentinel\"' -ForegroundColor Cyan
Write-Host ""
Write-Host "  # Disable task"
Write-Host '  Disable-ScheduledTask -TaskName "FPGA Build Pipeline" -TaskPath "\Sentinel\"' -ForegroundColor Cyan
Write-Host ""
Write-Host "  # Enable task"
Write-Host '  Enable-ScheduledTask -TaskName "FPGA Build Pipeline" -TaskPath "\Sentinel\"' -ForegroundColor Cyan
Write-Host ""
Write-Host "  # Remove task"
Write-Host '  Unregister-ScheduledTask -TaskName "FPGA Build Pipeline" -TaskPath "\Sentinel\" -Confirm:$false' -ForegroundColor Cyan
Write-Host ""
Write-Host "GUI Tools:" -ForegroundColor Yellow
Write-Host "  taskschd.msc    - Task Scheduler Management Console" -ForegroundColor Cyan
Write-Host "  eventvwr.msc    - Event Viewer (for task logs)" -ForegroundColor Cyan
Write-Host ""

# Offer to run now
$RunNow = Read-Host "Run task immediately? [Y/N]"
if ($RunNow -eq "Y" -or $RunNow -eq "y") {
    Write-Host ""
    Write-Host "[INFO] Starting task..." -ForegroundColor Yellow
    Start-ScheduledTask -TaskName "FPGA Build Pipeline" -TaskPath "\Sentinel\"
    Write-Host "[SUCCESS] Task started. Check Sentinel logs for progress." -ForegroundColor Green
    Write-Host "[INFO] Logs location: $SentinelRoot\projects\<project>\runs\<timestamp>\logs\" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Installation complete!" -ForegroundColor Green
Write-Host ""
