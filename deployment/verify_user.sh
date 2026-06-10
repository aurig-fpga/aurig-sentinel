#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 LogiMentor S.r.l.

# Verification script to check which user Sentinel is running as
# This works for both user-level and system-wide installations

set -e

echo "=========================================="
echo "Sentinel User Verification Script"
echo "=========================================="
echo ""

# Check if running as root (needed for system-wide checks)
if [[ $EUID -eq 0 ]]; then
    SUDO=""
else
    SUDO="sudo"
fi

echo "1. Checking Sentinel system user..."
echo "-----------------------------------"
if id sentinel &>/dev/null; then
    echo "✓ Sentinel system user exists"
    id sentinel
    echo ""
    echo "  Home: $(getent passwd sentinel | cut -d: -f6)"
    echo "  Shell: $(getent passwd sentinel | cut -d: -f7)"
    echo "  Groups: $(groups sentinel 2>/dev/null || echo 'none')"
else
    echo "✗ Sentinel system user does NOT exist"
    echo "  → This is normal if using user-level installation"
fi

echo ""
echo "2. Checking systemd service configuration..."
echo "--------------------------------------------"

# Check system-wide service
if systemctl list-unit-files | grep -q "^sentinel.service"; then
    echo "✓ System-wide service found"
    SERVICE_USER=$($SUDO systemctl show sentinel.service -p User --value)
    if [[ -n "$SERVICE_USER" ]]; then
        echo "  Configured to run as: $SERVICE_USER"
    else
        echo "  ⚠ No User= setting (will run as root - NOT RECOMMENDED!)"
    fi
else
    echo "✗ System-wide service not found"
fi

# Check user-level service
if systemctl --user list-unit-files 2>/dev/null | grep -q "^sentinel.service"; then
    echo "✓ User-level service found"
    USER_SERVICE_USER=$(systemctl --user show sentinel.service -p User --value 2>/dev/null || echo "")
    echo "  Running as: $(whoami) (user-level service)"
else
    echo "✗ User-level service not found"
fi

echo ""
echo "3. Checking running processes..."
echo "---------------------------------"
if pgrep -f "sentinel.*--config" &>/dev/null; then
    echo "✓ Sentinel process is running:"
    ps aux | grep -E "sentinel.*--config" | grep -v grep | while read line; do
        USER=$(echo "$line" | awk '{print $1}')
        PID=$(echo "$line" | awk '{print $2}')
        CMD=$(echo "$line" | awk '{for(i=11;i<=NF;i++) printf $i" "; print ""}')
        echo "  User: $USER | PID: $PID"
        echo "  Command: $CMD"
    done
else
    echo "✗ No Sentinel processes currently running"
fi

echo ""
echo "4. Checking file ownership..."
echo "-----------------------------"

# Check system-wide paths
if [[ -d /var/log/sentinel ]]; then
    echo "System logs (/var/log/sentinel):"
    ls -ld /var/log/sentinel
    if [[ -n "$(ls -A /var/log/sentinel 2>/dev/null)" ]]; then
        echo "  Latest files:"
        ls -lht /var/log/sentinel | head -3
    fi
else
    echo "✗ /var/log/sentinel not found"
fi

if [[ -d /var/lib/sentinel ]]; then
    echo ""
    echo "System data (/var/lib/sentinel):"
    ls -ld /var/lib/sentinel
    if [[ -d /var/lib/sentinel/projects ]]; then
        LATEST_RUN=$(ls -td /var/lib/sentinel/projects/*/* 2>/dev/null | head -1)
        if [[ -n "$LATEST_RUN" ]]; then
            echo "  Latest run: $LATEST_RUN"
            ls -ld "$LATEST_RUN"
        fi
    fi
else
    echo "✗ /var/lib/sentinel not found"
fi

# Check user-level paths
USER_HOME=$(eval echo ~$(whoami))
if [[ -d "$USER_HOME/Sentinel/projects" ]]; then
    echo ""
    echo "User-level projects ($USER_HOME/Sentinel/projects):"
    LATEST_USER_RUN=$(ls -td "$USER_HOME/Sentinel/projects"/*/* 2>/dev/null | head -1)
    if [[ -n "$LATEST_USER_RUN" ]]; then
        echo "  Latest run: $LATEST_USER_RUN"
        ls -ld "$LATEST_USER_RUN"
    fi
fi

echo ""
echo "5. Testing 'whoami' in service context..."
echo "------------------------------------------"
if systemctl list-unit-files | grep -q "^sentinel.service"; then
    echo "System service configured user:"
    $SUDO systemctl show sentinel.service -p User,UID,GID --value 2>/dev/null || echo "  Not configured"
fi

if systemctl --user list-unit-files 2>/dev/null | grep -q "^sentinel.service"; then
    echo "User service runs as: $(whoami) (UID: $(id -u))"
fi

echo ""
echo "=========================================="
echo "Verification Summary"
echo "=========================================="
echo ""

if id sentinel &>/dev/null && systemctl list-unit-files | grep -q "^sentinel.service"; then
    SERVICE_USER=$($SUDO systemctl show sentinel.service -p User --value)
    if [[ "$SERVICE_USER" == "sentinel" ]]; then
        echo "✓ SYSTEM-WIDE INSTALLATION DETECTED"
        echo "  Service configured to run as 'sentinel' user"
        echo "  Files should be owned by 'sentinel:sentinel'"
    else
        echo "⚠ MIXED CONFIGURATION"
        echo "  Sentinel user exists but service not configured correctly"
    fi
elif systemctl --user list-unit-files 2>/dev/null | grep -q "^sentinel.service"; then
    echo "✓ USER-LEVEL INSTALLATION DETECTED"
    echo "  Service runs as: $(whoami)"
    echo "  Files owned by: $(whoami)"
else
    echo "✗ NO SERVICE INSTALLATION DETECTED"
    echo "  Run deployment/systemd/install.sh (user-level)"
    echo "  or deployment/systemd/install-system.sh (system-wide)"
fi

echo ""
