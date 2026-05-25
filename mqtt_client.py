"""
MQTT Client for Home Assistant Integration
Handles MQTT connection, discovery, and message publishing.
"""

import json
import logging
from typing import Dict, Callable, Optional
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)


class MQTTClient:
    """MQTT client for Home Assistant integration."""
    
    def __init__(self, host: str, port: int, username: str, password: str,
                 client_id: str = "zaptec-bridge", availability_topic: Optional[str] = None):
        """
        Initialize MQTT client.
        
        Args:
            host: MQTT broker hostname/IP
            port: MQTT broker port
            username: MQTT username
            password: MQTT password
            client_id: MQTT client ID
        """
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client_id = client_id
        self.availability_topic = availability_topic

        self.client = mqtt.Client(client_id=client_id)
        self.client.username_pw_set(username, password)
        # Last Will: broker marks us offline if the connection drops, so HA
        # shows entities "unavailable" instead of stale retained values.
        if self.availability_topic:
            self.client.will_set(self.availability_topic, "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        
        self.connected = False
        self.message_callbacks: Dict[str, Callable] = {}
        
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        if rc == 0:
            logger.info("Connected to MQTT broker")
            self.connected = True
            # Announce online (retained), then resubscribe (also covers reconnects).
            if self.availability_topic:
                self.client.publish(self.availability_topic, "online", qos=1, retain=True)
            for topic in self.message_callbacks.keys():
                self.client.subscribe(topic)
                logger.debug(f"Subscribed to {topic}")
        else:
            logger.error(f"Failed to connect to MQTT broker: {rc}")
            self.connected = False
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from MQTT broker."""
        logger.warning(f"Disconnected from MQTT broker: {rc}")
        self.connected = False
    
    def _on_message(self, client, userdata, msg):
        """Callback when message received."""
        topic = msg.topic
        payload = msg.payload.decode('utf-8')
        logger.debug(f"Received message on {topic}: {payload}")
        
        # Call registered callback for this topic
        if topic in self.message_callbacks:
            try:
                self.message_callbacks[topic](topic, payload)
            except Exception as e:
                logger.error(f"Error in message callback for {topic}: {e}")
    
    def connect(self) -> bool:
        """
        Connect to MQTT broker.
        
        Returns:
            True if connection successful
        """
        try:
            logger.info(f"Connecting to MQTT broker at {self.host}:{self.port}...")
            self.client.connect(self.host, self.port, 60)
            self.client.loop_start()
            return True
        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from MQTT broker."""
        logger.info("Disconnecting from MQTT broker...")
        # Publish offline before a graceful disconnect (the LWT only fires on an
        # ungraceful drop).
        if self.availability_topic and self.connected:
            try:
                msg = self.client.publish(self.availability_topic, "offline", qos=1, retain=True)
                msg.wait_for_publish()
            except Exception:
                pass
        self.client.loop_stop()
        self.client.disconnect()
    
    def publish(self, topic: str, payload: str, retain: bool = False) -> bool:
        """
        Publish a message to MQTT.
        
        Args:
            topic: MQTT topic
            payload: Message payload
            retain: Whether to retain the message
            
        Returns:
            True if publish successful
        """
        if not self.connected:
            logger.warning("Not connected to MQTT broker")
            return False
        
        try:
            result = self.client.publish(topic, payload, qos=1, retain=retain)
            result.wait_for_publish()
            return result.is_published()
        except Exception as e:
            logger.error(f"Failed to publish to {topic}: {e}")
            return False
    
    def subscribe(self, topic: str, callback: Callable):
        """
        Subscribe to a topic and register callback.
        
        Args:
            topic: MQTT topic to subscribe to
            callback: Function to call when message received
        """
        self.message_callbacks[topic] = callback
        if self.connected:
            self.client.subscribe(topic)
            logger.debug(f"Subscribed to {topic}")
    
    def publish_discovery(self, component: str, node_id: str, object_id: str, 
                         config: Dict, discovery_prefix: str = "homeassistant"):
        """
        Publish Home Assistant MQTT discovery message.
        
        Args:
            component: Component type (sensor, binary_sensor, switch, etc.)
            node_id: Node ID (e.g., charger serial)
            object_id: Object ID (e.g., power, state)
            config: Discovery configuration dictionary
            discovery_prefix: MQTT discovery prefix
        """
        topic = f"{discovery_prefix}/{component}/{node_id}/{object_id}/config"
        payload = json.dumps(config)
        
        logger.debug(f"Publishing discovery to {topic}")
        self.publish(topic, payload, retain=True)
    
    def publish_state(self, state_topic: str, state: Dict):
        """
        Publish state data.
        
        Args:
            state_topic: Topic to publish to
            state: State data dictionary
        """
        payload = json.dumps(state)
        self.publish(state_topic, payload, retain=True)
