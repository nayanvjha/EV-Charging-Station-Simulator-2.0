# Deployment Guide - EV Charging Station Simulator

## Quick Deploy Options

### Option 1: Docker (Recommended) 🐳

**Prerequisites:** Docker and Docker Compose installed

```bash
# Build and run
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

**Access:**
- Dashboard: http://localhost:8000
- CSMS WebSocket: ws://localhost:9000/ocpp/<station_id>

---

### Option 2: Local Python 🐍

```bash
# Terminal 1: Start CSMS
python csms_server.py

# Terminal 2: Start API
uvicorn controller_api:app --host 0.0.0.0 --port 8000
```

Or use the combined launcher:
```bash
python start_all.py
```

---

## Cloud Deployment

### AWS EC2

1. **Launch EC2 Instance:**
   - Amazon Linux 2 or Ubuntu 22.04
   - t3.medium or larger
   - Security Group: Open ports 8000, 9000

2. **Connect and Setup:**
   ```bash
   # SSH to instance
   ssh -i your-key.pem ec2-user@your-instance-ip
   
   # Install Docker
   sudo yum update -y
   sudo yum install docker -y
   sudo service docker start
   sudo usermod -a -G docker ec2-user
   
   # Install Docker Compose
   sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
   sudo chmod +x /usr/local/bin/docker-compose
   
   # Clone and run
   git clone https://github.com/your-repo/ev-charging-sim.git
   cd ev-charging-sim
   docker-compose up -d
   ```

3. **Access:**
   - http://your-ec2-ip:8000

---

### Google Cloud Run

1. **Build and push image:**
   ```bash
   # Authenticate
   gcloud auth configure-docker
   
   # Build
   docker build -t gcr.io/YOUR_PROJECT/ev-simulator .
   
   # Push
   docker push gcr.io/YOUR_PROJECT/ev-simulator
   ```

2. **Deploy:**
   ```bash
   gcloud run deploy ev-simulator \
     --image gcr.io/YOUR_PROJECT/ev-simulator \
     --platform managed \
     --port 8000 \
     --allow-unauthenticated
   ```

---

### Heroku

1. **Create Procfile:**
   ```
   web: uvicorn controller_api:app --host 0.0.0.0 --port $PORT
   ```

2. **Deploy:**
   ```bash
   heroku create ev-charging-sim
   heroku stack:set container
   git push heroku main
   ```

---

### Railway.app (Easiest)

1. Go to https://railway.app
2. Click "New Project" → "Deploy from GitHub"
3. Select your repository
4. Railway auto-detects Python and deploys!

---

### DigitalOcean App Platform

1. Go to https://cloud.digitalocean.com/apps
2. Create App → GitHub repo
3. Configure:
   - Build Command: `pip install -r requirements.txt`
   - Run Command: `python start_all.py`
4. Deploy!

---

## Production Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_HOST` | `0.0.0.0` | API bind address |
| `API_PORT` | `8000` | API port |
| `CSMS_PORT` | `9000` | CSMS WebSocket port |

### Nginx Reverse Proxy (Optional)

For SSL/HTTPS, create `nginx.conf`:

```nginx
events {
    worker_connections 1024;
}

http {
    upstream api {
        server ev-simulator:8000;
    }
    
    upstream csms {
        server ev-simulator:9000;
    }

    server {
        listen 80;
        server_name your-domain.com;
        
        # Redirect to HTTPS
        return 301 https://$server_name$request_uri;
    }

    server {
        listen 443 ssl;
        server_name your-domain.com;
        
        ssl_certificate /etc/nginx/ssl/cert.pem;
        ssl_certificate_key /etc/nginx/ssl/key.pem;
        
        # Dashboard & API
        location / {
            proxy_pass http://api;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
        
        # CSMS WebSocket
        location /ocpp/ {
            proxy_pass http://csms;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host $host;
        }
    }
}
```

---

## Monitoring

### Health Check Endpoint

```bash
curl http://localhost:8000/stations
```

### Prometheus Metrics

```bash
curl http://localhost:8000/metrics
```

### Docker Logs

```bash
docker-compose logs -f --tail=100
```

---

## Scaling

### Horizontal Scaling with Docker Swarm

```bash
# Initialize swarm
docker swarm init

# Deploy stack
docker stack deploy -c docker-compose.yml ev-sim

# Scale
docker service scale ev-sim_ev-simulator=3
```

### Kubernetes

Create `k8s-deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ev-simulator
spec:
  replicas: 2
  selector:
    matchLabels:
      app: ev-simulator
  template:
    metadata:
      labels:
        app: ev-simulator
    spec:
      containers:
      - name: ev-simulator
        image: your-registry/ev-simulator:latest
        ports:
        - containerPort: 8000
        - containerPort: 9000
---
apiVersion: v1
kind: Service
metadata:
  name: ev-simulator
spec:
  type: LoadBalancer
  ports:
  - name: api
    port: 8000
    targetPort: 8000
  - name: csms
    port: 9000
    targetPort: 9000
  selector:
    app: ev-simulator
```

Deploy:
```bash
kubectl apply -f k8s-deployment.yaml
```

---

## Troubleshooting

### Port Already in Use

```bash
# Find process using port
lsof -i :8000
lsof -i :9000

# Kill process
kill -9 <PID>
```

### Docker Container Won't Start

```bash
# Check logs
docker-compose logs

# Rebuild
docker-compose build --no-cache
docker-compose up -d
```

### WebSocket Connection Failed

- Ensure port 9000 is open in firewall
- Check security groups (AWS/GCP)
- Verify CSMS is running: `curl -I ws://localhost:9000`

---

## Quick Commands Reference

```bash
# Local development
python start_all.py

# Docker
docker-compose up -d          # Start
docker-compose down           # Stop
docker-compose logs -f        # Logs
docker-compose restart        # Restart

# Check services
curl http://localhost:8000/stations
curl http://localhost:8000/pricing

# Test SmartCharging
curl -X POST http://localhost:8000/stations/PY-SIM-0001/test_profiles \
  -H "Content-Type: application/json" \
  -d '{"scenario": "peak_shaving", "connector_id": 1, "max_power_w": 7400}'
```

---

## Support

- **Issues:** GitHub Issues
- **Documentation:** See `COMPLETE_DOCUMENTATION.md`
- **API Docs:** http://localhost:8000/docs (Swagger UI)
