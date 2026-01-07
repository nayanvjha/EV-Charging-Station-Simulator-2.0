"""
Start All Services - Combined launcher for deployment

Starts both CSMS server and Controller API in a single process.
"""

import asyncio
import subprocess
import sys
import signal
import os

def main():
    print("=" * 60)
    print("EV Charging Station Simulator - Starting Services")
    print("=" * 60)
    
    # Get host/port from environment or use defaults
    api_host = os.environ.get("API_HOST", "0.0.0.0")
    api_port = os.environ.get("API_PORT", "8000")
    csms_port = os.environ.get("CSMS_PORT", "9000")
    
    print(f"\n📡 CSMS WebSocket Server: ws://0.0.0.0:{csms_port}/ocpp/<station_id>")
    print(f"🌐 Dashboard & API: http://0.0.0.0:{api_port}")
    print("\n" + "=" * 60)
    
    # Start CSMS server in background
    csms_process = subprocess.Popen(
        [sys.executable, "csms_server.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    
    # Start Controller API (uvicorn)
    api_process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "controller_api:app",
            "--host", api_host,
            "--port", api_port
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    
    def signal_handler(sig, frame):
        print("\n\n🛑 Shutting down services...")
        csms_process.terminate()
        api_process.terminate()
        csms_process.wait()
        api_process.wait()
        print("✅ All services stopped")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("\n✅ Services started! Press Ctrl+C to stop.\n")
    
    # Stream output from both processes
    import threading
    
    def stream_output(process, prefix):
        for line in iter(process.stdout.readline, b''):
            print(f"[{prefix}] {line.decode().strip()}")
    
    csms_thread = threading.Thread(target=stream_output, args=(csms_process, "CSMS"))
    api_thread = threading.Thread(target=stream_output, args=(api_process, "API"))
    
    csms_thread.daemon = True
    api_thread.daemon = True
    
    csms_thread.start()
    api_thread.start()
    
    # Wait for processes
    try:
        csms_process.wait()
        api_process.wait()
    except KeyboardInterrupt:
        signal_handler(None, None)


if __name__ == "__main__":
    main()
