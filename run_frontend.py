#!/usr/bin/env python3
"""
Launcher script for the DocIntel AI Web Dashboard.
Starts a local web server and automatically opens the application in your browser.
Pure Python standard library - zero additional dependencies required.
"""

import http.server
import json
import os
import socket
import socketserver
import sys
import threading
import time
import webbrowser

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
OUTPUTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cdk-outputs.json")


def find_free_port(preferred_port=3000):
    """Finds an available TCP port starting from preferred_port."""
    for port in range(preferred_port, preferred_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return preferred_port


def get_live_api_endpoint():
    """Reads the live API Gateway endpoint from cdk-outputs.json."""
    if os.path.exists(OUTPUTS_FILE):
        try:
            with open(OUTPUTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for stack in data.values():
                    if "ApiEndpoint" in stack:
                        return stack["ApiEndpoint"].rstrip("/")
        except Exception:
            pass
    return None


class FrontendHandler(http.server.SimpleHTTPRequestHandler):
    """Serves static files from the frontend directory with proper MIME types."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def end_headers(self):
        # Enable caching-busting during development and CORS
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def log_message(self, format, *args):
        # Suppress routine GET request noise in console
        pass


def main():
    if not os.path.exists(FRONTEND_DIR):
        print(f"[!] Error: Frontend directory not found at {FRONTEND_DIR}")
        sys.exit(1)

    port = find_free_port(3000)
    api_endpoint = get_live_api_endpoint()

    print("=================================================================")
    print("  DocIntel AI: Serverless Document Intelligence Web Dashboard   ")
    print("=================================================================")
    print(f"  Local Dashboard URL:  http://localhost:{port}")
    if api_endpoint:
        print(f"  Connected AWS API:    {api_endpoint}")
    print("=================================================================")
    print("  Press Ctrl+C in terminal to stop the web server at any time.   ")
    print("=================================================================")

    # Open web browser after a brief 0.5s pause
    def open_browser():
        time.sleep(0.5)
        webbrowser.open(f"http://localhost:{port}")

    threading.Thread(target=open_browser, daemon=True).start()

    # Start HTTP Server
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), FrontendHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[+] Web server stopped cleanly.")


if __name__ == "__main__":
    main()
