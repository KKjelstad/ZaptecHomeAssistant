FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY zaptec_api.py .
COPY mqtt_client.py .
COPY ha_discovery.py .
COPY zaptec_bridge.py .

# Create volume for config
VOLUME /app/config

# Run the bridge
CMD ["python", "zaptec_bridge.py"]
