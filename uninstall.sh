#!/bin/sh
set -e

echo "=== SuperMicro Fan Controller Uninstaller ==="
echo

# Check if running as root
if [ "$(id -u)" -ne 0 ]; then 
    echo "ERROR: Please run as root (use: su -c './uninstall.sh' or login as root)"
    exit 1
fi

# Detect init system
if command -v systemctl >/dev/null 2>&1; then
    INIT_SYSTEM="systemd"
    echo "Detected: systemd"
elif command -v rc-service >/dev/null 2>&1; then
    INIT_SYSTEM="openrc"
    echo "Detected: OpenRC"
else
    echo "WARNING: Unknown init system, will attempt to clean up files only"
    INIT_SYSTEM="unknown"
fi

echo
echo "⚠️  WARNING: Uninstalling will cause fans to return to default"
echo "             control - likely maintaining last set value until a "
echo "             reboot or user set value in IPMI."
echo
printf "Continue with uninstall? (y/n) "
read -r REPLY
echo
if [ "$REPLY" != "y" ] && [ "$REPLY" != "Y" ]; then
    echo "Uninstall cancelled"
    exit 0
fi

echo "=== Stopping and Removing Service ==="

# Stop and disable service
if [ "$INIT_SYSTEM" = "systemd" ]; then
    # Check if service exists and is active
    if systemctl is-active --quiet fan-controller 2>/dev/null; then
        echo "Stopping service..."
        systemctl stop fan-controller
        echo "✓ Service stopped"
    else
        echo "Service not running (or not found)"
    fi
    
    # Check if service is enabled
    if systemctl is-enabled --quiet fan-controller 2>/dev/null; then
        echo "Disabling service..."
        systemctl disable fan-controller
        echo "✓ Service disabled"
    fi
    
    # Remove service file
    if [ -f /etc/systemd/system/fan-controller.service ]; then
        echo "Removing service file..."
        rm /etc/systemd/system/fan-controller.service
        systemctl daemon-reload
        echo "✓ Service file removed"
    else
        echo "Service file not found (may already be removed)"
    fi

elif [ "$INIT_SYSTEM" = "openrc" ]; then
    # Check if service is running
    if rc-service fan-controller status >/dev/null 2>&1; then
        echo "Stopping service..."
        rc-service fan-controller stop 2>/dev/null || true
        echo "✓ Service stopped"
    else
        echo "Service not running (or not found)"
    fi
    
    # Check if service is enabled
    if rc-update show default 2>/dev/null | grep -q fan-controller; then
        echo "Disabling service..."
        rc-update del fan-controller default
        echo "✓ Service disabled"
    fi
    
    # Remove service file
    if [ -f /etc/init.d/fan-controller ]; then
        echo "Removing service file..."
        rm /etc/init.d/fan-controller
        echo "✓ Service file removed"
    else
        echo "Service file not found (may already be removed)"
    fi
fi

echo
echo "=== Removing Program Files ==="

# Remove installation directory
if [ -d /opt/fan-controller ]; then
    echo
    echo "The installation directory contains:"
    ls -lh /opt/fan-controller/
    echo
    printf "Remove all files from /opt/fan-controller? (y/n) "
    read -r REPLY
    echo
    if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
        rm -rf /opt/fan-controller
        echo "✓ Program files removed"
    else
        echo "✓ Program files kept at /opt/fan-controller"
        echo "  (You can manually remove them later with: rm -rf /opt/fan-controller)"
    fi
else
    echo "Installation directory not found (may already be removed)"
fi

# Remove log files
echo
echo "=== Cleaning Up Log Files ==="

LOGS_FOUND=0

if [ -f /var/log/fan-controller.log ]; then
    echo "Found: /var/log/fan-controller.log"
    LOGS_FOUND=1
fi

if [ -f /var/log/fan-controller.err ]; then
    echo "Found: /var/log/fan-controller.err"
    LOGS_FOUND=1
fi

if [ $LOGS_FOUND -eq 1 ]; then
    printf "Remove log files? (y/n) "
    read -r REPLY
    echo
    if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
        rm -f /var/log/fan-controller.log
        rm -f /var/log/fan-controller.err
        echo "✓ Log files removed"
    else
        echo "✓ Log files kept"
    fi
else
    echo "No log files found"
fi

# Check for any remaining PID files
if [ -f /run/fan-controller.pid ]; then
    echo "Removing PID file..."
    rm -f /run/fan-controller.pid
    echo "✓ PID file removed"
fi

echo
echo "=== Uninstall Summary ==="
echo

# Check what's left
REMNANTS=0

if [ -d /opt/fan-controller ]; then
    echo "Remaining: /opt/fan-controller/ (kept by user choice)"
    REMNANTS=1
fi

if [ -f /var/log/fan-controller.log ] || [ -f /var/log/fan-controller.err ]; then
    echo "Remaining: Log files in /var/log/ (kept by user choice)"
    REMNANTS=1
fi

if [ -f /etc/systemd/system/fan-controller.service ]; then
    echo "WARNING: Service file still exists: /etc/systemd/system/fan-controller.service"
    REMNANTS=1
fi

if [ -f /etc/init.d/fan-controller ]; then
    echo "WARNING: Service file still exists: /etc/init.d/fan-controller"
    REMNANTS=1
fi

if [ $REMNANTS -eq 0 ]; then
    echo "✓ All components removed successfully"
else
    echo
    echo "Some files were kept or could not be removed (see above)"
fi

echo
echo "=========================================="
echo "=== Uninstall Complete ==="
echo "=========================================="
echo
echo "⚠️  Server fans have returned to default control"
echo "   (Likely running at last set value until a reboot "
echo "   or settings change in IPMI)"
echo

# Offer to restore repos backup if it exists (Alpine only)
if [ -f /etc/apk/repositories.backup ]; then
    echo "Note: A backup of your Alpine repositories file was found:"
    echo "      /etc/apk/repositories.backup"
    echo "      (Created when community repo was enabled during installation)"
    echo
    printf "Restore original repositories file? (y/n) "
    read -r REPLY
    if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
        mv /etc/apk/repositories.backup /etc/apk/repositories
        echo "✓ Original repositories file restored"
    fi
fi

echo
echo "Uninstall process complete."
