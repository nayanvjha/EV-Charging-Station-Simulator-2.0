# EV Charging Station Simulator - Docker Image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create reports directory
RUN mkdir -p reports

# Expose ports
# 8000 - Controller API + Dashboard
# 9000 - CSMS WebSocket server
EXPOSE 8000 9000

# Default command (can be overridden)
CMD ["python", "start_all.py"]
