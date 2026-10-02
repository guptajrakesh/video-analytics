from __future__ import annotations

import logging
import os
import sys

from sync.push import run_sync_loop

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    central_api_url = os.environ.get("CENTRAL_API_URL", "")
    if not central_api_url:
        logging.error("CENTRAL_API_URL is required for the sync worker")
        sys.exit(1)
    run_sync_loop(central_api_url, interval_seconds=float(os.environ.get("SYNC_INTERVAL_SECONDS", "60")))
