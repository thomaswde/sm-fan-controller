#!/bin/sh
set -e

echo "=== SuperMicro Fan Controller Installer ==="
echo

# Check if running as root
if [ "$(id -u)" -ne 0 ]; then 
    echo "ERROR: Please run as root (use: su -c './install.sh' or login as root)"
    exit 1
fi

# Detect OS and init system
if [ -f /etc/alpine-release ]; then
    OS="alpine"
    INIT_SYSTEM="openrc"
    echo "Detected: Alpine Linux $(cat /etc/alpine-release)"
elif command -v systemctl >/dev/null 2>&1; then
    OS="systemd-linux"
    INIT_SYSTEM="systemd"
    echo "Detected: systemd-based Linux"
elif command -v rc-service >/dev/null 2>&1; then
    OS="other-openrc"
    INIT_SYSTEM="openrc"
    echo "Detected: OpenRC-based Linux"
else
    echo "ERROR: Unknown operating system or init system"
    exit 1
fi

echo "Init system: $INIT_SYSTEM"
echo

# Alpine-specific setup
if [ "$OS" = "alpine" ]; then
    echo "=== Alpine Linux Setup ==="
    
    # Check if community repo is enabled
    if ! grep -q "^[^#]*community" /etc/apk/repositories; then
        echo "Enabling community repository..."
        # Get the current release version
        ALPINE_VERSION=$(cat /etc/alpine-release | cut -d'.' -f1,2)
        
        # Backup repositories file
        cp /etc/apk/repositories /etc/apk/repositories.backup
        
        # Enable community repo
        sed -i "s|#\(.*/$ALPINE_VERSION/community\)|\1|" /etc/apk/repositories
        
        # If sed didn't work (repo line doesn't exist), add it
        if ! grep -q "^[^#]*community" /etc/apk/repositories; then
            echo "http://dl-cdn.alpinelinux.org/alpine/v$ALPINE_VERSION/community" >> /etc/apk/repositories
        fi
        
        echo "✓ Community repository enabled"
    else
        echo "✓ Community repository already enabled"
    fi
    
    # Update package index
    echo "Updating package index..."
    apk update
    echo
fi

# Install dependencies
echo "=== Installing Dependencies ==="

if [ "$OS" = "alpine" ]; then
    # Alpine packages
    PACKAGES_TO_INSTALL=""
    
    # Check bash
    if ! command -v bash >/dev/null 2>&1; then
        echo "  - bash needed"
        PACKAGES_TO_INSTALL="$PACKAGES_TO_INSTALL bash"
    fi
    
    # Check python3
    if ! command -v python3 >/dev/null 2>&1; then
        echo "  - python3 needed"
        PACKAGES_TO_INSTALL="$PACKAGES_TO_INSTALL python3"
    fi
    
    # Check py3-yaml
    if ! python3 -c "import yaml" 2>/dev/null; then
        echo "  - py3-yaml needed"
        PACKAGES_TO_INSTALL="$PACKAGES_TO_INSTALL py3-yaml"
    fi
    
    # Check ipmitool (requires community repo)
    if ! command -v ipmitool >/dev/null 2>&1; then
        echo "  - ipmitool needed (from community repo)"
        PACKAGES_TO_INSTALL="$PACKAGES_TO_INSTALL ipmitool"
    fi
    
    if [ -n "$PACKAGES_TO_INSTALL" ]; then
        echo
        echo "Installing: $PACKAGES_TO_INSTALL"
        apk add $PACKAGES_TO_INSTALL
        echo "✓ Dependencies installed"
    else
        echo "✓ All dependencies already installed"
    fi
    
else
    # Non-Alpine systems
    MISSING_DEPS=""
    
    if ! command -v python3 >/dev/null 2>&1; then
        MISSING_DEPS="$MISSING_DEPS python3"
    fi
    
    if ! command -v ipmitool >/dev/null 2>&1; then
        MISSING_DEPS="$MISSING_DEPS ipmitool"
    fi
    
    if ! python3 -c "import yaml" 2>/dev/null; then
        MISSING_DEPS="$MISSING_DEPS python3-yaml"
    fi
    
    if [ "$INIT_SYSTEM" = "systemd" ]; then
        if ! python3 -c "import systemd.daemon" 2>/dev/null; then
            MISSING_DEPS="$MISSING_DEPS python3-systemd"
        fi
    fi
    
    if [ -n "$MISSING_DEPS" ]; then
        echo "ERROR: Missing dependencies: $MISSING_DEPS"
        echo
        echo "Install them with:"
        echo "  Debian/Ubuntu: apt install python3 python3-yaml python3-systemd ipmitool"
        echo "  RHEL/Rocky: dnf install python3 python3-pyyaml python3-systemd ipmitool"
        exit 1
    fi
    
    echo "✓ All dependencies found"
fi

echo

# Create installation directory
INSTALL_DIR="/opt/fan-controller"
echo "=== Installing Fan Controller ==="
echo "Installation directory: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

# Copy files
echo "Copying program files..."
if [ ! -f "fan-controller.py" ]; then
    echo "ERROR: fan-controller.py not found in current directory"
    exit 1
fi

cp fan-controller.py "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/fan-controller.py"
echo "✓ fan-controller.py installed"

# Handle config file
if [ ! -f "$INSTALL_DIR/config.yaml" ]; then
    if [ -f "config.yaml" ]; then
        echo "Copying existing config.yaml"
        cp config.yaml "$INSTALL_DIR/"
        CONFIG_ACTION="copied"
    elif [ -f "config.yaml.example" ]; then
        echo "Creating config.yaml from example"
        cp config.yaml.example "$INSTALL_DIR/config.yaml"
        CONFIG_ACTION="created from example"
    else
        echo "ERROR: No config file found (config.yaml or config.yaml.example)"
        echo "Please create a config.yaml file"
        exit 1
    fi
else
    echo "✓ Existing config.yaml found, not overwriting"
    CONFIG_ACTION="preserved existing"
fi

echo

# Install service
if [ "$INIT_SYSTEM" = "systemd" ]; then
    echo "=== Installing systemd Service ==="
    
    if [ ! -f "fan-controller.service" ]; then
        echo "ERROR: fan-controller.service not found"
        exit 1
    fi
    
    cp fan-controller.service /etc/systemd/system/
    systemctl daemon-reload
    echo "✓ Service file installed"
    
    echo
    printf "Enable and start fan-controller service now? (y/n) "
    read -r REPLY
    echo
    if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
        systemctl enable fan-controller
        systemctl start fan-controller
        echo "✓ Service enabled and started"
        SERVICE_STATUS="enabled and running"
    else
        echo "Service installed but not started."
        echo "To start manually:"
        echo "  systemctl enable fan-controller"
        echo "  systemctl start fan-controller"
        SERVICE_STATUS="installed but not started"
    fi
    
elif [ "$INIT_SYSTEM" = "openrc" ]; then
    echo "=== Installing OpenRC Service ==="
    
    if [ ! -f "fan-controller.openrc" ]; then
        echo "ERROR: fan-controller.openrc not found"
        exit 1
    fi
    
    cp fan-controller.openrc /etc/init.d/fan-controller
    chmod +x /etc/init.d/fan-controller
    echo "✓ Service file installed"
    
    echo
    printf "Enable and start fan-controller service now? (y/n) "
    read -r REPLY
    echo
    if [ "$REPLY" = "y" ] || [ "$REPLY" = "Y" ]; then
        rc-update add fan-controller default
        rc-service fan-controller start
        echo "✓ Service enabled and started"
        SERVICE_STATUS="enabled and running"
    else
        echo "Service installed but not started."
        echo "To start manually:"
        echo "  rc-update add fan-controller default"
        echo "  rc-service fan-controller start"
        SERVICE_STATUS="installed but not started"
    fi
fi

echo
echo "=========================================="
echo "=== Installation Complete ==="
echo "=========================================="
echo
echo "Configuration: $CONFIG_ACTION"
echo "Service: $SERVICE_STATUS"
echo

# Check if web interface is enabled
if grep -q "enabled: true" "$INSTALL_DIR/config.yaml" 2>/dev/null; then
    WEB_PORT=$(grep "port:" "$INSTALL_DIR/config.yaml" | grep -v "^#" | head -1 | awk '{print $2}')
    echo "=== Web Interface ==="
    echo "Status: Enabled"
    echo "URL: http://$(hostname -I | awk '{print $1}'):${WEB_PORT:-8080}"
    echo "     (or http://<VM_IP>:${WEB_PORT:-8080})"
    
    if [ "$OS" = "alpine" ]; then
        echo
        echo "Note: If you cannot access the web interface from another"
        echo "machine, you may need to configure the firewall:"
        echo "  apk add iptables"
        echo "  iptables -A INPUT -p tcp --dport ${WEB_PORT:-8080} -j ACCEPT"
        echo "  rc-service iptables save"
        echo
        echo "Or disable the firewall (not recommended for production):"
        echo "  rc-service iptables stop"
    fi
fi

echo
echo "=== File Locations ==="
echo "Program: /opt/fan-controller/fan-controller.py"
echo "Config: /opt/fan-controller/config.yaml"

if [ "$INIT_SYSTEM" = "systemd" ]; then
    echo "Service: /etc/systemd/system/fan-controller.service"
    echo "Logs: journalctl -u fan-controller -f"
elif [ "$INIT_SYSTEM" = "openrc" ]; then
    echo "Service: /etc/init.d/fan-controller"
    echo "Logs: /var/log/fan-controller.log"
    echo "      /var/log/fan-controller.err"
fi

echo
echo "=== Quick Reference ==="

if [ "$INIT_SYSTEM" = "systemd" ]; then
    echo "Status:   systemctl status fan-controller"
    echo "Logs:     journalctl -u fan-controller -f"
    echo "Restart:  systemctl restart fan-controller"
    echo "Stop:     systemctl stop fan-controller (fans go to 100%!)"
    echo "Disable:  systemctl disable fan-controller"
elif [ "$INIT_SYSTEM" = "openrc" ]; then
    echo "Status:   rc-service fan-controller status"
    echo "Logs:     tail -f /var/log/fan-controller.log"
    echo "Restart:  rc-service fan-controller restart"
    echo "Stop:     rc-service fan-controller stop (fans go to 100%!)"
    echo "Disable:  rc-update del fan-controller default"
fi

echo
echo "⚠️  IMPORTANT: Edit $INSTALL_DIR/config.yaml"
echo "   with your IPMI settings before starting the service!"
echo

if [ "$CONFIG_ACTION" = "created from example" ]; then
    echo "⚠️  Configuration created from example - YOU MUST EDIT IT"
    echo "   At minimum, update:"
    echo "   - ipmi.host (your server's IPMI IP address)"
    echo "   - ipmi.username and ipmi.password"
    echo "   - Temperature thresholds for your environment"
    echo
    echo "   After editing, restart the service:"
    if [ "$INIT_SYSTEM" = "systemd" ]; then
        echo "   systemctl restart fan-controller"
    else
        echo "   rc-service fan-controller restart"
    fi
    echo
fi

echo "Installation successful!"
