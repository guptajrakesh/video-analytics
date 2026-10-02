"""Send one clearly-labelled test alert through the configured channels and
report what each service answered. Use it to check a real Slack / Teams /
Discord webhook URL or SMTP settings before relying on them:

    ALERT_WEBHOOK_URL=https://hooks.slack.com/services/... ALERT_WEBHOOK_FORMAT=slack \\
        python -m pipeline.send_test_alert

    ALERT_EMAIL_TO=ops@example.com SMTP_HOST=smtp.example.com SMTP_USER=... SMTP_PASSWORD=... \\
        python -m pipeline.send_test_alert

Settings are the same environment variables the worker uses (see
pipeline/notify.py). Exit code is 0 only if every configured channel accepted
the message, and 2 if none is configured.
"""

from __future__ import annotations

import os
import sys

import requests

from pipeline.alerts import Alert
from pipeline.notify import build_webhook_payload, send_email

TEST_ALERT = Alert(
    zone_id="test-zone",
    alert_type="queue_wait_exceeded",
    message="This is a test alert from Video Analytics - if you can read it, delivery works.",
    value=12.0,
    threshold=10.0,
    occurred_at=0.0,
    occurrences=1,
)


def main() -> int:
    results: list[bool] = []

    url = os.environ.get("ALERT_WEBHOOK_URL", "")
    if url:
        fmt = os.environ.get("ALERT_WEBHOOK_FORMAT", "generic").lower()
        try:
            response = requests.post(url, json=build_webhook_payload(fmt, "test-camera", TEST_ALERT), timeout=10)
            ok = response.ok
            print(f"webhook ({fmt}): HTTP {response.status_code} {response.text[:200]!r}")
        except requests.RequestException as e:
            ok = False
            print(f"webhook ({fmt}): failed - {e}")
        results.append(ok)

    email = send_email("test-camera", TEST_ALERT)
    if email is not None:
        print(f"email: {'sent' if email else 'failed (see the log above)'}")
        results.append(email)

    if not results:
        print("nothing configured: set ALERT_WEBHOOK_URL and/or ALERT_EMAIL_TO + SMTP_HOST")
        return 2
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
