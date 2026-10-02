"""Alert notification channels: a webhook (generic / Slack / Discord / Teams)
and email over SMTP. Configuration is read from the environment at call time:

  ALERT_WEBHOOK_URL        where to POST alerts (unset = no webhook)
  ALERT_WEBHOOK_FORMAT     generic (default) | slack | discord | teams
  ALERT_EMAIL_TO           comma-separated recipients (unset = no email)
  SMTP_HOST, SMTP_PORT     mail server (port default 587)
  SMTP_USER, SMTP_PASSWORD optional login
  SMTP_FROM                sender address (default: SMTP_USER or alerts@localhost)
  SMTP_STARTTLS            "0" to disable STARTTLS (default on; use "0" for a local test server)

An alert counts as delivered if at least one configured channel accepted it.
A channel that is down never raises: delivery failure must not break video
processing, and the alert is stored either way.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

import requests

from pipeline.alerts import Alert

logger = logging.getLogger(__name__)

ALERT_LABELS = {
    "queue_wait_exceeded": "Wait time alert",
    "occupancy_exceeded": "Overcrowding alert",
}


def alert_text(camera_id: str, alert: Alert) -> str:
    return f"{ALERT_LABELS.get(alert.alert_type, alert.alert_type)} on {camera_id} / {alert.zone_id}: {alert.message}"


def build_webhook_payload(fmt: str, camera_id: str, alert: Alert) -> dict:
    text = alert_text(camera_id, alert)
    if fmt == "slack":
        return {"text": text}
    if fmt == "discord":
        return {"content": text[:2000]}  # Discord rejects content over 2000 characters
    if fmt == "teams":
        # Power Automate "Workflows" webhook: expects an Adaptive Card attachment.
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,  # required by the Workflows webhook schema
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.2",  # the card version Teams webhooks accept
                        "body": [
                            {"type": "TextBlock", "weight": "Bolder", "text": ALERT_LABELS.get(alert.alert_type, alert.alert_type)},
                            {"type": "TextBlock", "wrap": True, "text": f"{camera_id} / {alert.zone_id}: {alert.message}"},
                        ],
                    },
                }
            ],
        }
    return {
        "text": text,
        "camera_id": camera_id,
        "zone_id": alert.zone_id,
        "alert_type": alert.alert_type,
        "message": alert.message,
        "value": alert.value,
        "threshold": alert.threshold,
        "occurrences": alert.occurrences,
    }


def send_webhook(camera_id: str, alert: Alert) -> bool | None:
    """None = no webhook configured; True/False = delivery outcome."""
    url = os.environ.get("ALERT_WEBHOOK_URL", "")
    if not url:
        return None
    payload = build_webhook_payload(os.environ.get("ALERT_WEBHOOK_FORMAT", "generic").lower(), camera_id, alert)
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except requests.RequestException:
        logger.warning("alert webhook delivery failed for %s/%s", camera_id, alert.zone_id, exc_info=True)
        return False


def send_email(camera_id: str, alert: Alert) -> bool | None:
    """None = email not configured; True/False = delivery outcome."""
    recipients = [r.strip() for r in os.environ.get("ALERT_EMAIL_TO", "").split(",") if r.strip()]
    host = os.environ.get("SMTP_HOST", "")
    if not recipients or not host:
        return None

    user = os.environ.get("SMTP_USER", "")
    message = EmailMessage()
    message["Subject"] = f"[Video Analytics] {ALERT_LABELS.get(alert.alert_type, alert.alert_type)}: {camera_id} / {alert.zone_id}"
    message["From"] = os.environ.get("SMTP_FROM") or user or "alerts@localhost"
    message["To"] = ", ".join(recipients)
    message.set_content(
        f"{alert.message}\n\nCamera: {camera_id}\nZone: {alert.zone_id}\n"
        f"Peak value: {alert.value:g} (threshold {alert.threshold:g})\nOccurrences: {alert.occurrences}\n"
    )
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=10) as smtp:
            if os.environ.get("SMTP_STARTTLS", "1") != "0":
                smtp.starttls()
            if user:
                smtp.login(user, os.environ.get("SMTP_PASSWORD", ""))
            smtp.send_message(message)
        return True
    except (smtplib.SMTPException, OSError):
        logger.warning("alert email delivery failed for %s/%s", camera_id, alert.zone_id, exc_info=True)
        return False


def deliver_alert(camera_id: str, alert: Alert) -> bool:
    results = [r for r in (send_webhook(camera_id, alert), send_email(camera_id, alert)) if r is not None]
    return any(results)
