"""
Home Assistant MQTT Discovery
Creates entities in Home Assistant via MQTT discovery protocol.
"""

import logging
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class HomeAssistantDiscovery:
    """Handles Home Assistant MQTT discovery."""
    
    def __init__(self, mqtt_client, discovery_prefix: str = "homeassistant",
                 state_topic_prefix: str = "zaptec", availability_topic: Optional[str] = None):
        """
        Initialize HA discovery helper.
        
        Args:
            mqtt_client: MQTTClient instance
            discovery_prefix: MQTT discovery prefix
            state_topic_prefix: Prefix for state topics
        """
        self.mqtt = mqtt_client
        self.discovery_prefix = discovery_prefix
        self.state_topic_prefix = state_topic_prefix
        self.availability_topic = availability_topic

    def _apply_availability(self, config: Dict):
        """Add availability (online/offline) to an entity config so HA marks it
        unavailable when the bridge is down, instead of showing stale state."""
        if self.availability_topic:
            config["availability_topic"] = self.availability_topic
            config["payload_available"] = "online"
            config["payload_not_available"] = "offline"

    @staticmethod
    def _sanitize_topic_part(text: str) -> str:
        """
        Sanitize text for use in MQTT topics.
        Removes special characters and replaces spaces with underscores.
        Handles Norwegian characters: æ→ae, ø→o, å→a
        
        Args:
            text: Text to sanitize
            
        Returns:
            Sanitized text safe for MQTT topics
        """
        # Replace Norwegian characters
        replacements = {
            'æ': 'ae', 'Æ': 'Ae',
            'ø': 'o', 'Ø': 'O',
            'å': 'a', 'Å': 'A',
            'ä': 'a', 'Ä': 'A',
            'ö': 'o', 'Ö': 'O',
        }
        
        for old, new in replacements.items():
            text = text.replace(old, new)
        
        # Replace spaces and special characters with underscores
        text = re.sub(r'[^\w\-]', '_', text)
        
        # Remove duplicate underscores
        text = re.sub(r'_+', '_', text)
        
        # Remove leading/trailing underscores
        text = text.strip('_')
        
        return text
    
    def _get_device_info(self, charger_data: Dict) -> Dict:
        """
        Generate device info for Home Assistant.
        
        Args:
            charger_data: Charger data from API
            
        Returns:
            Device info dictionary
        """
        charger_id = charger_data.get("Id", "unknown")
        charger_name = charger_data.get("Name", f"Zaptec Charger {charger_id}")
        
        # The API returns charger name as SerialNo sometimes, try different fields
        serial = charger_data.get("MID") or charger_data.get("DeviceId") or charger_data.get("SerialNo", charger_id)
        device_id = charger_data.get("DeviceId", serial)
        
        # DeviceType is a number, map it to actual model names
        device_type_id = charger_data.get("DeviceType", 0)
        device_type_map = {
            1: "Zaptec Go",
            2: "Zaptec Pro",
            3: "Zaptec Home",
            4: "Zaptec Pro+",
        }
        model = device_type_map.get(device_type_id, f"Zaptec Charger (Type {device_type_id})")
        
        firmware_version = charger_data.get("FirmwareVersion") or charger_data.get("SoftwareVersion", "unknown")
        
        safe_serial = self._sanitize_topic_part(serial)
        
        # Build identifiers list - use multiple IDs for better matching
        identifiers = [f"zaptec_{safe_serial}"]
        if device_id and device_id != serial:
            identifiers.append(f"zaptec_{device_id}")
        
        device_info = {
            "identifiers": identifiers,
            "name": charger_name,
            "manufacturer": "Zaptec",
            "model": model,
            "sw_version": firmware_version,
            "serial_number": serial,
            "configuration_url": f"https://portal.zaptec.com/charger/{charger_id}"
        }
        
        # Log device info for debugging
        logger.info(f"Device Info for '{charger_name}':")
        logger.info(f"  - Charger ID: {charger_id}")
        logger.info(f"  - Serial Number: {serial}")
        logger.info(f"  - Device ID: {device_id}")
        logger.info(f"  - Model: {model} (Type: {device_type_id})")
        logger.info(f"  - Firmware: {firmware_version}")
        logger.info(f"  - Identifiers: {identifiers}")
        
        return device_info
    
    def create_charger_entities(self, charger_data: Dict):
        """
        Create all entities for a charger.
        
        Args:
            charger_data: Charger data from API
        """
        charger_id = charger_data.get("Id", "unknown")
        charger_name = charger_data.get("Name", f"Zaptec {charger_id}")
        
        # Use MID (actual serial) or DeviceId for topic, not the charger name
        serial = charger_data.get("MID") or charger_data.get("DeviceId") or charger_data.get("SerialNo", charger_id)
        
        # Sanitize serial for MQTT topic (removes special chars like ø, æ, å)
        safe_serial = self._sanitize_topic_part(serial)
        node_id = f"zaptec_{safe_serial}"
        state_topic = f"{self.state_topic_prefix}/{node_id}/state"
        
        device_info = self._get_device_info(charger_data)
        
        logger.info(f"Creating Home Assistant entities for charger '{charger_name}' (topic: {node_id})")
        logger.info(f"  State topic: {state_topic}")
        logger.info(f"  Command topics: {self.state_topic_prefix}/{node_id}/set, {self.state_topic_prefix}/{node_id}/set_current")
        
        # Sensor: Operating Mode / State
        self._create_sensor(
            node_id=node_id,
            object_id="operating_mode",
            name="Operating Mode",
            device_info=device_info,
            state_topic=state_topic,
            value_template="{{ value_json.operating_mode }}",
            icon="mdi:ev-station"
        )
        
        # Sensor: Total Charge Power
        self._create_sensor(
            node_id=node_id,
            object_id="total_charge_power",
            name="Total Charge Power",
            device_info=device_info,
            state_topic=state_topic,
            value_template="{{ value_json.total_charge_power }}",
            unit_of_measurement="kW",
            device_class="power",
            state_class="measurement",
            icon="mdi:flash"
        )
        
        # Sensor: Total Charge Current
        self._create_sensor(
            node_id=node_id,
            object_id="total_charge_current",
            name="Total Charge Current",
            device_info=device_info,
            state_topic=state_topic,
            value_template="{{ value_json.total_charge_current }}",
            unit_of_measurement="A",
            device_class="current",
            state_class="measurement"
        )
        
        # Sensor: Voltage
        self._create_sensor(
            node_id=node_id,
            object_id="voltage",
            name="Voltage",
            device_info=device_info,
            state_topic=state_topic,
            value_template="{{ value_json.voltage }}",
            unit_of_measurement="V",
            device_class="voltage",
            state_class="measurement"
        )
        
        # Sensor: Total Energy
        self._create_sensor(
            node_id=node_id,
            object_id="total_charge_power_session",
            name="Session Energy",
            device_info=device_info,
            state_topic=state_topic,
            value_template="{{ value_json.total_charge_power_session }}",
            unit_of_measurement="kWh",
            device_class="energy",
            state_class="total_increasing",
            icon="mdi:lightning-bolt"
        )
        
        # Sensor: Completed Session Energy
        self._create_sensor(
            node_id=node_id,
            object_id="completed_session_energy",
            name="Completed Session Energy",
            device_info=device_info,
            state_topic=state_topic,
            value_template="{{ value_json.completed_session_energy }}",
            unit_of_measurement="kWh",
            device_class="energy",
            icon="mdi:battery-charging"
        )
        
        # Sensor: Temperature
        self._create_sensor(
            node_id=node_id,
            object_id="temperature",
            name="Temperature",
            device_info=device_info,
            state_topic=state_topic,
            value_template="{{ value_json.temperature }}",
            unit_of_measurement="°C",
            device_class="temperature",
            state_class="measurement"
        )
        
        # Binary Sensor: Is Charging
        self._create_binary_sensor(
            node_id=node_id,
            object_id="is_charging",
            name="Charging",
            device_info=device_info,
            state_topic=state_topic,
            value_template="{{ value_json.is_charging }}",
            device_class="battery_charging"
        )
        
        # Binary Sensor: Connected
        self._create_binary_sensor(
            node_id=node_id,
            object_id="is_connected",
            name="Connected",
            device_info=device_info,
            state_topic=state_topic,
            value_template="{{ value_json.is_connected }}",
            device_class="connectivity"
        )
        
        # Switch: Charging Control
        command_topic = f"{self.state_topic_prefix}/{node_id}/set"
        self._create_switch(
            node_id=node_id,
            object_id="charging",
            name="Charging",
            device_info=device_info,
            state_topic=state_topic,
            command_topic=command_topic,
            value_template="{{ value_json.is_charging }}",
            icon="mdi:ev-station"
        )
        
        # Number: Charge Current Limit
        self._create_number(
            node_id=node_id,
            object_id="charge_current_limit",
            name="Charge Current Limit",
            device_info=device_info,
            state_topic=state_topic,
            command_topic=f"{self.state_topic_prefix}/{node_id}/set_current",
            value_template="{{ value_json.max_charge_current }}",
            unit_of_measurement="A",
            min_value=6,
            max_value=32,
            step=1,
            icon="mdi:current-ac"
        )
    
    def _create_sensor(self, node_id: str, object_id: str, name: str, 
                       device_info: Dict, state_topic: str, value_template: str,
                       unit_of_measurement: Optional[str] = None,
                       device_class: Optional[str] = None,
                       state_class: Optional[str] = None,
                       icon: Optional[str] = None):
        """Create a sensor entity."""
        config = {
            "name": name,
            "unique_id": f"{node_id}_{object_id}",
            "state_topic": state_topic,
            "value_template": value_template,
            "device": device_info
        }
        
        if unit_of_measurement:
            config["unit_of_measurement"] = unit_of_measurement
        if device_class:
            config["device_class"] = device_class
        if state_class:
            config["state_class"] = state_class
        if icon:
            config["icon"] = icon
        self._apply_availability(config)

        self.mqtt.publish_discovery("sensor", node_id, object_id, config,
                                    self.discovery_prefix)
    
    def _create_binary_sensor(self, node_id: str, object_id: str, name: str,
                             device_info: Dict, state_topic: str, value_template: str,
                             device_class: Optional[str] = None,
                             icon: Optional[str] = None):
        """Create a binary sensor entity."""
        config = {
            "name": name,
            "unique_id": f"{node_id}_{object_id}",
            "state_topic": state_topic,
            "value_template": value_template,
            "payload_on": "true",
            "payload_off": "false",
            "device": device_info
        }
        
        if device_class:
            config["device_class"] = device_class
        if icon:
            config["icon"] = icon
        self._apply_availability(config)

        self.mqtt.publish_discovery("binary_sensor", node_id, object_id, config,
                                    self.discovery_prefix)
    
    def _create_switch(self, node_id: str, object_id: str, name: str,
                      device_info: Dict, state_topic: str, command_topic: str,
                      value_template: str, icon: Optional[str] = None):
        """Create a switch entity."""
        config = {
            "name": name,
            "unique_id": f"{node_id}_{object_id}",
            "state_topic": state_topic,
            "command_topic": command_topic,
            "value_template": value_template,
            "payload_on": "ON",
            "payload_off": "OFF",
            "state_on": "true",
            "state_off": "false",
            "device": device_info
        }
        
        if icon:
            config["icon"] = icon
        self._apply_availability(config)

        self.mqtt.publish_discovery("switch", node_id, object_id, config,
                                    self.discovery_prefix)
    
    def _create_number(self, node_id: str, object_id: str, name: str,
                      device_info: Dict, state_topic: str, command_topic: str,
                      value_template: str, min_value: float, max_value: float,
                      step: float, unit_of_measurement: Optional[str] = None,
                      icon: Optional[str] = None):
        """Create a number entity."""
        config = {
            "name": name,
            "unique_id": f"{node_id}_{object_id}",
            "state_topic": state_topic,
            "command_topic": command_topic,
            "value_template": value_template,
            "min": min_value,
            "max": max_value,
            "step": step,
            "device": device_info
        }
        
        if unit_of_measurement:
            config["unit_of_measurement"] = unit_of_measurement
        if icon:
            config["icon"] = icon
        self._apply_availability(config)

        self.mqtt.publish_discovery("number", node_id, object_id, config,
                                    self.discovery_prefix)
    
    def create_bridge_entities(self):
        """Create bridge-level control entities (e.g., poll boost button)."""
        node_id = "zaptec_bridge"
        
        device_info = {
            "identifiers": ["zaptec_bridge"],
            "name": "Zaptec Bridge",
            "manufacturer": "Zaptec Home Assistant Bridge",
            "model": "MQTT Bridge",
            "configuration_url": "https://github.com"
        }
        
        logger.info("Creating bridge control entities")
        
        # Button: Boost Poll Rate (for motion sensor automation)
        config = {
            "name": "Boost Poll Rate",
            "unique_id": f"{node_id}_poll_boost",
            "command_topic": f"{self.state_topic_prefix}/bridge/poll_boost",
            "payload_press": "ON",
            "icon": "mdi:speedometer",
            "device": device_info
        }
        
        self.mqtt.publish_discovery("button", node_id, "poll_boost", config,
                                    self.discovery_prefix)
        
        logger.info("  - Created Poll Boost button")

