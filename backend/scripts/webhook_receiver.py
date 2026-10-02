"""Local stand-in for a Slack/Teams webhook, for demos and testing.

Accepts alert POSTs from the worker (ALERT_WEBHOOK_URL), appends each JSON
body as one line to a log file, and returns 200 so the alert is marked
delivered. Replace ALERT_WEBHOOK_URL with a real endpoint in production.

Usage: python scripts/webhook_receiver.py [port] [log_path]
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9099
LOG_PATH = sys.argv[2] if len(sys.argv) > 2 else "./data/webhook_received.log"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Lets the dashboard's "Webhook inbox" panel show what was received.
        lines = []
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, encoding="utf-8") as f:
                lines = [json.loads(line) for line in f if line.strip()]
        body = json.dumps(lines[-100:][::-1]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(body.decode("utf-8") + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
    print(f"webhook receiver on :{PORT}, logging to {LOG_PATH}")
    # Threaded: browsers open idle speculative connections that would
    # otherwise block a single-threaded server and make POSTs time out.
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
