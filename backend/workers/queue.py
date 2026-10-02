"""Thin Redis-backed queue for FrameJobs.

Deliberately not using RQ's decorator-style API here: we want an explicit,
serializable job shape (FrameJob) that's identical whether it was enqueued by
the cloud API's upload endpoint or the edge mode's local-folder watcher.
"""

from __future__ import annotations

import dataclasses
import json

import redis

from ingestion.common import FrameJob, SourceType

QUEUE_KEY = "frame_jobs"


def get_redis(url: str) -> redis.Redis:
    # socket_timeout must exceed the largest BLPOP `timeout` ever passed to
    # dequeue() — BLPOP blocks server-side for up to that long with no data
    # sent, and the client's socket read raises a raw TimeoutError (crashing
    # the worker) if the socket gives up first. None here means "wait
    # forever on reads", which is correct since BLPOP's own timeout is what
    # actually bounds the wait.
    return redis.Redis.from_url(url, decode_responses=True, socket_timeout=None)


def enqueue(r: redis.Redis, job: FrameJob) -> None:
    r.rpush(QUEUE_KEY, json.dumps(dataclasses.asdict(job)))


def dequeue(r: redis.Redis, timeout: int = 5) -> FrameJob | None:
    result = r.blpop([QUEUE_KEY], timeout=timeout)
    if result is None:
        return None
    _, raw = result
    data = json.loads(raw)
    data["source_type"] = SourceType(data["source_type"])
    return FrameJob(**data)
