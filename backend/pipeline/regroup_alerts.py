"""One-off migration: group alerts that were recorded one-per-visit, before
alert grouping existed, using the same cooldown rule as live processing.

Only legacy alerts are touched (those with no `last_occurred_at`), and only
alerts with the same zone, type and status are merged. Per group the earliest
alert is kept, given the occurrence count, peak value and last occurrence
time, and the rest are removed; no processed results (tracks, events,
heatmap data) are changed. Safe to re-run: merged alerts are not legacy any
more, so a second run finds nothing to do.

Usage (dry run by default):
    python -m pipeline.regroup_alerts            # show what would change
    python -m pipeline.regroup_alerts --apply    # do it
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from models.db import get_session, init_db
from models.schema import AlertEvent, Zone
from pipeline.alerts import grouped_message


@dataclass
class Group:
    keep: AlertEvent
    members: list[AlertEvent] = field(default_factory=list)


def plan_groups(session) -> list[Group]:
    cooldowns = {z.id: (z.alert_cooldown_seconds if z.alert_cooldown_seconds is not None else 60.0) for z in session.query(Zone).all()}
    legacy = (
        session.query(AlertEvent)
        .filter(AlertEvent.last_occurred_at.is_(None))
        .order_by(AlertEvent.zone_id, AlertEvent.alert_type, AlertEvent.status, AlertEvent.occurred_at)
        .all()
    )

    groups: list[Group] = []
    current: Group | None = None
    for row in legacy:
        joins = (
            current is not None
            and (current.keep.zone_id, current.keep.alert_type, current.keep.status) == (row.zone_id, row.alert_type, row.status)
            and (row.occurred_at - current.members[-1].occurred_at).total_seconds() <= cooldowns.get(row.zone_id, 60.0)
        )
        if joins:
            current.members.append(row)
        else:
            current = Group(keep=row, members=[row])
            groups.append(current)
    return groups


def apply_groups(session, groups: list[Group]) -> tuple[int, int]:
    """Returns (alerts_before, alerts_after) for the groups handled."""
    before = sum(len(g.members) for g in groups)
    for g in groups:
        keep = g.keep
        keep.occurrences = len(g.members)
        keep.value = max(m.value for m in g.members)
        keep.last_occurred_at = max(m.occurred_at for m in g.members)
        keep.delivered = any(m.delivered for m in g.members)
        if len(g.members) > 1:
            keep.message = grouped_message(keep.alert_type, keep.threshold, keep.occurrences, keep.value)
        for m in g.members[1:]:
            session.delete(m)
    return before, len(groups)


def main(apply: bool) -> None:
    init_db()
    with get_session() as session:
        groups = plan_groups(session)
        if not groups:
            print("nothing to do: no ungrouped legacy alerts")
            return
        for g in groups:
            print(f"{g.keep.zone_id} / {g.keep.alert_type} / {g.keep.status}: {len(g.members)} alert(s) -> 1")
        total_before = sum(len(g.members) for g in groups)
        print(f"\n{total_before} alerts would become {len(groups)}")
        if not apply:
            session.rollback()
            print("dry run only - re-run with --apply to make the change")
            return
        apply_groups(session, groups)
        print("applied")


if __name__ == "__main__":
    main(apply="--apply" in sys.argv)
