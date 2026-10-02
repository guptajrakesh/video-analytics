"""One-off end-to-end smoke test: real video -> detector -> tracker ->
aggregator -> sqlite DB. Not part of the pytest suite (needs the full ML
stack + a real video file); run manually during development."""
import os
import sys
import datetime

os.environ["DATABASE_URL"] = "sqlite:///./e2e_smoke_test.db"
sys.path.insert(0, ".")

from ingestion.common import FrameJob, SourceType
from models.db import init_db, get_session
from models.schema import Camera, Vertical, Zone, ZoneType, Video, Track, FootfallEvent, ZoneEvent, PositionSample
from pipeline.detector import PersonDetector
from pipeline.run import process_video

if os.path.exists("./e2e_smoke_test.db"):
    os.remove("./e2e_smoke_test.db")

init_db()

with get_session() as session:
    session.add(Camera(id="cam1", name="Mall Corridor Test Cam", vertical=Vertical.RETAIL_SHOWROOM))
    session.add(
        Zone(
            id="zone1",
            camera_id="cam1",
            name="Display Section",
            zone_type=ZoneType.DISPLAY_SECTION,
            polygon=[[200, 100], [600, 100], [600, 360], [200, 360]],
        )
    )

job = FrameJob(
    camera_id="cam1",
    video_path="../sample_videos/mall_walking_test.mp4",
    source_type=SourceType.UPLOAD,
    recorded_at=datetime.datetime(2026, 1, 15, 14, 30, 0, tzinfo=datetime.timezone.utc).isoformat(),
)

camera_config = {
    "entrance_line": ((0, 180), (640, 180)),  # horizontal line through middle of frame
    "zones": [{"id": "zone1", "polygon": [(200, 100), (600, 100), (600, 360), (200, 360)]}],
}

detector = PersonDetector()
process_video(job, detector, camera_config)

with get_session() as session:
    n_videos = session.query(Video).count()
    n_tracks = session.query(Track).count()
    n_footfall = session.query(FootfallEvent).count()
    n_zone_events = session.query(ZoneEvent).count()
    n_positions = session.query(PositionSample).count()

    print(f"videos: {n_videos}")
    print(f"tracks: {n_tracks}")
    print(f"footfall events: {n_footfall}")
    print(f"zone events: {n_zone_events}")
    print(f"position samples: {n_positions}")

    footfall_in = session.query(FootfallEvent).filter_by(direction="in").count()
    footfall_out = session.query(FootfallEvent).filter_by(direction="out").count()
    print(f"  -> in: {footfall_in}, out: {footfall_out}")

    zone_enters = session.query(ZoneEvent).filter_by(event_type="enter").count()
    zone_exits = session.query(ZoneEvent).filter_by(event_type="exit").count()
    print(f"  -> zone enters: {zone_enters}, exits: {zone_exits}")

    sample = session.query(Track).first()
    print(f"sample track: id={sample.id}, local_id={sample.local_track_id}, first_seen={sample.first_seen}, last_seen={sample.last_seen}")

print("--- E2E SMOKE TEST PASSED ---")
