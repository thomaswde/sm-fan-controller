#!/usr/bin/env python3
"""
SuperMicro Fan Controller
Dynamically controls server fan speeds based on temperature readings via IPMI
"""

import subprocess
import time
import sys
import yaml
from pathlib import Path
from collections import deque
from datetime import datetime, timedelta
import threading
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs
import hashlib

# Syslog support
try:
    import syslog
    SYSLOG_AVAILABLE = True
except ImportError:
    SYSLOG_AVAILABLE = False

# Watchdog support
try:
    import systemd.daemon
    SYSTEMD_WATCHDOG = True
except ImportError:
    SYSTEMD_WATCHDOG = False

# Syslog facility mapping
SYSLOG_FACILITIES = {
    'USER': syslog.LOG_USER,
    'DAEMON': syslog.LOG_DAEMON,
    'LOCAL0': syslog.LOG_LOCAL0,
    'LOCAL1': syslog.LOG_LOCAL1,
    'LOCAL2': syslog.LOG_LOCAL2,
    'LOCAL3': syslog.LOG_LOCAL3,
    'LOCAL4': syslog.LOG_LOCAL4,
    'LOCAL5': syslog.LOG_LOCAL5,
    'LOCAL6': syslog.LOG_LOCAL6,
    'LOCAL7': syslog.LOG_LOCAL7,
}

class WebInterface:
    """Simple web interface for monitoring and configuration"""
    
    def __init__(self, controller, config):
        self.controller = controller
        self.config = config
        self.server = None
        self.server_thread = None
        
    def check_auth(self, auth_header):
        """Verify basic authentication"""
        if not auth_header:
            return False
        
        try:
            auth_type, auth_string = auth_header.split(' ', 1)
            if auth_type.lower() != 'basic':
                return False
            
            decoded = base64.b64decode(auth_string).decode('utf-8')
            username, password = decoded.split(':', 1)
            
            return (username == self.config['web_interface']['auth']['username'] and
                    password == self.config['web_interface']['auth']['password'])
        except Exception:
            return False
    
    def create_handler(self):
        """Create request handler with access to controller"""
        controller = self.controller
        web_config = self.config['web_interface']
        parent = self
        
        class RequestHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                """Suppress default logging"""
                pass
            
            def do_AUTHCHECK(self):
                """Check authentication and send 401 if needed"""
                auth_header = self.headers.get('Authorization')
                if not parent.check_auth(auth_header):
                    self.send_response(401)
                    self.send_header('WWW-Authenticate', 'Basic realm="Fan Controller"')
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()
                    self.wfile.write(b'Authentication required')
                    return False
                return True
            
            def do_GET(self):
                """Handle GET requests"""
                if not self.do_AUTHCHECK():
                    return
                
                if self.path == '/' or self.path == '/index.html':
                    self.serve_main_page()
                elif self.path == '/status.json':
                    self.serve_status_json()
                elif self.path == '/config.json':
                    self.serve_config_json()
                else:
                    self.send_error(404)
            
            def do_POST(self):
                """Handle POST requests"""
                if not self.do_AUTHCHECK():
                    return
                
                if self.path == '/update_config':
                    self.handle_config_update()
                elif self.path == '/restart_service':
                    self.handle_restart()
                else:
                    self.send_error(404)
            
            def serve_main_page(self):
                """Serve the main HTML page"""
                html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>SuperMicro Fan Controller</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: monospace; margin: 20px; background: #1e1e1e; color: #d4d4d4; }
        .container { max-width: 1400px; margin: 0 auto; }
        h1, h2 { color: #4ec9b0; border-bottom: 1px solid #404040; padding-bottom: 10px; }
        .section { background: #252526; padding: 15px; margin: 15px 0; border: 1px solid #404040; }
        .sensor-table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
        .sensor-table th { background: #2d2d30; color: #858585; text-align: left; padding: 8px; border-bottom: 1px solid #404040; }
        .sensor-table td { padding: 6px 8px; border-bottom: 1px solid #333; }
        .sensor-table tr:hover { background: #2d2d30; }
        .temp-critical { color: #f48771; }
        .temp-warning { color: #dcdcaa; }
        .temp-normal { color: #4ec9b0; }
        .status-ok { color: #4ec9b0; }
        .alert { color: #f48771; }
        .normal { color: #4ec9b0; }
        .warning { color: #dcdcaa; }
        input, select { 
            background: #3c3c3c; color: #d4d4d4; border: 1px solid #404040; 
            padding: 8px; margin: 5px 0; width: 100%; box-sizing: border-box;
        }
        button { 
            background: #0e639c; color: white; border: none; padding: 10px 20px; 
            cursor: pointer; margin: 5px 5px 5px 0; font-family: monospace;
        }
        button:hover { background: #1177bb; }
        button.danger { background: #a1260d; }
        button.danger:hover { background: #c72e0d; }
        .form-group { margin: 10px 0; }
        label { display: block; color: #858585; margin-bottom: 5px; }
        .timestamp { color: #858585; font-size: 0.9em; }
    </style>
</head>
<body>
    <div class="container">
        <h1>SuperMicro Fan Controller</h1>

        <div class="section">
            <h2>Current Status</h2>
            <table class="sensor-table" id="sensor-table">
                <thead>
                    <tr>
                        <th>Sensor</th>
                        <th>Reading</th>
                        <th>Unit</th>
                        <th>Status</th>
                        <th>Lower NR</th>
                        <th>Lower C</th>
                        <th>Lower NC</th>
                        <th>Upper NC</th>
                        <th>Upper C</th>
                        <th>Upper NR</th>
                    </tr>
                </thead>
                <tbody>
                    <tr><td colspan="10">Loading...</td></tr>
                </tbody>
            </table>
            <div class="timestamp" id="last-update"></div>
        </div>
        
        <div class="section">
            <h2>Temperature Thresholds</h2>
            <form id="thresholds-form">
                <div class="form-group">
                    <label>Moderate (C):</label>
                    <input type="number" name="moderate" id="moderate" min="0" max="100">
                </div>
                <div class="form-group">
                    <label>High (C):</label>
                    <input type="number" name="high" id="high" min="0" max="100">
                </div>
                <div class="form-group">
                    <label>Emergency (C):</label>
                    <input type="number" name="emergency" id="emergency" min="0" max="100">
                </div>
                <div class="form-group">
                    <label>Safety Floor (C):</label>
                    <input type="number" name="safety_floor" id="safety_floor" min="0" max="100">
                </div>
                <button type="submit">Update Thresholds</button>
            </form>
        </div>
        
        <div class="section">
            <h2>Fan Speeds</h2>
            <form id="speeds-form">
                <div class="form-group">
                    <label>Idle Speed (%):</label>
                    <input type="range" name="idle" id="speed-idle-range" min="0" max="100" value="6">
                    <input type="number" id="speed-idle-percent" min="0" max="100" value="6" style="width: 80px; display: inline-block;">%
                    <span id="speed-idle-hex" style="color: #858585; margin-left: 10px;">0x04</span>
                </div>
                <div class="form-group">
                    <label>Moderate Speed (%):</label>
                    <input type="range" name="moderate" id="speed-moderate-range" min="0" max="100" value="25">
                    <input type="number" id="speed-moderate-percent" min="0" max="100" value="25" style="width: 80px; display: inline-block;">%
                    <span id="speed-moderate-hex" style="color: #858585; margin-left: 10px;">0x10</span>
                </div>
                <div class="form-group">
                    <label>High Speed (%):</label>
                    <input type="range" name="high" id="speed-high-range" min="0" max="100" value="50">
                    <input type="number" id="speed-high-percent" min="0" max="100" value="50" style="width: 80px; display: inline-block;">%
                    <span id="speed-high-hex" style="color: #858585; margin-left: 10px;">0x32</span>
                </div>
                <div class="form-group">
                    <label>Emergency Speed (%):</label>
                    <input type="range" name="emergency" id="speed-emergency-range" min="0" max="100" value="100">
                    <input type="number" id="speed-emergency-percent" min="0" max="100" value="100" style="width: 80px; display: inline-block;">%
                    <span id="speed-emergency-hex" style="color: #858585; margin-left: 10px;">0x64</span>
                </div>
                <div class="form-group">
                    <label>Safety Floor Speed (%):</label>
                    <input type="range" name="safety_floor_speed" id="speed-safety-range" min="0" max="100" value="36">
                    <input type="number" id="speed-safety-percent" min="0" max="100" value="36" style="width: 80px; display: inline-block;">%
                    <span id="speed-safety-hex" style="color: #858585; margin-left: 10px;">0x24</span>
                </div>
                
                <div class="form-group" style="border-top: 1px solid #404040; padding-top: 15px; margin-top: 15px;">
                    <label>
                        <input type="checkbox" id="static-peripheral-enabled" style="width: auto; display: inline-block; margin-right: 5px;">
                        Static Peripheral Fan Speed
                    </label>
                    <p style="color: #858585; font-size: 0.9em; margin: 5px 0;">When enabled, peripheral fans run at fixed speed regardless of temperature (except safety floor)</p>
                </div>
                
                <div class="form-group" id="static-peripheral-control">
                    <label>Static Peripheral Speed (%):</label>
                    <input type="range" name="static_peripheral" id="speed-static-peripheral-range" min="0" max="100" value="6">
                    <input type="number" id="speed-static-peripheral-percent" min="0" max="100" value="6" style="width: 80px; display: inline-block;">%
                    <span id="speed-static-peripheral-hex" style="color: #858585; margin-left: 10px;">0x04</span>
                </div>
                
                <button type="submit">Update Fan Speeds</button>
            </form>
        </div>

        <div class="section">
            <h2>Polling Intervals</h2>
            <form id="polling-form">
                <div class="form-group">
                    <label>Normal Polling (seconds):</label>
                    <input type="number" name="normal" id="poll-normal" min="5" max="60" value="15">
                </div>
                <div class="form-group">
                    <label>High Load Polling (seconds):</label>
                    <input type="number" name="high_load" id="poll-high" min="1" max="30" value="5">
                </div>
                <button type="submit">Update Polling Intervals</button>
            </form>
        </div>
        
        <div class="section">
            <h2>Service Control</h2>
            <button class="danger" onclick="restartService()">Restart Service</button>
            <p style="color: #858585; font-size: 0.9em;">Note: Restarting will temporarily disable fan control</p>
        </div>
    </div>
    
    <script>
        let configLoaded = false;
        
        // Hex to percentage conversion (0x00 = 0%, 0x64 = 100%)
        function hexToPercent(hex) {
            const value = parseInt(hex, 16);
            return Math.round((value / 100) * 100);
        }
        
        function percentToHex(percent) {
            const value = Math.round((percent / 100) * 100);
            return '0x' + value.toString(16).padStart(2, '0');
        }
        
        function loadConfig() {
            // Load config values once on page load
            fetch('/config.json')
                .then(r => r.json())
                .then(data => {
                    // Update threshold form values
                    document.getElementById('moderate').value = data.thresholds.moderate;
                    document.getElementById('high').value = data.thresholds.high;
                    document.getElementById('emergency').value = data.thresholds.emergency;
                    document.getElementById('safety_floor').value = data.thresholds.safety_floor;
                    
                    // Update fan speed form values (convert hex to percent)
                    document.getElementById('speed-idle-percent').value = hexToPercent(data.fan_speeds.idle);
                    document.getElementById('speed-idle-range').value = hexToPercent(data.fan_speeds.idle);
                    document.getElementById('speed-idle-hex').textContent = data.fan_speeds.idle;
                    
                    document.getElementById('speed-moderate-percent').value = hexToPercent(data.fan_speeds.moderate);
                    document.getElementById('speed-moderate-range').value = hexToPercent(data.fan_speeds.moderate);
                    document.getElementById('speed-moderate-hex').textContent = data.fan_speeds.moderate;
                    
                    document.getElementById('speed-high-percent').value = hexToPercent(data.fan_speeds.high);
                    document.getElementById('speed-high-range').value = hexToPercent(data.fan_speeds.high);
                    document.getElementById('speed-high-hex').textContent = data.fan_speeds.high;
                    
                    document.getElementById('speed-emergency-percent').value = hexToPercent(data.fan_speeds.emergency);
                    document.getElementById('speed-emergency-range').value = hexToPercent(data.fan_speeds.emergency);
                    document.getElementById('speed-emergency-hex').textContent = data.fan_speeds.emergency;
                    
                    document.getElementById('speed-safety-percent').value = hexToPercent(data.fan_speeds.safety_floor_speed);
                    document.getElementById('speed-safety-range').value = hexToPercent(data.fan_speeds.safety_floor_speed);
                    document.getElementById('speed-safety-hex').textContent = data.fan_speeds.safety_floor_speed;
                    
                    // Update polling intervals
                    document.getElementById('poll-normal').value = data.polling.normal;
                    document.getElementById('poll-high').value = data.polling.high_load;
                    // Update static peripheral settings
                    document.getElementById('static-peripheral-enabled').checked = data.static_peripheral.enabled;
                    document.getElementById('speed-static-peripheral-percent').value = hexToPercent(data.static_peripheral.speed);
                    document.getElementById('speed-static-peripheral-range').value = hexToPercent(data.static_peripheral.speed);
                    document.getElementById('speed-static-peripheral-hex').textContent = data.static_peripheral.speed;
                    updateStaticPeripheralState();
                    
                    configLoaded = true;
                })
                .catch(err => console.error('Error loading config:', err));
        }
        
        function updateStatus() {
            fetch('/status.json')
                .then(r => r.json())
                .then(data => {
                    // Update sensor table
                    let tbody = '';
                    for (const sensor of data.all_sensors) {
                        let tempClass = '';
                        if (sensor.unit === 'degrees C') {
                            const temp = parseFloat(sensor.value);
                            if (temp >= 85) tempClass = 'temp-critical';
                            else if (temp >= 75) tempClass = 'temp-warning';
                            else tempClass = 'temp-normal';
                        }
                        
                        const statusClass = sensor.status === 'ok' ? 'status-ok' : 'alert';
                        
                        tbody += `
                            <tr>
                                <td>${sensor.name}</td>
                                <td class="${tempClass}">${sensor.value}</td>
                                <td>${sensor.unit}</td>
                                <td class="${statusClass}">${sensor.status}</td>
                                <td>${sensor.lower_nr}</td>
                                <td>${sensor.lower_c}</td>
                                <td>${sensor.lower_nc}</td>
                                <td>${sensor.upper_nc}</td>
                                <td>${sensor.upper_c}</td>
                                <td>${sensor.upper_nr}</td>
                            </tr>
                        `;
                    }
                    
                    document.querySelector('#sensor-table tbody').innerHTML = tbody;
                    document.getElementById('last-update').textContent = 'Last update: ' + new Date().toLocaleString();
                })
                .catch(err => console.error('Error fetching status:', err));
        }
        
        function updateConfig(formData) {
            fetch('/update_config', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: new URLSearchParams(formData)
            })
            .then(r => r.json())
            .then(data => {
                alert(data.message);
                // Reload config after successful update
                loadConfig();
            })
            .catch(err => alert('Error: ' + err));
        }
        
        // Setup fan speed sliders
        const fanSpeedInputs = [
            {range: 'speed-idle-range', percent: 'speed-idle-percent', hex: 'speed-idle-hex', name: 'idle'},
            {range: 'speed-moderate-range', percent: 'speed-moderate-percent', hex: 'speed-moderate-hex', name: 'moderate'},
            {range: 'speed-high-range', percent: 'speed-high-percent', hex: 'speed-high-hex', name: 'high'},
            {range: 'speed-emergency-range', percent: 'speed-emergency-percent', hex: 'speed-emergency-hex', name: 'emergency'},
            {range: 'speed-safety-range', percent: 'speed-safety-percent', hex: 'speed-safety-hex', name: 'safety_floor_speed'},
            {range: 'speed-static-peripheral-range', percent: 'speed-static-peripheral-percent', hex: 'speed-static-peripheral-hex', name: 'static_peripheral'}
        ];
        
        fanSpeedInputs.forEach(input => {
            const rangeElem = document.getElementById(input.range);
            const percentElem = document.getElementById(input.percent);
            const hexElem = document.getElementById(input.hex);
            
            // Sync range slider with number input
            rangeElem.addEventListener('input', (e) => {
                const percent = parseInt(e.target.value);
                percentElem.value = percent;
                hexElem.textContent = percentToHex(percent);
            });
            
            // Sync number input with range slider
            percentElem.addEventListener('input', (e) => {
                const percent = parseInt(e.target.value) || 0;
                rangeElem.value = percent;
                hexElem.textContent = percentToHex(percent);
            });
        });
        
        // Handle static peripheral enable/disable
        const staticPeripheralCheckbox = document.getElementById('static-peripheral-enabled');
        const staticPeripheralControl = document.getElementById('static-peripheral-control');
        
        function updateStaticPeripheralState() {
            const enabled = staticPeripheralCheckbox.checked;
            const rangeElem = document.getElementById('speed-static-peripheral-range');
            const percentElem = document.getElementById('speed-static-peripheral-percent');
            
            if (enabled) {
                staticPeripheralControl.style.opacity = '1';
                rangeElem.disabled = false;
                percentElem.disabled = false;
            } else {
                staticPeripheralControl.style.opacity = '0.5';
                rangeElem.disabled = true;
                percentElem.disabled = true;
            }
        }
        
        staticPeripheralCheckbox.addEventListener('change', updateStaticPeripheralState);
        
        document.getElementById('thresholds-form').addEventListener('submit', (e) => {
            e.preventDefault();
            const formData = new FormData(e.target);
            formData.append('section', 'thresholds');
            updateConfig(formData);
        });
        
        document.getElementById('speeds-form').addEventListener('submit', (e) => {
            e.preventDefault();
            const formData = new FormData();
            formData.append('section', 'fan_speeds');
            
            fanSpeedInputs.forEach(input => {
                const percent = document.getElementById(input.percent).value;
                const hex = percentToHex(percent);
                formData.append(input.name, hex);
            });
            
            // Add static peripheral settings
            formData.append('static_peripheral_enabled', document.getElementById('static-peripheral-enabled').checked ? 'true' : 'false');
            
            updateConfig(formData);
        });
        
        document.getElementById('polling-form').addEventListener('submit', (e) => {
            e.preventDefault();
            const formData = new FormData(e.target);
            formData.append('section', 'polling');
            updateConfig(formData);
        });
        
        function restartService() {
            if (confirm('Restart the fan controller service?')) {
                fetch('/restart_service', {method: 'POST'})
                    .then(r => r.json())
                    .then(data => alert(data.message))
                    .catch(err => alert('Error: ' + err));
            }
        }
        
        // Load config once on page load
        loadConfig();
        
        // Update status every 5 seconds (doesn't touch config forms)
        updateStatus();
        setInterval(updateStatus, 5000);
    </script>
</body>
</html>"""
                
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(html.encode('utf-8'))
            
            def serve_status_json(self):
                """Serve current status as JSON"""
                import json
                
                # Get all sensor data
                all_sensors = controller.get_all_sensor_data()
                
                status = {
                    'all_sensors': all_sensors
                }
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(status).encode())
            
            def serve_config_json(self):
                """Serve configuration values as JSON"""
                import json
                
                config_data = {
                    'thresholds': controller.config['thresholds'],
                    'fan_speeds': controller.config['fan_speeds'],
                    'polling': controller.config['polling'],
                    'static_peripheral': controller.config.get('static_peripheral', {'enabled': False, 'speed': '0x04'})
                }
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(config_data).encode())
            
            def handle_config_update(self):
                """Handle configuration update"""
                import json
                
                try:
                    content_length = int(self.headers['Content-Length'])
                    post_data = self.rfile.read(content_length).decode('utf-8')
                    params = parse_qs(post_data)
                    
                    section = params.get('section', [''])[0]
                    
                    if section == 'thresholds':
                        controller.config['thresholds']['moderate'] = int(params['moderate'][0])
                        controller.config['thresholds']['high'] = int(params['high'][0])
                        controller.config['thresholds']['emergency'] = int(params['emergency'][0])
                        controller.config['thresholds']['safety_floor'] = int(params['safety_floor'][0])
                    elif section == 'fan_speeds':
                        controller.config['fan_speeds']['idle'] = params['idle'][0]
                        controller.config['fan_speeds']['moderate'] = params['moderate'][0]
                        controller.config['fan_speeds']['high'] = params['high'][0]
                        controller.config['fan_speeds']['emergency'] = params['emergency'][0]
                        controller.config['fan_speeds']['safety_floor_speed'] = params['safety_floor_speed'][0]
                        
                        # Handle static peripheral settings
                        if 'static_peripheral' not in controller.config:
                            controller.config['static_peripheral'] = {}
                        controller.config['static_peripheral']['speed'] = params['static_peripheral'][0]
                        controller.config['static_peripheral']['enabled'] = params.get('static_peripheral_enabled', ['false'])[0] == 'true'
                    elif section == 'polling':
                        controller.config['polling']['normal'] = int(params['normal'][0])
                        controller.config['polling']['high_load'] = int(params['high_load'][0])

                    # Save to config file
                    config_path = Path('/opt/fan-controller/config.yaml')
                    with open(config_path, 'w') as f:
                        yaml.dump(controller.config, f, default_flow_style=False)
                    
                    response = {'status': 'success', 'message': 'Configuration updated successfully. Changes will take effect on next poll cycle.'}
                    
                except Exception as e:
                    response = {'status': 'error', 'message': str(e)}
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())
            
            def handle_restart(self):
                """Handle service restart request"""
                import json
                import subprocess
                
                try:
                    # Try OpenRC first, then systemd
                    try:
                        subprocess.run(['rc-service', 'fan-controller', 'restart'], check=True)
                    except (FileNotFoundError, subprocess.CalledProcessError):
                        subprocess.run(['systemctl', 'restart', 'fan-controller'], check=True)
                    response = {'status': 'success', 'message': 'Service restart initiated'}
                except Exception as e:
                    response = {'status': 'error', 'message': str(e)}
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())
        
        return RequestHandler
    
    def start(self):
        """Start the web server in a background thread"""
        if not self.config['web_interface']['enabled']:
            return
        
        bind_addr = self.config['web_interface']['bind_address']
        port = self.config['web_interface']['port']
        
        handler = self.create_handler()
        self.server = HTTPServer((bind_addr, port), handler)
        
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        
        print(f"Web interface started on http://{bind_addr}:{port}")
        if self.controller.logging_enabled:
            self.controller.log_info(f"Web interface started on http://{bind_addr}:{port}")
    
    def stop(self):
        """Stop the web server"""
        if self.server:
            self.server.shutdown()
            print("Web interface stopped")

class FanController:
    def __init__(self, config_path="config.yaml"):
        """Initialize fan controller with configuration"""
        self.load_config(config_path)
        
        # Runtime state
        self.current_speeds = {'cpu': None, 'peripheral': None}
        self.poll_interval = self.config['polling']['normal']
        self.high_load_start = None
        self.high_load_alerted = False
        self.emergency_active = False
        self.safety_override_active = False
        self.temp_log = deque(maxlen=self.config['logging']['temp_log_size'])
        self.high_load_events = deque(maxlen=100)
        
        # Initialize syslog if enabled
        self.logging_enabled = self.config['logging']['enabled'] and SYSLOG_AVAILABLE
        if self.logging_enabled:
            facility = SYSLOG_FACILITIES.get(
                self.config['logging']['facility'], 
                syslog.LOG_USER
            )
            syslog.openlog("fan-controller", syslog.LOG_PID, facility)
            self.log_info("Fan controller initializing")
        
        print(f"Fan controller initialized with config: {config_path}")

        # Initialize web interface
        self.web_interface = None
        if self.config.get('web_interface', {}).get('enabled', False):
            self.web_interface = WebInterface(self, self.config)

    def load_config(self, config_path):
        """Load configuration from YAML file"""
        try:
            with open(config_path, 'r') as f:
                self.config = yaml.safe_load(f)
            print(f"Configuration loaded from {config_path}")
        except FileNotFoundError:
            print(f"ERROR: Configuration file not found: {config_path}", file=sys.stderr)
            print("Please copy config.yaml.example to config.yaml and customize it")
            sys.exit(1)
        except yaml.YAMLError as e:
            print(f"ERROR: Invalid YAML in configuration file: {e}", file=sys.stderr)
            sys.exit(1)

    def log_info(self, message):
        """Log info message to syslog if enabled"""
        if self.logging_enabled:
            syslog.syslog(syslog.LOG_INFO, message)

    def log_warning(self, message):
        """Log warning message to syslog if enabled"""
        if self.logging_enabled:
            syslog.syslog(syslog.LOG_WARNING, message)

    def log_error(self, message):
        """Log error message to syslog if enabled"""
        if self.logging_enabled:
            syslog.syslog(syslog.LOG_ERR, message)

    def log_alert(self, message):
        """Log alert message to syslog if enabled"""
        if self.logging_enabled:
            syslog.syslog(syslog.LOG_ALERT, message)

    def get_sensor_temp(self, sensor_name):
        """Get temperature from IPMI sensor"""
        try:
            result = subprocess.run(
                ["ipmitool", "-I", "lanplus", 
                 "-H", self.config['ipmi']['host'],
                 "-U", self.config['ipmi']['username'],
                 "-P", self.config['ipmi']['password'],
                 "sensor", "get", sensor_name],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.split('\n'):
                if 'Sensor Reading' in line:
                    temp_str = line.split(':')[1].strip().split()[0]
                    try:
                        return int(float(temp_str))
                    except ValueError:
                        return None
        except Exception as e:
            print(f"Error reading {sensor_name}: {e}", file=sys.stderr)
            self.log_error(f"Error reading {sensor_name}: {e}")
            return None
        return None

    def get_all_sensor_data(self):
        """Get all IPMI sensor readings"""
        try:
            result = subprocess.run(
                ["ipmitool", "-I", "lanplus",
                 "-H", self.config['ipmi']['host'],
                 "-U", self.config['ipmi']['username'],
                 "-P", self.config['ipmi']['password'],
                 "sensor"],
                capture_output=True, text=True, timeout=15
            )
            
            sensors = []
            for line in result.stdout.split('\n'):
                if '|' in line:
                    parts = [p.strip() for p in line.split('|')]
                    if len(parts) >= 10:
                        sensors.append({
                            'name': parts[0],
                            'value': parts[1],
                            'unit': parts[2],
                            'status': parts[3],
                            'lower_nr': parts[4],
                            'lower_c': parts[5],
                            'lower_nc': parts[6],
                            'upper_nc': parts[7],
                            'upper_c': parts[8],
                            'upper_nr': parts[9]
                        })
            
            return sensors
        except Exception as e:
            self.log_error(f"Error reading all sensors: {e}")
            return []

    def get_zone_max_temp(self, zone_name):
        """Get the maximum temperature for a specific zone"""
        sensors = self.config['sensors'].get(zone_name, [])
        if not sensors:
            return None
        
        temps = {}
        for sensor in sensors:
            temp = self.get_sensor_temp(sensor)
            if temp is not None:
                temps[sensor] = temp
        
        if not temps:
            return None
        
        return max(temps.values())

    def get_all_temps(self):
        """Get all temperature readings from all zones"""
        all_temps = {}
        
        for zone_name, sensors in self.config['sensors'].items():
            for sensor in sensors:
                temp = self.get_sensor_temp(sensor)
                if temp is not None:
                    all_temps[sensor] = temp
        
        return all_temps

    def check_safety_floor(self, all_temps):
        """Check if any sensor exceeds safety floor threshold"""
        if not all_temps:
            return False
        
        max_temp = max(all_temps.values())
        threshold = self.config['thresholds']['safety_floor']
        
        if max_temp >= threshold:
            if not self.safety_override_active:
                max_sensor = max(all_temps, key=all_temps.get)
                msg = f"Safety floor enforced: {max_sensor} at {max_temp}C (threshold: {threshold}C) - all fans minimum {self.config['fan_speeds']['safety_floor_speed']}"
                print(f"ALERT: {msg}")
                self.log_alert(msg)
                self.safety_override_active = True
            return True
        else:
            if self.safety_override_active:
                self.log_info("Safety floor no longer required")
                self.safety_override_active = False
            return False

    def set_fan_speed(self, zone_name, speed_hex):
        """Set fan speed via IPMI raw command"""
        zone = self.config['fan_zones'][zone_name]
        try:
            subprocess.run(
                ["ipmitool", "-I", "lanplus",
                 "-H", self.config['ipmi']['host'],
                 "-U", self.config['ipmi']['username'],
                 "-P", self.config['ipmi']['password'],
                 "raw", "0x30", "0x70", "0x66", "0x01", zone, speed_hex],
                capture_output=True, timeout=10, check=True
            )
            return True
        except Exception as e:
            print(f"Error setting fan speed for {zone_name}: {e}", file=sys.stderr)
            self.log_error(f"Error setting fan speed for {zone_name}: {e}")
            return False

    def determine_fan_speed(self, temp):
        """Return fan speed and load state based on temperature"""
        if temp is None:
            return self.config['fan_speeds']['error_safe'], "error"
        
        thresholds = self.config['thresholds']
        speeds = self.config['fan_speeds']
        
        if temp >= thresholds['emergency']:
            return speeds['emergency'], "emergency"
        elif temp >= thresholds['high']:
            return speeds['high'], "high"
        elif temp >= thresholds['moderate']:
            return speeds['moderate'], "moderate"
        else:
            return speeds['idle'], "idle"

    def apply_speed_floor(self, speed_hex, floor_hex):
        """Ensure fan speed is at least the floor value, return higher of the two"""
        speed = int(speed_hex, 16)
        floor = int(floor_hex, 16)
        return f"0x{max(speed, floor):02x}"

    def log_temp(self, all_temps, fan_speeds, load_states):
        """Add entry to rolling temperature log"""
        timestamp = datetime.now()
        self.temp_log.append({
            'timestamp': timestamp,
            'temps': all_temps.copy(),
            'fan_speeds': fan_speeds.copy(),
            'load_states': load_states.copy()
        })

    def check_alerts(self, load_states):
        """Check if alerts should be triggered"""
        current_time = time.time()
        now = datetime.now()
        
        # Determine overall worst load state
        state_priority = {'idle': 0, 'moderate': 1, 'high': 2, 'emergency': 3, 'error': 2}
        worst_state = max(load_states.values(), key=lambda s: state_priority.get(s, 0))
        
        # Emergency state alert
        if worst_state == "emergency":
            if not self.emergency_active:
                msg = f"EMERGENCY: Temperature >= {self.config['thresholds']['emergency']}C - fans at 100%"
                print(f"ALERT: {msg}")
                self.log_alert(msg)
                self.emergency_active = True
        else:
            self.emergency_active = False
        
        # Sustained high load alert
        if worst_state in ["high", "emergency"]:
            if self.high_load_start is None:
                self.high_load_start = current_time
                self.high_load_events.append(now)
            else:
                duration = current_time - self.high_load_start
                if duration >= self.config['alerts']['sustained_high_load'] and not self.high_load_alerted:
                    msg = f"Sustained high load for {int(duration)}s (temp sustained >= {self.config['thresholds']['high']}C)"
                    print(f"ALERT: {msg}")
                    self.log_warning(msg)
                    self.high_load_alerted = True
        else:
            self.high_load_start = None
            self.high_load_alerted = False
        
        # Multiple high load events in time window
        cutoff_time = now - timedelta(seconds=self.config['alerts']['high_load_event_window'])
        while self.high_load_events and self.high_load_events[0] < cutoff_time:
            self.high_load_events.popleft()
        
        if len(self.high_load_events) >= self.config['alerts']['high_load_event_threshold']:
            msg = (f"Multiple high load events detected: {len(self.high_load_events)} events "
                   f"in last {self.config['alerts']['high_load_event_window']}s")
            print(f"ALERT: {msg}")
            self.log_warning(msg)
            self.high_load_events.clear()

    def adjust_polling(self, load_states):
        """Adjust polling interval based on load state"""
        state_priority = {'idle': 0, 'moderate': 1, 'high': 2, 'emergency': 3, 'error': 2}
        worst_state = max(load_states.values(), key=lambda s: state_priority.get(s, 0))
        
        if worst_state in ["high", "emergency"]:
            self.poll_interval = self.config['polling']['high_load']
        else:
            self.poll_interval = self.config['polling']['normal']

    def print_status(self, all_temps, fan_speeds, load_states):
        """Print current status summary"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        temp_str = ", ".join([f"{s}: {t}C" for s, t in all_temps.items()])
        fan_str = ", ".join([f"{z}: {s}" for z, s in fan_speeds.items()])
        state_str = ", ".join([f"{z}: {s}" for z, s in load_states.items()])
        
        print(f"[{timestamp}] Temps: {temp_str} | Fans: {fan_str} | States: {state_str} | Poll: {self.poll_interval}s")

    def notify_watchdog(self):
        """Notify systemd watchdog that we're alive"""
        if SYSTEMD_WATCHDOG:
            systemd.daemon.notify('WATCHDOG=1')

    def run(self):
        """Main control loop"""
        print("Fan controller starting...")
        self.log_info("Fan controller started")
        
        # Start web interface if enabled
        if self.web_interface:
            self.web_interface.start()
        
        if SYSTEMD_WATCHDOG:
            print("Systemd watchdog enabled")
        
        if not self.logging_enabled:
            if not SYSLOG_AVAILABLE:
                print("Warning: syslog module not available")
            else:
                print("Logging disabled in configuration")
        
        while True:
            try:
                # Get all temperature readings
                all_temps = self.get_all_temps()
                
                if not all_temps:
                    print("Warning: No valid temperature readings", file=sys.stderr)
                    self.log_warning("No valid temperature readings")
                    time.sleep(self.poll_interval)
                    continue
                
                # Check for safety floor
                safety_floor_active = self.check_safety_floor(all_temps)
                
                # Determine fan speeds for each zone
                new_speeds = {}
                load_states = {}
                
                # CPU zone
                cpu_temp = self.get_zone_max_temp('cpu_zone')
                if cpu_temp is not None:
                    new_speeds['cpu'], load_states['cpu'] = self.determine_fan_speed(cpu_temp)
                else:
                    new_speeds['cpu'] = self.config['fan_speeds']['error_safe']
                    load_states['cpu'] = 'error'
                
                # Peripheral zone
                periph_temp = self.get_zone_max_temp('peripheral_zone')
                
                # Check if static peripheral speed is enabled
                static_peripheral_config = self.config.get('static_peripheral', {})
                if static_peripheral_config.get('enabled', False):
                    # Use static speed
                    new_speeds['peripheral'] = static_peripheral_config.get('speed', self.config['fan_speeds']['idle'])
                    load_states['peripheral'] = 'static'
                elif periph_temp is not None:
                    # Use temperature-based control
                    new_speeds['peripheral'], load_states['peripheral'] = self.determine_fan_speed(periph_temp)
                else:
                    # If no peripheral sensors, use idle speed
                    new_speeds['peripheral'] = self.config['fan_speeds']['idle']
                    load_states['peripheral'] = 'idle'
                
                # Apply safety floor if triggered (ensures no fan runs below floor speed)
                if safety_floor_active:
                    floor_speed = self.config['fan_speeds']['safety_floor_speed']
                    for zone in new_speeds:
                        original_speed = new_speeds[zone]
                        new_speeds[zone] = self.apply_speed_floor(new_speeds[zone], floor_speed)
                        if new_speeds[zone] != original_speed:
                            print(f"Safety floor applied to {zone}: {original_speed} -> {new_speeds[zone]}")
                
                # Update fan speeds if changed
                for zone in ['cpu', 'peripheral']:
                    if new_speeds[zone] != self.current_speeds[zone]:
                        print(f"Setting {zone} fans to {new_speeds[zone]} ({load_states[zone]})")
                        if self.set_fan_speed(zone, new_speeds[zone]):
                            self.current_speeds[zone] = new_speeds[zone]
                            self.log_info(f"{zone} fan speed changed to {new_speeds[zone]} (state: {load_states[zone]})")
                
                # Logging and monitoring
                self.log_temp(all_temps, self.current_speeds, load_states)
                self.check_alerts(load_states)
                self.adjust_polling(load_states)
                self.print_status(all_temps, self.current_speeds, load_states)
                
                # Notify watchdog
                self.notify_watchdog()
                
                time.sleep(self.poll_interval)
                
            except Exception as e:
                print(f"Error in main loop: {e}", file=sys.stderr)
                self.log_error(f"Error in main loop: {e}")
                time.sleep(self.config['polling']['normal'])

def main():
    # Look for config in same directory as script
    script_dir = Path(__file__).parent
    config_path = script_dir / "config.yaml"
    
    # Allow config path override via command line
    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
    
    controller = FanController(config_path)
    
    try:
        controller.run()
    except KeyboardInterrupt:
        print("\nFan controller stopped")
        if controller.logging_enabled:
            controller.log_info("Fan controller stopped by user")
        if controller.web_interface:
            controller.web_interface.stop()
        sys.exit(0)

if __name__ == "__main__":
    main()
