"""
MQTT Client for Home Assistant Integration
Handles MQTT connection, discovery, and message publishing.
"""

import json
import logging
import threading
from typing import Dict, Callable, Optional
import paho.mqtt.client as mqtt

logger = logging.getLogger(__name__)

# Max seconds publish() waits for a QoS 1 ack. Never wait forever: if the
# connection drops mid-publish the ack may never arrive.
PUBLISH_TIMEOUT = 5.0


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
        # Optional hook run after every successful (re)connect, so the bridge can
        # re-publish discovery (retained discovery is lost if the broker restarts).
        self.on_connect_callback: Optional[Callable] = None
        
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
            if self.on_connect_callback:
                try:
                    self.on_connect_callback()
                except Exception as e:
                    logger.error(f"on_connect callback failed: {e}")
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
                msg.wait_for_publish(timeout=PUBLISH_TIMEOUT)
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
            if self._in_network_thread():
                # Callbacks (on_connect -> discovery re-publish, on_message) run on
                # paho's network thread, which is the thread that processes the
                # PUBACK. Waiting here would deadlock it, so just queue the message.
                return result.rc == mqtt.MQTT_ERR_SUCCESS
            result.wait_for_publish(timeout=PUBLISH_TIMEOUT)
            if not result.is_published():
                logger.warning(f"Publish to {topic} not acknowledged within {PUBLISH_TIMEOUT}s")
                return False
            return True
        except Exception as e:
            logger.error(f"Failed to publish to {topic}: {e}")
            return False
    
    def _in_network_thread(self) -> bool:
        """True when called from paho's loop_start() network thread."""
        return threading.current_thread() is getattr(self.client, "_thread", None)

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
