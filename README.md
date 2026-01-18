# SuperMicro Fan Controller

Temperature-based fan speed controler for SuperMicro servers via IPMI. Runs as a linux system service with web-based monitoring and configuration.

## Features

- **Dynamic Fan Control**: Adjusts fan speeds based on temperature ranges
- **Multi-Zone Support**: Independent control for CPU and peripheral fan zones
- **Safety Systems**: 
  - Safety floor enforcement when any sensor exceeds threshold
  - Automatic high-speed mode during thermal events
  - Error-safe fallback speeds
- **Web Interface**: Real-time monitoring and configuration
  - Live sensor readings with full IPMI sensor table
  - Adjustable temperature thresholds
  - Percentage-based fan speed control with transparent hex conversion
  - Service restart capability
- **Static Peripheral Mode**: Optional fixed-speed peripheral fans (useful when no PCIe cards installed)
- **Flexible Polling**: Adjustable intervals based on thermal load
- **Comprehensive Logging**: Optional syslog integration (or just do this in IPMI natively)
- **Multi-Platform**: Supports both systemd and OpenRC init systems, built for Alpine Linux

## Quick Start

### Prerequisites

**BEFORE INSTALLING: Configure your SuperMicro BMC fan mode**

Set BMC to "Full" mode via IPMI web interface (Configuration → Fan Mode) or via command:

```bash
ipmitool -I lanplus -H <IPMI_IP> -U <USER> -P <PASS> raw 0x30 0x45 0x01 0x01
```

This ensures the controller has full control without BMC interference. See the Configuration section for details.

### Bare Alpine Linux Installation

The installer handles everything automatically on a fresh Alpine VM:

```bash
# Download and extract the files
tar -xzf fan-controller.tar.gz
cd fan-controller

# Run installer (handles repo setup, dependencies, service installation)
su -c './install.sh'

# Edit configuration with your IPMI settings
vi /opt/fan-controller/config.yaml

# Start the service
rc-service fan-controller restart
```

The installer will:
1. Enable the Alpine community repository (required for ipmitool)
2. Install all dependencies (bash, python3, py3-yaml, ipmitool)
3. Set up the service to auto-start on boot
4. Configure logging

### Other Linux Distributions

For Debian/Ubuntu or RHEL-based systems (but just use Alpine, it's super light and works fine):

```bash
# Install dependencies first
apt install python3 python3-yaml python3-systemd ipmitool  # Debian/Ubuntu
# or
dnf install python3 python3-pyyaml python3-systemd ipmitool  # RHEL/Rocky

# Run installer
sudo ./install.sh
```

## Configuration

Edit `/opt/fan-controller/config.yaml`:

### BMC Fan Mode Setting

**Before using this controller, configure your SuperMicro BMC fan mode:**

- **Full Speed Mode**: 
  - BMC sets fans to 100% initially
  - This controller takes over and sets speeds via IPMI raw commands
  - If service stops: Fans maintain their last commanded speed

- **Standard/Optimal/Heavy I/O Modes**:
  - BMC actively manages fans based on its own thermal logic
  - This controller and BMC compete for control but with a low polling interval you probably won't notice
  - If service stops: BMC should resume control within seconds to minutes

**Setting Fan Mode via IPMITOOL** (alternative to web interface):

```bash
# Set to Full Speed mode
ipmitool -I lanplus -H <IPMI_IP> -U <USER> -P <PASS> raw 0x30 0x45 0x01 0x01

# Check current fan mode
ipmitool -I lanplus -H <IPMI_IP> -U <USER> -P <PASS> raw 0x30 0x45 0x00
# Returns: 00 = Standard, 01 = Full, 02 = Optimal, 04 = Heavy I/O
```

### Essential Settings

```yaml
ipmi:
  host: "10.1.2.3"      # Your server's IPMI IP
  username: "ADMIN"         # IPMI username
  password: "ADMIN"         # IPMI password

thresholds:
  moderate: 50      # Temp for moderate fan speed
  high: 78          # Temp for high fan speed
  emergency: 90     # Temp for maximum fan speed
  safety_floor: 95  # Any sensor above this enforces minimum speed

fan_speeds:
  idle: "0x04"              # 6% duty cycle
  moderate: "0x16"          # 25% duty cycle
  high: "0x32"              # 50% duty cycle
  emergency: "0x64"         # 100% duty cycle
  safety_floor_speed: "0x24" # 36% minimum when safety triggered
```

### Optional: Static Peripheral Fan Control

Useful when you have no PCIe cards and want peripheral fans at a fixed low speed:

```yaml
static_peripheral:
  enabled: true          # Enable static speed mode
  speed: "0x04"         # Fixed speed (6% duty cycle)
```

When enabled:
- Peripheral fans run at the specified speed regardless of temperature
- Safety floor still overrides if any sensor gets too hot
- CPU fans continue temperature-based control

### Sensor Mapping

Map sensors to fan zones:

```yaml
sensors:
  cpu_zone:
    - "CPU Temp"
    - "PCH Temp"
  peripheral_zone:
    - "Peripheral Temp"

fan_zones:
  cpu: "0x00"        # Main CPU fans
  peripheral: "0x01"  # Peripheral/PCIe fans
```

To find your server's sensor names:
```bash
ipmitool -I lanplus -H <IPMI_IP> -U <USER> -P <PASS> sensor
```

## Web Interface

Access at `http://<VM_IP>` (default credentials: ADMIN/ADMIN)

Features:
- **Live Sensor Table**: All IPMI sensors with color-coded temperature warnings
- **Configuration Forms**: 
  - Temperature thresholds (Celsius)
  - Fan speeds (percentage sliders with hex display)
  - Static peripheral fan control (checkbox + speed slider)
  - Polling intervals
- **Service Control**: Restart button
- **Auto-refresh**: Status updates every 5 seconds

### Firewall Configuration (Alpine)

If the web interface isn't accessible from other machines:

```bash
apk add iptables
iptables -A INPUT -p tcp --dport 8080 -j ACCEPT
rc-service iptables save
```

## Service Management

### OpenRC (Alpine)

```bash
rc-service fan-controller status    # Check status
rc-service fan-controller start     # Start service
rc-service fan-controller stop      # Stop service
rc-service fan-controller restart   # Restart service
tail -f /var/log/fan-controller.log # View logs
rc-update del fan-controller default # Disable auto-start
```

### systemd (Ubuntu/Debian/RHEL)

```bash
systemctl status fan-controller     # Check status
systemctl start fan-controller      # Start service
systemctl stop fan-controller       # Stop service
systemctl restart fan-controller    # Restart service
journalctl -u fan-controller -f     # View logs
systemctl disable fan-controller    # Disable auto-start
```

## How It Works

### Control Loop

1. **Temperature Reading**: Polls all configured IPMI sensors
2. **Zone Analysis**: Determines maximum temperature per fan zone
3. **Speed Calculation**: Selects appropriate fan speed based on thresholds
4. **Static Override**: Applies static peripheral speed if enabled
5. **Safety Check**: Enforces safety floor if any sensor exceeds threshold
6. **IPMI Command**: Sets fan speeds via `ipmitool raw` commands
7. **Adaptive Polling**: Adjusts poll interval based on thermal load

### Fan Speed Hex Values

SuperMicro expects duty cycle as hex (0x00-0x64):
- `0x00` = 0% (sets fans to minimum RPM)
- `0x32` = 50% duty cycle
- `0x64` = 100% duty cycle

The web interface provides percentage sliders with automatic hex conversion.

### Safety Features

1. **Safety Floor**: If ANY sensor exceeds `safety_floor` threshold, ALL fans run at minimum `safety_floor_speed`
2. **Error-Safe Speed**: If sensor reads fail, fans default to 50% (`error_safe`)
3. **Adaptive Polling**: During high/emergency thermal events, polling accelerates to 5s (configurable)
4. **Alert System**: Logs warnings for sustained high load and multiple thermal events

## Uninstallation

```bash
su -c './uninstall.sh'
```

The uninstaller:
- Stops and disables the service
- Removes service files
- Optionally removes program files and logs
- Optionally restores original Alpine repository configuration

**What happens to fans after uninstall:**
- **Full Speed mode**: Fans maintain their last commanded speed
- **Other modes**: BMC resumes control (probably)

## File Locations

```
/opt/fan-controller/
├── fan-controller.py    # Main program
└── config.yaml         # Configuration

/etc/init.d/fan-controller           # OpenRC service (Alpine)
/etc/systemd/system/fan-controller.service  # systemd service

/var/log/fan-controller.log  # Standard output (OpenRC)
/var/log/fan-controller.err  # Error output (OpenRC)
```

## Troubleshooting

### Service won't start

```bash
# Check service status
rc-service fan-controller status

# Check logs
tail -f /var/log/fan-controller.log
tail -f /var/log/fan-controller.err
```

Common issues:
1. **Wrong IPMI credentials**: Check `ipmi.host`, `ipmi.username`, `ipmi.password` in config.yaml
2. **Network unreachable**: Ensure VM can reach IPMI IP
3. **Missing dependencies**: Re-run `./install.sh`

### Fans running at 100%

**If fans are at 100% on initial setup:**
1. **Expected behavior**: When BMC is in "Full Speed" mode, fans start at 100%
2. **Controller takes over**: Within 15 seconds (one poll cycle), this service will reduce them
3. **Not a problem**: This is how "Full Speed" mode works - BMC sets 100%, we override

**If fans won't reduce from 100%:**
1. Check service is running: `rc-service fan-controller status`
2. Check logs for IPMI errors: `tail -f /var/log/fan-controller.log`
3. Verify IPMI credentials in config.yaml
4. Test IPMI manually: `ipmitool -I lanplus -H <IP> -U <USER> -P <PASS> sensor`

**If fans are stuck at 100% after service stops:**
- **Full Speed mode**: This shouldn't happen - fans should stay at last commanded speed
- **Other modes**: Expected - BMC has resumed control
- **Solution**: Restart the service or change BMC to "Full Speed" mode

**Unexpected 100% during operation:**
- Emergency threshold exceeded (check temps in web interface)
- IPMI communication failure (check logs)
- Safety floor triggered (check for hot sensor in web interface)

### Web interface not accessible

1. Check service is running: `rc-service fan-controller status`
2. Check port in config: `grep port /opt/fan-controller/config.yaml`
3. Configure firewall (see Firewall Configuration above)
4. Test locally: `curl http://localhost:8080` (should ask for auth)

### Static peripheral mode not working

Ensure you've:
1. Enabled the checkbox in the web interface
2. Set a speed value
3. Clicked "Update Fan Speeds"
4. Waited one poll cycle (~15 seconds)

Check the main log for "static" state on peripheral fans.

## Development

### Testing IPMI Commands

Test sensor reads:
```bash
ipmitool -I lanplus -H <IP> -U <USER> -P <PASS> sensor
```

Test fan control:
```bash
# Set CPU fans to 50% (0x32)
ipmitool -I lanplus -H <IP> -U <USER> -P <PASS> \
  raw 0x30 0x70 0x66 0x01 0x00 0x32

# Set peripheral fans to 25% (0x10)
ipmitool -I lanplus -H <IP> -U <USER> -P <PASS> \
  raw 0x30 0x70 0x66 0x01 0x01 0x10
```

### Manual Execution

For testing without the service:
```bash
cd /opt/fan-controller
python3 fan-controller.py
```

Press Ctrl+C to stop (fans will go to 100%).

## License

MIT License - See LICENSE file

## Credits

Created for SuperMicro X10/X11/X12 series servers with IPMI 2.0 support.

## Support

For issues, feature requests, or contributions, please open an issue on GitHub.

---

**⚠️ Important Safety Notes:**

1. **Configure BMC to "Full Speed" mode** - This prevents control conflicts and provides safe failover behavior
2. **In "Full Speed" mode, stopping the service keeps fans at last speed** - Not dangerous, but verify temps if stopped
3. **In other modes, stopping causes BMC to resume control** - May spike fans to 100% during transition
4. **Monitor your temperatures** - Start with conservative settings and adjust gradually
5. **Test thoroughly** - Verify your thresholds are appropriate for your hardware and workload
6. **Keep BMC updated** - Ensure your server's BMC firmware is up to date
7. **Safety floor is your friend** - Set `safety_floor` threshold conservatively (e.g., 90°C)

**How this controller works:**
- Sends IPMI raw commands to directly set fan duty cycles
- Does NOT change BMC fan mode (you set this once in BMC settings)
- In "Full Speed" mode: Controller has full control, BMC stays hands-off
- In other modes: Controller and BMC compete (not recommended)

This software controls critical thermal management. Use at your own risk. Always monitor temperatures when making changes.