"""CLI entrypoint for the edge-mode folder watcher (see docker-compose.edge.yml).
Separate from folder_watcher.py's library function so the function itself
stays unit-testable without reading from the environment.
"""

from __future__ import annotations

import logging
import os

from ingestion.common import SourceType
from ingestion.folder_watcher import watch_folder

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    watch_folder(
        folder=os.environ["WATCH_FOLDER"],
        camera_id=os.environ["CAMERA_ID"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        source_type=SourceType(os.environ.get("WATCH_SOURCE_TYPE", "local_folder")),
        poll_interval=float(os.environ.get("POLL_INTERVAL_SECONDS", "10")),
        # The watched folder is usually mounted read-only, so the "already seen"
        # list has to live somewhere writable.
        state_path=os.environ.get("WATCHER_STATE_PATH") or None,
    )
