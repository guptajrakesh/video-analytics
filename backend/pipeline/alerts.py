"""Threshold-breach detection over a zone's enter/exit event stream.

Pure Python, zero DB/network dependency — consumes the same ZoneEvent
objects pipeline/aggregator.py's ZoneTracker already produces, so this is
testable with the exact synthetic sequences used in test_aggregator.py.
Delivery (DB write + webhook) is a separate concern, handled by the worker.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.aggregator import ZoneEvent


@dataclass
class Alert:
    zone_id: str
    alert_type: str  # "queue_wait_exceeded" | "occupancy_exceeded"
    message: str
    value: float
    threshold: float
    occurred_at: float
    occurrences: int = 1
    last_occurred_at: float | None = None  # set by group_alerts


def check_wait_time_alerts(zone_id: str, zone_events: list[ZoneEvent], threshold_seconds: float) -> list[Alert]:
    """Flags any single visit whose dwell time exceeded the threshold — the
    queue/wait-time case (e.g. a billing counter or teller counter)."""
    alerts = []
    for event in zone_events:
        if event.event_type == "exit" and event.dwell_seconds is not None and event.dwell_seconds > threshold_seconds:
            alerts.append(
                Alert(
                    zone_id=zone_id,
                    alert_type="queue_wait_exceeded",
                    message=f"Wait time {event.dwell_seconds:.1f}s exceeded threshold {threshold_seconds:.1f}s",
                    value=event.dwell_seconds,
                    threshold=threshold_seconds,
                    occurred_at=event.timestamp,
                )
            )
    return alerts


def check_occupancy_alerts(zone_id: str, zone_events: list[ZoneEvent], threshold_count: int) -> list[Alert]:
    """Flags moments where concurrent occupancy exceeded the threshold — the
    crowding/fire-safety case, independent of any single visit's length.
    Fires once per crossing above the threshold (not once per frame still
    above it), so a zone that stays crowded for a while produces one alert
    per distinct entry that pushed it over, not a flood of duplicates."""
    alerts = []
    for event in sorted(zone_events, key=lambda e: e.timestamp):
        concurrent = _running_concurrency(zone_events, event.timestamp)
        if event.event_type == "enter" and concurrent > threshold_count:
            alerts.append(
                Alert(
                    zone_id=zone_id,
                    alert_type="occupancy_exceeded",
                    message=f"Occupancy {concurrent} exceeded threshold {threshold_count}",
                    value=concurrent,
                    threshold=threshold_count,
                    occurred_at=event.timestamp,
                )
            )
    return alerts


def _running_concurrency(zone_events: list[ZoneEvent], at_time: float) -> int:
    """Number of tracks that had entered by `at_time` and not yet exited."""
    concurrent = 0
    for event in zone_events:
        if event.timestamp > at_time:
            continue
        if event.event_type == "enter":
            concurrent += 1
        elif event.event_type == "exit":
            concurrent -= 1
    return concurrent


def group_alerts(alerts: list[Alert], cooldown_seconds: float) -> list[Alert]:
    """Collapse repeated breaches of the same kind in the same zone into one
    alert. A breach joins the open group if it happens within
    `cooldown_seconds` of that group's latest breach (so a long stretch of
    trouble is one alert, not one per visit). The group keeps the first
    occurrence time, counts occurrences, and reports the peak value."""
    groups: list[Alert] = []
    open_group: dict[tuple[str, str], Alert] = {}

    for alert in sorted(alerts, key=lambda a: a.occurred_at):
        key = (alert.zone_id, alert.alert_type)
        group = open_group.get(key)
        if group is not None and alert.occurred_at - group.last_occurred_at <= cooldown_seconds:
            group.occurrences += 1
            group.last_occurred_at = alert.occurred_at
            group.value = max(group.value, alert.value)
            group.message = _group_message(group)
            continue

        new_group = Alert(
            zone_id=alert.zone_id,
            alert_type=alert.alert_type,
            message=alert.message,
            value=alert.value,
            threshold=alert.threshold,
            occurred_at=alert.occurred_at,
            occurrences=1,
            last_occurred_at=alert.occurred_at,
        )
        open_group[key] = new_group
        groups.append(new_group)
    return groups


def grouped_message(alert_type: str, threshold: float, occurrences: int, peak: float) -> str:
    if alert_type == "queue_wait_exceeded":
        return f"Wait time exceeded {threshold:.1f}s {occurrences} times (peak {peak:.1f}s)"
    return f"Occupancy exceeded {threshold:g} {occurrences} times (peak {peak:g})"


def _group_message(group: Alert) -> str:
    return grouped_message(group.alert_type, group.threshold, group.occurrences, group.value)
