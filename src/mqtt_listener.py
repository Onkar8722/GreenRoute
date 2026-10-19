"""
MQTT listener for IoT bin data ingestion.
Subscribes to MQTT topics and updates bin data in real-time.
"""

import json
import logging
import threading
from datetime import datetime
from typing import Callable, Optional
import paho.mqtt.client as mqtt
import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)


class MQTTListener:
    """MQTT client for real-time bin data collection."""
    
    def __init__(self, broker_host: str = 'localhost', 
                 broker_port: int = 1883,
                 db_config: dict = None,
                 on_message_callback: Optional[Callable] = None):
        """
        Initialize MQTT listener.
        
        Args:
            broker_host: MQTT broker hostname
            broker_port: MQTT broker port
            db_config: Database configuration dict
            on_message_callback: Optional callback when message received
        """
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.db_config = db_config or {
            'host': 'localhost',
            'port': 5432,
            'database': 'greenroute_db',
            'user': 'greenroute_user',
            'password': 'greenroute_pass'
        }
        self.on_message_callback = on_message_callback
        
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        
        self.connected = False
        self.running = False
        self.message_count = 0
        self.error_count = 0
    
    def _on_connect(self, client, userdata, flags, rc):
        """Called when MQTT client connects."""
        if rc == 0:
            logger.info(f"Connected to MQTT broker at {self.broker_host}:{self.broker_port}")
            self.connected = True
            
            # Subscribe to bin update topics
            client.subscribe("greenroute/bins/update")
            client.subscribe("greenroute/bins/+/fill")
            client.subscribe("greenroute/bins/+/status")
            logger.info("Subscribed to bin update topics")
        else:
            logger.error(f"Failed to connect to MQTT broker. Code: {rc}")
            self.connected = False
    
    def _on_disconnect(self, client, userdata, rc):
        """Called when MQTT client disconnects."""
        if rc != 0:
            logger.warning(f"Unexpected disconnection from MQTT. Code: {rc}")
        else:
            logger.info("Disconnected from MQTT broker")
        self.connected = False
    
    def _on_message(self, client, userdata, msg):
        """Process incoming MQTT message."""
        try:
            payload = json.loads(msg.payload.decode())
            self.message_count += 1
            
            # Route message based on topic
            topic = msg.topic
            
            if "bins/update" in topic:
                self._process_bin_update(payload)
            elif "/fill" in topic:
                self._process_bin_fill(topic, payload)
            elif "/status" in topic:
                self._process_bin_status(topic, payload)
            
            # Call user callback if provided
            if self.on_message_callback:
                self.on_message_callback(topic, payload)
            
            logger.debug(f"Processed message on topic {topic}")
        
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON payload: {e}")
            self.error_count += 1
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")
            self.error_count += 1
    
    def _process_bin_update(self, payload: dict):
        """Process bin update message."""
        try:
            bin_id = payload.get('bin_id')
            fill_level = payload.get('fill_level')
            status = payload.get('status', 0)
            battery = payload.get('battery_level')
            signal = payload.get('signal_strength')
            
            if not bin_id or fill_level is None:
                logger.warning(f"Invalid bin update payload: {payload}")
                return
            
            self._update_bin_in_db(
                bin_id=bin_id,
                fill_level=fill_level,
                status=status,
                battery_level=battery,
                signal_strength=signal
            )
        except Exception as e:
            logger.error(f"Error processing bin update: {e}")
            self.error_count += 1
    
    def _process_bin_fill(self, topic: str, payload: dict):
        """Process bin fill level update."""
        try:
            bin_id = topic.split('/')[-2]  # Extract bin_id from topic
            fill_level = payload.get('fill_level')
            
            if bin_id and fill_level is not None:
                self._update_bin_in_db(bin_id, fill_level=fill_level)
        except Exception as e:
            logger.error(f"Error processing fill update: {e}")
            self.error_count += 1
    
    def _process_bin_status(self, topic: str, payload: dict):
        """Process bin status update."""
        try:
            bin_id = topic.split('/')[-2]  # Extract bin_id from topic
            status = payload.get('status', 0)
            
            if bin_id:
                self._update_bin_in_db(bin_id, status=status)
        except Exception as e:
            logger.error(f"Error processing status update: {e}")
            self.error_count += 1
    
    def _update_bin_in_db(self, bin_id: str, fill_level: int = None, 
                         status: int = None, battery_level: int = None, 
                         signal_strength: int = None):
        """Update bin data in database."""
        try:
            conn = psycopg2.connect(**self.db_config)
            cursor = conn.cursor()
            
            updates = []
            params = []
            
            if fill_level is not None:
                updates.append("fill_level = %s")
                params.append(fill_level)
            
            if status is not None:
                updates.append("status = %s")
                params.append(status)
            
            if battery_level is not None:
                updates.append("battery_level = %s")
                params.append(battery_level)
            
            if signal_strength is not None:
                updates.append("signal_strength = %s")
                params.append(signal_strength)
            
            if updates:
                updates.append("last_updated = NOW()")
                
                query = f"UPDATE bins SET {', '.join(updates)} WHERE id = %s"
                params.append(bin_id)
                
                cursor.execute(query, params)
                
                # Also record fill history if fill_level provided
                if fill_level is not None:
                    cursor.execute("""
                        INSERT INTO fill_history (bin_id, fill_level, recorded_at)
                        VALUES (%s, %s, NOW())
                    """, (bin_id, fill_level))
                
                conn.commit()
                logger.debug(f"Updated bin {bin_id} in database")
            
            cursor.close()
            conn.close()
        
        except Exception as e:
            logger.error(f"Database error updating bin {bin_id}: {e}")
            self.error_count += 1
    
    def connect_and_loop(self):
        """Connect to MQTT broker and start listening loop."""
        try:
            logger.info(f"Connecting to MQTT broker {self.broker_host}:{self.broker_port}...")
            self.client.connect(self.broker_host, self.broker_port, keepalive=60)
            self.running = True
            self.client.loop_forever()
        except Exception as e:
            logger.error(f"MQTT connection error: {e}")
            self.running = False
    
    def start_background(self):
        """Start MQTT listener in background thread."""
        if self.running:
            logger.warning("MQTT listener is already running")
            return
        
        thread = threading.Thread(target=self.connect_and_loop, daemon=True)
        thread.start()
        logger.info("MQTT listener started in background")
        
        # Wait for connection
        import time
        for _ in range(10):
            if self.connected:
                return
            time.sleep(0.5)
        
        logger.warning("MQTT listener did not connect within timeout")
    
    def stop(self):
        """Stop MQTT listener."""
        if self.running:
            self.client.disconnect()
            self.running = False
            logger.info("MQTT listener stopped")
    
    def get_stats(self) -> dict:
        """Get listener statistics."""
        return {
            "connected": self.connected,
            "running": self.running,
            "message_count": self.message_count,
            "error_count": self.error_count,
            "broker": f"{self.broker_host}:{self.broker_port}"
        }
