"""CLI entrypoint for the RTSP grabber (see docker-compose.cloud.yml /
docker-compose.edge.yml — one instance per camera, parametrized by env)."""

from __future__ import annotations

import logging
import os

from ingestion.rtsp_grabber import record_segments

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    record_segments(
        rtsp_url=os.environ["RTSP_URL"],
        camera_id=os.environ["CAMERA_ID"],
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        segment_seconds=float(os.environ.get("SEGMENT_SECONDS", "30")),
    )
