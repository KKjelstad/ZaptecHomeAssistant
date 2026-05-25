#!/usr/bin/env python3
"""
Zaptec Home Assistant Bridge
Main application that connects Zaptec chargers to Home Assistant via MQTT.
"""

import logging
import signal
import sys
import time
import yaml
import os
import getpass
from pathlib import Path
from typing import Dict, Optional

from zaptec_api import ZaptecAPI
from mqtt_client import MQTTClient
from ha_discovery import HomeAssistantDiscovery


class ZaptecBridge:
    """Main bridge application."""
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        Initialize the bridge.
        
        Args:
            config_path: Path to configuration file
        """
        self.config = self._load_config(config_path)
        self.running = False
        
        # Setup logging
        log_level = self.config["bridge"].get("log_level", "INFO")
        logging.basicConfig(
            level=getattr(logging, log_level),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Initialize Zaptec API client first (it will try to load cached token)
        self.zaptec_api = ZaptecAPI(
            username=self.config["zaptec"]["username"],
            password="",  # may be filled from env/config/interactive below
            base_url=self.config["zaptec"].get("api_base_url", "https://api.zaptec.com")
        )

        # Attempt to populate password from environment/config/interactive prompt so
        # re-authentication can succeed later when cached token expires.
        zaptec_password = self._get_zaptec_password()
        if zaptec_password:
            self.zaptec_api.password = zaptec_password

        # If there is no valid cached token and we still don't have a password, fail early.
        if not self.zaptec_api.access_token and not self.zaptec_api.password:
            self.logger.error("No valid cached token and no Zaptec password available for authentication")
            raise ValueError("Zaptec password not provided; set ZAPTEC_PASSWORD or add 'zaptec.password' to config.yaml")
        elif self.zaptec_api.access_token:
            self.logger.info("Using cached authentication token")
        
        # Initialize MQTT components
        
        self.mqtt_client = MQTTClient(
            host=self.config["mqtt"]["host"],
            port=self.config["mqtt"]["port"],
            username=self.config["mqtt"]["username"],
            password=self.config["mqtt"]["password"],
            client_id=self.config["mqtt"].get("client_id", "zaptec-bridge")
        )
        
        self.ha_discovery = HomeAssistantDiscovery(
            mqtt_client=self.mqtt_client,
            discovery_prefix=self.config["bridge"].get("discovery_prefix", "homeassistant"),
            state_topic_prefix=self.config["bridge"].get("state_topic_prefix", "zaptec")
        )
        
        self.chargers: Dict[str, Dict] = {}
        # Support both old single poll_interval and new dual intervals
        self.poll_interval_charging = self.config["bridge"].get("poll_interval_charging", 10)
        self.poll_interval_idle = self.config["bridge"].get("poll_interval_idle", 120)
        # Fallback to old single poll_interval if new ones not set
        if "poll_interval" in self.config["bridge"]:
            old_interval = self.config["bridge"]["poll_interval"]
            self.poll_interval_charging = old_interval
            self.poll_interval_idle = old_interval
        
        self.current_poll_interval = self.poll_interval_idle  # Start with idle interval
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from YAML file."""
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)
    
    def _get_zaptec_password(self) -> Optional[str]:
        """
        Get Zaptec password from various sources in order of precedence:
        1. Environment variable (ZAPTEC_PASSWORD)
        2. Config file (if provided)
        3. Interactive prompt (if running in a terminal)
        
        Returns:
            Password string or None
        """
        # Try environment variable first
        password = os.environ.get("ZAPTEC_PASSWORD")
        if password:
            self.logger.debug("Using Zaptec password from environment variable")
            return password
        
        # Try config file
        password = self.config.get("zaptec", {}).get("password")
        if password:
            self.logger.debug("Using Zaptec password from config file")
            return password
        
        # Try interactive prompt if running in a terminal
        if sys.stdin.isatty():
            self.logger.info("Zaptec password not found in environment or config")
            try:
                password = getpass.getpass("Enter Zaptec password: ")
                return password
            except (KeyboardInterrupt, EOFError):
                self.logger.error("Password input cancelled")
                return None
        
        self.logger.error("No Zaptec password provided and not running in interactive mode")
        return None
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        self.logger.info("Received shutdown signal, stopping...")
        self.stop()
    
    def _handle_command(self, topic: str, payload: str):
        """
        Handle commands from Home Assistant.
        
        Args:
            topic: MQTT topic
            payload: Command payload
        """
        self.logger.debug(f"Handling command: {topic} = {payload}")
        
        # Extract charger serial from topic: zaptec/zaptec_SERIAL/set
        parts = topic.split('/')
        if len(parts) < 3:
            return
        
        node_id = parts[1]
        command_type = parts[2]
        
        # Find charger by node_id (need to sanitize serial for comparison)
        charger = None
        for c in self.chargers.values():
            serial = c.get("MID") or c.get("DeviceId") or c.get("SerialNo", c.get("Id", ""))
            safe_serial = self.ha_discovery._sanitize_topic_part(serial)
            if f"zaptec_{safe_serial}" == node_id:
                charger = c
                break
        
        if not charger:
            self.logger.warning(f"Charger not found for node_id: {node_id}")
            return
        
        charger_id = charger.get("Id")
        
        # Handle different command types
        if command_type == "set":
            # Charging on/off
            if payload == "ON":
                self.zaptec_api.start_charging(charger_id)
            elif payload == "OFF":
                self.zaptec_api.stop_charging(charger_id)
        
        elif command_type == "set_current":
            # Set charge current limit
            try:
                current = float(payload)
                self.zaptec_api.set_charge_current(charger_id, current)
            except ValueError:
                self.logger.error(f"Invalid current value: {payload}")
    
    def _discover_chargers(self):
        """Discover all accessible chargers."""
        self.logger.info("Discovering Zaptec chargers...")
        
        chargers = self.zaptec_api.get_chargers()
        if not chargers:
            self.logger.warning("No chargers found")
            return
        
        # Log the response structure for debugging
        self.logger.debug(f"Chargers response type: {type(chargers)}")
        if chargers and len(chargers) > 0:
            self.logger.debug(f"First charger type: {type(chargers[0])}")
            self.logger.debug(f"First charger data: {chargers[0]}")
        
        # Handle if chargers is not a list
        if not isinstance(chargers, list):
            self.logger.error(f"Unexpected chargers data type: {type(chargers)}")
            self.logger.debug(f"Chargers data: {chargers}")
            return
        
        # Filter by specific charger IDs if configured
        filter_ids = self.config.get("chargers", [])
        if filter_ids:
            chargers = [c for c in chargers if isinstance(c, dict) and c.get("Id") in filter_ids]
        
        for charger in chargers:
            # Handle both dict and string types
            if isinstance(charger, str):
                self.logger.warning(f"Charger is a string (ID?): {charger}")
                # Try to fetch full charger details
                charger_details = self.zaptec_api.get_charger_state(charger)
                if not charger_details:
                    self.logger.error(f"Failed to get details for charger {charger}")
                    continue
                charger_id = charger
                charger_name = charger
                # Create a minimal charger dict
                charger = {"Id": charger_id, "Name": charger_name, "SerialNo": charger_id}
            elif not isinstance(charger, dict):
                self.logger.error(f"Unexpected charger type: {type(charger)}")
                continue
            else:
                charger_id = charger.get("Id")
                charger_name = charger.get("Name", charger_id)
            
            self.chargers[charger_id] = charger
            self.logger.info(f"Found charger: '{charger_name}' (ID: {charger_id})")
            self.logger.debug(f"Charger data keys: {list(charger.keys())}")
            self.logger.debug(f"Full charger data: {charger}")
            
            # Create Home Assistant entities
            self.ha_discovery.create_charger_entities(charger)
            
            # Subscribe to command topics (use MID/DeviceId for topic, not name)
            serial = charger.get("MID") or charger.get("DeviceId") or charger.get("SerialNo", charger_id)
            safe_serial = self.ha_discovery._sanitize_topic_part(serial)
            node_id = f"zaptec_{safe_serial}"
            state_prefix = self.config["bridge"].get("state_topic_prefix", "zaptec")
            
            self.mqtt_client.subscribe(
                f"{state_prefix}/{node_id}/set",
                self._handle_command
            )
            self.mqtt_client.subscribe(
                f"{state_prefix}/{node_id}/set_current",
                self._handle_command
            )
    
    def _parse_charger_state(self, state_data) -> Dict:
        """
        Parse charger state data into a format suitable for Home Assistant.
        
        Args:
            state_data: Raw state data from API (can be dict or list)
            
        Returns:
            Parsed state dictionary
        """
        # The state data is a list of state variables with StateId and Value
        # We need to map these IDs to meaningful names
        state_map = {}
        
        if isinstance(state_data, list):
            # Direct list of state items
            for item in state_data:
                if isinstance(item, dict):
                    state_id = item.get("StateId")
                    value = item.get("ValueAsString") or item.get("Value")
                    if state_id is not None:
                        state_map[state_id] = value
        elif isinstance(state_data, dict) and "StateData" in state_data:
            # Wrapped in StateData
            for item in state_data.get("StateData", []):
                state_id = item.get("StateId")
                value = item.get("ValueAsString") or item.get("Value")
                if state_id is not None:
                    state_map[state_id] = value
        
        # Log the state IDs we received for debugging (only in DEBUG mode)
        if self.logger.level <= logging.DEBUG:
            self.logger.debug(f"Total state items received: {len(state_map)}")
            # Only show key values in normal operation
            self.logger.debug(f"Key states - Power: {state_map.get(513)}W, Current: {state_map.get(553)}A, Voltage: {state_map.get(501)}V")
        
        # Helper functions for safe type conversion
        def safe_float(value, default=0.0):
            """Safely convert value to float."""
            try:
                return float(value) if value is not None else default
            except (ValueError, TypeError):
                return default
        
        def safe_int(value, default=0):
            """Safely convert value to int."""
            try:
                return int(float(value)) if value is not None else default
            except (ValueError, TypeError):
                return default
        
        # Map state IDs to meaningful values based on official Zaptec API documentation
        # Reference: https://docs.zaptec.com/docs/state-observation-reference
        # 
        # Key State IDs:
        # -2  = IsOnline (1=online, 0=offline)
        # 150 = Network communication mode (Wifi, LTE, PLC, none)
        # 270 = Humidity (internal humidity in %)
        # 501 = VoltagePhase1 (output voltage phase 1 in volts)
        # 502 = VoltagePhase2 (output voltage phase 2 in volts)
        # 503 = VoltagePhase3 (output voltage phase 3 in volts)
        # 507 = CurrentPhase1 (output current phase 1 in amperes)
        # 508 = CurrentPhase2 (output current phase 2 in amperes)
        # 509 = CurrentPhase3 (output current phase 3 in amperes)
        # 513 = TotalChargePower (total instant charge power in watts)
        # 553 = TotalChargePowerSession (total energy delivered in current session in kWh)
        # 708 = ChargeCurrentSet (allocated charge current in amperes)
        # 710 = ChargerOperationMode (1=disconnected, 2=connected requesting, 3=charging, 5=finished)
        # 711 = IsEnabled (1=enabled, 0=disabled)
        # 714 = CableType (ampere rating of connected cable)
        # 715 = NetworkType (1=IT 1ph, 2=IT 3ph, 3=TN 1ph, 4=TN 3ph)
        # 721 = SessionIdentifier (current session ID UUID)
        # 809 = CommunicationSignalStrength (signal strength)
        # 911 = SmartComputerSoftwareApplicationVersion (firmware version)
        
        total_power_w = safe_float(state_map.get(513))  # TotalChargePower in Watts
        session_energy_kwh = safe_float(state_map.get(553))  # TotalChargePowerSession in kWh
        
        # Calculate total current from phases
        current_p1 = safe_float(state_map.get(507))
        current_p2 = safe_float(state_map.get(508))
        current_p3 = safe_float(state_map.get(509))
        total_current = current_p1 + current_p2 + current_p3
        
        # Get voltage (use phase 1 as representative)
        voltage = safe_float(state_map.get(501))
        
        # Other measurements
        humidity = safe_float(state_map.get(270))
        charge_current_set = safe_float(state_map.get(708), 16)
        operating_mode = safe_int(state_map.get(710))
        is_enabled = safe_int(state_map.get(711)) == 1
        is_online = safe_int(state_map.get(-2)) == 1
        
        parsed = {
            "operating_mode": self._get_operating_mode_text(operating_mode),
            "total_charge_power": round(total_power_w / 1000, 2),  # W to kW
            "total_charge_current": round(total_current, 2),
            "voltage": round(voltage, 1),
            "total_charge_power_session": round(session_energy_kwh, 2),
            "completed_session_energy": round(session_energy_kwh, 2),  # Same as session energy
            "max_charge_current": charge_current_set,
            "temperature": round(humidity, 1),  # Using humidity as temperature placeholder
            "is_charging": operating_mode == 3,  # Mode 3 = charging
            "is_connected": operating_mode in [2, 3, 5]  # Connected if mode 2, 3, or 5
        }
        
        return parsed
    
    def _get_operating_mode_text(self, mode_code: int) -> str:
        """
        Convert operating mode code to text.
        Based on StateId 710 (ChargerOperationMode)
        """
        modes = {
            0: "Unknown",
            1: "Disconnected",
            2: "Connected - Waiting",
            3: "Charging",
            5: "Finished"
        }
        return modes.get(mode_code, f"Mode {mode_code}")
    
    def _update_charger_state(self, charger_id: str):
        """
        Update and publish charger state.
        
        Args:
            charger_id: The charger ID
        """
        charger = self.chargers.get(charger_id)
        if not charger:
            return
        
        # Get current state from API
        state_data = self.zaptec_api.get_charger_state(charger_id)
        if not state_data:
            self.logger.warning(f"Failed to get state for charger {charger_id}")
            return
        
        # Log first few items to understand structure
        if isinstance(state_data, list) and len(state_data) > 0:
            self.logger.debug(f"First 5 state items: {state_data[:5]}")
        
        # Parse and publish state
        parsed_state = self._parse_charger_state(state_data)
        
        serial = charger.get("MID") or charger.get("DeviceId") or charger.get("SerialNo", charger_id)
        safe_serial = self.ha_discovery._sanitize_topic_part(serial)
        node_id = f"zaptec_{safe_serial}"
        state_prefix = self.config["bridge"].get("state_topic_prefix", "zaptec")
        state_topic = f"{state_prefix}/{node_id}/state"
        
        self.mqtt_client.publish_state(state_topic, parsed_state)
        
        charger_name = charger.get('Name', charger_id)
        self.logger.info(f"Updated state for '{charger_name}':")
        self.logger.info(f"  - Mode: {parsed_state.get('operating_mode')}")
        self.logger.info(f"  - Power: {parsed_state.get('total_charge_power')} kW")
        self.logger.info(f"  - Current: {parsed_state.get('total_charge_current')} A")
        self.logger.info(f"  - Charging: {parsed_state.get('is_charging')}")
        self.logger.info(f"  - Connected: {parsed_state.get('is_connected')}")
    
    def _poll_chargers(self):
        """Poll all chargers for updates."""
        for charger_id in self.chargers.keys():
            try:
                self._update_charger_state(charger_id)
            except Exception as e:
                self.logger.error(f"Error updating charger {charger_id}: {e}")
    
    def start(self):
        """Start the bridge service."""
        self.logger.info("Starting Zaptec Home Assistant Bridge...")
        
        # Ensure we have valid authentication with Zaptec API
        if not self.zaptec_api._ensure_authenticated():
            self.logger.error("Failed to authenticate with Zaptec API")
            return False
        
        # Connect to MQTT broker
        if not self.mqtt_client.connect():
            self.logger.error("Failed to connect to MQTT broker")
            return False
        
        # Wait a bit for MQTT connection to establish
        time.sleep(2)
        
        # Discover chargers
        self._discover_chargers()
        
        if not self.chargers:
            self.logger.error("No chargers found, exiting")
            return False
        
        # Start polling loop
        self.running = True
        self.logger.info(f"Bridge started with dynamic polling:")
        self.logger.info(f"  - Charging mode: {self.poll_interval_charging}s interval")
        self.logger.info(f"  - Idle mode: {self.poll_interval_idle}s interval")
        self.logger.info(f"  - Boost duration: {self.boost_duration}s")
        
        try:
            while self.running:
                self._poll_chargers()
                time.sleep(self.current_poll_interval)
        except Exception as e:
            self.logger.error(f"Error in main loop: {e}", exc_info=True)
        
        return True
    
    def stop(self):
        """Stop the bridge service."""
        self.logger.info("Stopping Zaptec Home Assistant Bridge...")
        self.running = False
        self.mqtt_client.disconnect()


def main():
    """Main entry point."""
    try:
        bridge = ZaptecBridge()
        bridge.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
