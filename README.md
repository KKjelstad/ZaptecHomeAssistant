# Zaptec Home Assistant Bridge

A Python-based bridge service that connects Zaptec EV chargers to Home Assistant via MQTT.

## Features

- 🔌 Real-time monitoring of Zaptec charger status
- 📊 Charge session tracking and history
- 🏠 Automatic Home Assistant MQTT discovery
- 🔄 Dynamic polling intervals (fast when charging, slow when idle)
- 📈 Energy consumption tracking
- ⚙️ Remote control capabilities (start/stop charging, adjust current)
- 🔐 OAuth2 token caching for efficient authentication
- ⚡ Rate limit handling with exponential backoff
- 🎯 Smart polling: 10s updates during charging, 120s when idle

## Requirements

- Python 3.8+
- Home Assistant with MQTT broker configured
- Zaptec account with API access

## Important Notes

⚠️ **API Usage**: This bridge uses polling to fetch charger state. According to [Zaptec API Usage Guidelines](https://docs.zaptec.com/docs/api-usage-guidelines), polling should be minimized. The default polling interval is 60 seconds to respect API limits.

💡 **Recommendation**: For real-time updates, Zaptec recommends using their [Service Bus message subscriptions](https://docs.zaptec.com/docs/zaptec-service-bus-subscriptions) instead of polling. Future versions of this bridge may implement Service Bus support for more efficient real-time updates.

## Installation

1. Clone this repository:
```bash
cd /home/kristian/workspace/ZaptecHomeAssistant
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Configure the service:
```bash
cp config.example.yaml config.yaml
# Edit config.yaml with your credentials
```

4. Run the service:
```bash
python zaptec_bridge.py
```

## Configuration

Edit `config.yaml` with your settings:

```yaml
zaptec:
  username: "your-email@example.com"
  # password is optional - see below for options
  
mqtt:
  host: "homeassistant.local"
  port: 1883
  username: "mqtt-user"
  password: "mqtt-password"
  
bridge:
  poll_interval: 30  # seconds
  discovery_prefix: "homeassistant"
```

### Password Configuration (3 options)

For security, you have multiple options for providing your Zaptec password:

**Option 1: Environment Variable (Recommended)**
```bash
export ZAPTEC_PASSWORD="your-password"
python zaptec_bridge.py
```

**Option 2: Interactive Prompt (Best for First Run)**
```bash
python zaptec_bridge.py
# You'll be prompted to enter your password on first run
# The OAuth2 token will be cached, so you won't need it again until it expires
```

**Option 3: Config File (Less Secure)**
```yaml
zaptec:
  username: "your-email@example.com"
  password: "your-password"  # Not recommended for production
```

> **Note:** The bridge uses OAuth2 and caches the access token in `.zaptec_token_cache.json`. After the first authentication, you won't be prompted for your password again unless the token expires (typically 24+ hours).

## Running as a Service

### Systemd (Linux)

The shipped unit runs as a dedicated `zaptec` user from `/opt/zaptec-bridge`,
with the Zaptec password in a `0600` EnvironmentFile (not in the unit / `ps`).
MQTT credentials stay in `config.yaml` (keep it `0600`).

1. Install the code + venv into `/opt/zaptec-bridge` and create the `zaptec` user.
2. Put `config.yaml` (MQTT creds) in `/opt/zaptec-bridge/` (chmod 600, owned by `zaptec`).
3. Create the env file with your Zaptec password:
```bash
sudo cp zaptec-bridge.env.example /etc/zaptec-bridge.env
sudo nano /etc/zaptec-bridge.env   # set ZAPTEC_PASSWORD
sudo chmod 600 /etc/zaptec-bridge.env
```
4. Install, enable and start:
```bash
sudo cp zaptec-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now zaptec-bridge
sudo systemctl status zaptec-bridge
```

### Docker

1. Create a `.env` file:
```bash
cp .env.example .env
nano .env  # Set your ZAPTEC_PASSWORD
```

2. Run with docker-compose:
```bash
docker-compose up -d
```

Or run directly with environment variable:
```bash
export ZAPTEC_PASSWORD="your-password"
docker-compose up -d
```

## Home Assistant Integration

The bridge automatically creates entities in Home Assistant:

- **Sensors**: Power, Energy, State, Voltage, Current
- **Binary Sensors**: Connected, Charging
- **Switches**: Start/Stop charging
- **Number**: Charge current limit

All entities appear with the prefix `zaptec_` in Home Assistant.

## License

MIT
