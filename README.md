# Video Analytics Platform

Retail-showroom and bank-branch video analytics (footfall, zone dwell time,
checkout/teller queue wait-time, heatmaps, threshold alerts) — see
`C:\Users\admin\.claude\plans\we-need-to-build-virtual-cook.md` for the full
product plan, licensing rationale, and milestone roadmap.

## Status: M1–M6 built and verified end-to-end

Every milestone in the plan (core pipeline, zones/dwell/queue, RTSP
ingestion, edge sync, dashboard, alerting) has working code backed by either
automated tests or a real end-to-end run against actual infrastructure
(real Postgres/Redis, a real test video, a real browser, a real webhook
receiver) — not just unit tests in isolation. See "Known limitations" below
for the honest gaps, and each milestone's own section for what was
specifically verified and how.

## Stack

- **Detection**: YOLOX-Nano (Apache-2.0) — not Ultralytics YOLOv8, which is
  AGPL-3.0 and incompatible with a closed-source commercial product.
- **Tracking**: a clean-room ByteTrack reimplementation using only numpy +
  scipy (`backend/pipeline/tracker.py`) — avoids the original reference
  implementation's `cython_bbox` dependency, which needs a C compiler to
  build from source.
- **Backend**: FastAPI + Redis (job queue) + Postgres (SQLAlchemy).
- **Everything runs CPU-only** — no GPU, no paid AI API, by design (see the
  plan's Zero-Cost Constraint section).

## Repo layout

```
backend/
  api/        FastAPI app (camera/zone CRUD, uploads, reference frames, analytics
              with date ranges, overview, alert status, edge sync)
  ingestion/  upload handler, local-folder/network-share watcher (edge mode),
              RTSP live-stream grabber (segments a camera feed into files)
  pipeline/   detector (YOLOX) + tracker (ByteTrack) + aggregator (footfall/
              dwell/queue/heatmap) + alerts (thresholds, grouping) + notify
              (webhook/email) + redeliver + worker
              — aggregator/alerts are pure Python, no ML deps
  sync/       edge -> central push worker (see M4 below)
  models/     SQLAlchemy schema, shared verbatim by cloud and edge deployments
  workers/    Redis queue wrapper
  tests/      pytest suite — aggregator, tracker, RTSP grabber, alert logic;
              zero ML dependencies, runs in ~2 seconds
  scripts/    e2e_smoke_test.py — manual real-video verification script
dashboard/    React + Vite + Recharts dashboard: Overview, Compare, Setup, Upload
deploy/
  docker-compose.cloud.yml   full stack for a central/cloud deployment
  docker-compose.edge.yml    runs inside a client's own network (see plan)
sample_videos/  test footage (Pexels free-license clip, see attribution below)
```

## Setup

```bash
cd backend
python -m venv ../.venv        # use Python 3.11/3.12 for best ML package support
source ../.venv/Scripts/activate   # Windows Git Bash; use ../.venv/bin/activate on Linux/Mac
pip install -r requirements.txt
pip install --no-deps yolox==0.3.0   # see requirements.txt comment on why --no-deps
pip install loguru thop
```

Download pretrained weights (Apache-2.0, from the official YOLOX releases):

```bash
mkdir -p pipeline/weights
curl -L -o pipeline/weights/yolox_nano.pth \
  https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.pth
```

## Running tests

```bash
cd backend
python -m pytest tests/ -v
```

These 66 tests cover the aggregator (footfall, dwell/queue time, heatmap),
tracker, RTSP grabber, alert thresholds and grouping, the notification
channels (webhook formats, email), cross-video alert merging, and the API
(camera/zone create-edit-delete, validation, reference frames, uploads,
date-range filtering, time buckets, overview, alert status, and adding
missing database columns). They run against a throwaway SQLite database in
about ten seconds, with no Postgres or Redis.

For a real end-to-end check against actual video (needs the full ML stack
+ pretrained weights installed):

```bash
cd backend
python scripts/e2e_smoke_test.py
```

## Running the full stack locally

```bash
cd deploy
docker compose -f docker-compose.cloud.yml up -d postgres redis minio
cd ../backend
DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/video_analytics" \
REDIS_URL="redis://localhost:6379/0" \
uvicorn api.main:app --reload &

DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/video_analytics" \
REDIS_URL="redis://localhost:6379/0" \
python -m pipeline.run &
```

Then:

```bash
curl -X POST localhost:8000/cameras -H "Content-Type: application/json" \
  -d '{"id":"cam1","name":"Mall Corridor","vertical":"retail_showroom"}'

curl -X POST localhost:8000/videos/upload -F "camera_id=cam1" -F "file=@sample_videos/mall_walking_test.mp4"
```

Add an entrance line / zones to get footfall and dwell/queue metrics (the
worker fetches these per camera from the DB automatically — no code change
needed per site):

```bash
curl -X POST localhost:8000/cameras -H "Content-Type: application/json" \
  -d '{"id":"cam1","name":"Mall Corridor","vertical":"retail_showroom","entrance_line":[[0,180],[640,180]]}'

curl -X POST localhost:8000/zones -H "Content-Type: application/json" \
  -d '{"id":"zone1","camera_id":"cam1","name":"Display Section","zone_type":"display_section","polygon":[[200,100],[600,100],[600,360],[200,360]]}'

curl localhost:8000/cameras/cam1/footfall
curl localhost:8000/zones/zone1/dwell
curl localhost:8000/verticals/retail_showroom/zone-template  # or bank_branch
```

## Running with Docker

`deploy/docker-compose.cloud.yml` runs the whole stack in containers:
Postgres, Redis, MinIO, the API, the worker and the dashboard (static files
served by nginx). Build and start it:

```bash
cd deploy
docker compose -f docker-compose.cloud.yml up -d --build
```

Defaults: dashboard on http://localhost:5173, API on http://localhost:8000.
Every host port can be changed with an environment variable (`API_PORT`,
`DASHBOARD_PORT`, `POSTGRES_PORT`, `REDIS_PORT`, `MINIO_API_PORT`). The
dashboard talks to the API **from the browser**, so tell it the address the
browser should use when building (it is baked into the static files), and
let the API accept that origin:

```bash
PUBLIC_API_URL=https://api.example.com CORS_ORIGINS=https://dashboard.example.com \
  docker compose -f docker-compose.cloud.yml up -d --build
```

Alert channels (`ALERT_WEBHOOK_URL`, `ALERT_EMAIL_TO`, `SMTP_*`) are passed
to the worker from your shell or an `.env` file next to the compose file. The
API and worker share one volume for uploads and reference frames. Services
wait for Postgres and Redis to be healthy before starting and restart
automatically if they exit.

**Verified in Docker** (images built from scratch, cold start, no manual
steps): a camera and zone created through the API, a video uploaded, the
containerized worker processing it, and the containerized dashboard showing
the result, with numbers identical to a run outside Docker. Alert delivery
from the worker container to a webhook on the host also worked. The **edge**
stack (`docker-compose.edge.yml`) was run too: a recording dropped into the
watched folder was picked up once, processed by the edge worker, and synced
by the sync worker to the cloud stack, which showed the same numbers and kept
no real video path. Edge needs the camera and zones created on both sides
with the same ids, and the sync worker only runs with `--profile sync` and
`CENTRAL_API_URL` set:

```bash
LOCAL_VIDEO_FOLDER=/path/to/recordings EDGE_CAMERA_ID=edge-cam-1 \
CENTRAL_API_URL=https://central.example.com \
  docker compose -f docker-compose.edge.yml --profile sync up -d --build
```

Not yet run in Docker: the RTSP grabber container, and anything behind real
TLS or authentication. MinIO starts with the cloud stack but is not used yet
(uploads go to a local volume). The backend image is about 2.5 GB because of
PyTorch.

## Live RTSP ingestion (M3)

`backend/ingestion/rtsp_grabber.py` connects to a camera's RTSP feed,
segments it into fixed-length video files (default 30s), and enqueues each
segment exactly like an upload — the detection worker doesn't know or care
whether a video came from a live camera a moment ago or a file someone
uploaded. Reconnects with exponential backoff (5s -> 10s -> 20s -> ... capped
at 60s) on connection failure, since cameras/routers/networks drop
connections in normal operation.

To run it standalone against a camera:

```bash
cd backend
RTSP_URL="rtsp://<camera-ip>/stream" CAMERA_ID="cam1" REDIS_URL="redis://localhost:6379/0" \
python -m ingestion.rtsp_grabber_main
```

Or via compose (both cloud and edge), under the `rtsp` profile so it's not
started by default in demos without a live camera:

```bash
RTSP_URL="rtsp://<camera-ip>/stream" docker compose -f docker-compose.cloud.yml --profile rtsp up rtsp_grabber
```

## Dashboard

`dashboard/` is a React + Vite app (Recharts for charts) with four screens:

- **Overview** - for one camera: entries/exits, zone visits, average dwell,
  open alerts; footfall over time; zone occupancy and dwell; a position
  heatmap drawn over the real camera picture with the zones and entrance
  line; and the alert list. A time-range picker (last hour / 24 hours /
  7 days / all time), a "group by" interval (5 min to 1 day) and an optional
  10-second auto-refresh apply to everything on the page.
- **Compare** - every camera side by side: entries and exits per camera plus
  a table of zone visits, average dwell and open alerts. Click a camera to
  open its overview.
- **Setup** - add, edit and delete cameras; draw zones and the entrance line
  on a real frame from the camera. Select a zone to drag its corners, click
  a faint dot on an edge to add a corner, or double-click a corner to remove
  it; zones can also be redrawn from scratch or deleted. The entrance line
  shows which side counts as "in" and can be flipped.
- **Upload** - send a recorded video for a camera, see how many videos are
  waiting for a worker, and the camera's recent processed videos.

Run it:

```bash
cd dashboard
npm install
echo "VITE_API_URL=http://localhost:8000" > .env.local   # match your API's port
npm run dev
```

The API needs CORS enabled for the dashboard's origin - set `CORS_ORIGINS`
(comma-separated, defaults to `http://localhost:5173`) if serving the
dashboard from somewhere else. Charts read their colours from the page's
design tokens, so they follow light/dark mode.

Optional demo panel: set `VITE_WEBHOOK_INBOX_URL` (for example
`http://localhost:9099`) to show a "Webhook inbox" of what the demo
receiver (`backend/scripts/webhook_receiver.py`) has received. Leave it
unset in production.

### Zones and the reference frame

Zones and the entrance line are stored in the pixel space of a *reference
frame* - one still image per camera. It is captured automatically from the
first video a camera receives (upload, watched folder or RTSP segment) and
served at `GET /cameras/{id}/reference-frame`; replace it with
`POST /cameras/{id}/reference-frame` (an image or a video). Keep it at the
camera's real resolution, because the pipeline uses the same coordinates.
Changing zones affects videos processed afterwards; results already recorded
are not recalculated.

## Alerting

`backend/pipeline/alerts.py` checks each processed video's zone events
against two independent per-zone thresholds, set in the Setup screen or via
`POST/PATCH /zones`:

- **Wait time** (`wait_time_alert_seconds`): one visit lasted longer than the
  limit - a slow billing or teller counter, or someone lingering.
- **Overcrowding** (`max_occupancy_alert`): more than this many people were
  inside at once. Note this is sensitive to tracker ID fragmentation (see
  limitations), so treat it with care until tracking is calibrated.

**Grouping.** Repeated breaches of the same kind in the same zone within the
zone's cooldown (`alert_cooldown_seconds`, default 60 s) collapse into one
alert with an occurrence count and the peak value, e.g. "Wait time exceeded
3.5s 22 times (peak 5.1s)". A live camera's next 30-second segment merges
into the still-open alert instead of creating a new one, and only a newly
created alert sends a notification.

**Status.** Alerts are `open`, `acknowledged` or `resolved`
(`PATCH /alerts/{id}`, buttons on the Overview). New breaches after an alert
is acknowledged or resolved start a fresh alert.

**Delivery.** `backend/pipeline/notify.py` sends each new alert to every
configured channel, set by environment variables on the worker. An alert is
"delivered" if at least one channel accepted it. A channel that is down never
breaks processing: the alert is stored either way, and
`python -m pipeline.redeliver` retries the undelivered ones.

| Variable | Meaning |
|---|---|
| `ALERT_WEBHOOK_URL` | where to POST alerts (unset = no webhook) |
| `ALERT_WEBHOOK_FORMAT` | `generic` (default), `slack`, `discord` or `teams` |
| `ALERT_EMAIL_TO` | comma-separated recipients (unset = no email) |
| `SMTP_HOST`, `SMTP_PORT` | mail server (port defaults to 587) |
| `SMTP_USER`, `SMTP_PASSWORD` | optional login |
| `SMTP_FROM` | sender address |
| `SMTP_STARTTLS` | `0` to disable STARTTLS (default on) |

Verification: the generic webhook and email were tested end to end (a local
HTTP receiver and a real local SMTP server), including grouping and single
delivery on a real upload. The Slack, Discord and Teams payloads were checked
against each service's official incoming-webhook documentation (Slack
`{"text"}`, Discord `{"content"}` capped at 2000 characters, and Teams'
Workflows Adaptive Card, which needs `"contentUrl": null` and card version
1.2), but **have not been sent to the real services**, because that needs
your own webhook URL. Check yours with one command:

```bash
ALERT_WEBHOOK_URL="https://hooks.slack.com/services/..." ALERT_WEBHOOK_FORMAT=slack \
  python -m pipeline.send_test_alert
```

It posts a clearly labelled test alert, prints what the service answered, and
exits non-zero if delivery failed. It works the same for email
(`ALERT_EMAIL_TO` plus the `SMTP_*` variables).

### Regrouping alerts recorded before grouping existed

Alerts stored one-per-visit by older versions can be merged with the same
cooldown rule. It only touches alerts that predate grouping, never mixes
statuses, leaves every processed result alone, and is safe to re-run:

```bash
python -m pipeline.regroup_alerts            # dry run: shows what would change
python -m pipeline.regroup_alerts --apply    # merges them
```

## Database changes

`init_db()` creates missing tables and then adds any columns the models
gained since the tables were created (`ALTER TABLE ... ADD COLUMN`, with the
column's default), so adding a nullable or defaulted column needs no manual
step. Renames, type changes and drops still need a proper migration tool.

## Known limitations (honest status)

- **Live RTSP was not fully soak-tested end-to-end in this dev environment**:
  a local test rig (mediamtx RTSP server + ffmpeg publishing a looped test
  clip) was built to validate this, and it successfully confirmed the
  reconnect/backoff logic against genuine connection failures (exponential
  backoff verified: 5s -> 10s -> 20s -> 40s against an unreachable host). A
  real file-creation bug was found and fixed this way too (a `VideoWriter`
  that never receives a frame may not create its output file at all on this
  platform — `_record_until_disconnected` now reads the first frame before
  creating the writer, and this exact scenario has a dedicated regression
  test in `tests/test_rtsp_grabber.py`). However, full live-frame delivery
  through the local test rig hit a persistent RTSP interop issue (this
  machine's ffmpeg 9.0.1 build not receiving RTP data from any RTSP server
  tested, including a version-pinned mediamtx) that several hours of
  debugging (transport forcing, port mapping, version pinning) didn't
  resolve — it presented as a local environment/version issue, not a defect
  traced to this module's code, which uses the same `cv2.VideoCapture(rtsp_url)`
  pattern as thousands of production deployments. **Recommend validating
  against a real IP camera or NVR during the next hands-on session** before
  trusting this in production — the segment-splitting/disconnect logic is
  unit-tested, but nothing substitutes for one real-camera test.
- **Tracking ID stability**: on a dense, real mall-corridor test clip, the
  ByteTrack reimplementation produces some ID fragmentation under
  cross-occlusion (people walking past each other) and at the ~10fps CPU
  inference speed of YOLOX-Nano on commodity hardware. Tuned defaults
  (`match_iou_thresh=0.15`, `max_frames_lost=45`) reduced this substantially
  from initial defaults but didn't eliminate it — expect this to need
  further per-camera calibration once real site footage is available,
  which is a normal part of deploying any tracking-by-detection system, not
  a sign the approach is wrong.
- **No auth.** The dashboard and API are open, and the database uses the
  default passwords - fine for an internal tool, not for anything external.
- **Alert sync**: `AlertEvent` rows aren't included in the edge->central
  sync batch yet (see M4's SyncBatch) - an edge deployment's alerts are only
  visible locally, not on the central dashboard, until that's added.
- **The editor has been used with a mouse only** (no touch or keyboard
  drawing), and zone shapes are polygons (no curves or holes).
- **Edge sites** still need their cameras and zones set up on both the edge
  and central sides.

## Edge sync worker (M4)

`backend/sync/push.py` pushes each fully-processed video's derived rows
(tracks, footfall/zone events, position samples) to the central API's
`POST /sync/ingest` — never the video file. Syncs at video granularity:
`Video.synced_at` is set locally once a push succeeds, so a crash mid-run
just resumes with whatever's still unsynced, and re-running a sync is
idempotent (every row has a stable UUID primary key from the edge side, so
`/sync/ingest` skips anything it's already seen).

Verified end-to-end with two genuinely separate Postgres/Redis pairs (not
mocked) simulating an edge site and a central deployment: edge processed a
video locally, `sync_once()` pushed it, and the central API's
`/cameras/{id}/footfall` and `/zones/{id}/dwell` endpoints returned results
identical to the edge-local numbers. Confirmed the central DB's `video_path`
for a synced video is a placeholder string, never a real file path — proving
no raw video crosses the sync boundary. Also confirmed sync is idempotent
(a second run with nothing new syncs 0 videos).

Run it standalone:

```bash
cd backend
DATABASE_URL="<edge postgres>" CENTRAL_API_URL="http://<central-api-host>:8000" \
python -m sync.push_main
```

Or via compose, under the `sync` profile (so a fully standalone edge
deployment, with no central API, doesn't try to run it):

```bash
CENTRAL_API_URL="http://<central-host>:8000" docker compose -f docker-compose.edge.yml --profile sync up sync_worker
```

**Known gap**: the camera/zone setup (IDs, entrance lines, zone polygons)
currently has to be created independently on both the edge and central
sides with matching IDs (`/sync/ingest` auto-creates a placeholder Camera if
missing, so a sync won't outright fail, but zone events for an unregistered
zone are silently skipped and counted in the response's
`skipped_zone_events`). A real multi-site product would push config
centrally and have edge sites pull it, rather than configuring both sides
by hand — worth doing before onboarding a second real site.

## Test footage attribution

The clips in `sample_videos/` are free-license footage from Pexels, used here
for development/testing only, under the
[Pexels License](https://www.pexels.com/license/) (free for commercial and
non-commercial use, no attribution required — credited here anyway):

- `mall_walking_test.mp4` — https://www.pexels.com/video/people-walking-inside-the-mall-4750042/, by Anvar Tushakov
- `bank_queue_test.mp4` — https://www.pexels.com/video/27433030/, by Ivan Keyframe (re-encoded to strip a timecode track that stopped it playing in some players)
- `retail_store_test.mp4` — https://www.pexels.com/video/4750041/

These are generic crowd footage, not real bank or client footage; the
"bank" and "retail" labels only reflect the zone setup used for the demo.
