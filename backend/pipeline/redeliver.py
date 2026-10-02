"""Retry webhook delivery for alerts that were stored but never delivered
(e.g. the webhook was down, or ALERT_WEBHOOK_URL was unset when the video was
processed). An alert is marked delivered only after the endpoint accepts it.

Usage: ALERT_WEBHOOK_URL=http://... python -m pipeline.redeliver
(or the email settings described in pipeline/notify.py)
"""

from __future__ import annotations

import logging

from models.db import get_session
from models.schema import AlertEvent
from pipeline.alerts import Alert
from pipeline.notify import deliver_alert

logger = logging.getLogger(__name__)


def redeliver_pending() -> tuple[int, int]:
    """Returns (delivered, still_pending)."""
    delivered = 0
    still_pending = 0
    with get_session() as session:
        pending = session.query(AlertEvent).filter(AlertEvent.delivered.is_(False)).all()
        for row in pending:
            alert = Alert(
                zone_id=row.zone_id,
                alert_type=row.alert_type,
                message=row.message,
                value=row.value,
                threshold=row.threshold,
                occurred_at=0.0,
                occurrences=row.occurrences or 1,
            )
            if deliver_alert(row.camera_id, alert):
                row.delivered = True
                delivered += 1
            else:
                still_pending += 1
    return delivered, still_pending


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ok, pending = redeliver_pending()
    print(f"delivered {ok}, still pending {pending}")
