import pytest

from pipeline import notify
from pipeline.alerts import Alert

ALERT = Alert("zone-1", "queue_wait_exceeded", "Wait time 9.0s exceeded threshold 5.0s", 9.0, 5.0, 0.0)
ENV = ["ALERT_WEBHOOK_URL", "ALERT_WEBHOOK_FORMAT", "ALERT_EMAIL_TO", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "SMTP_STARTTLS"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


class Resp:
    def __init__(self, ok=True):
        self.ok = ok

    def raise_for_status(self):
        if not self.ok:
            raise notify.requests.HTTPError("500")


def test_payload_formats():
    assert notify.build_webhook_payload("slack", "cam1", ALERT) == {"text": notify.alert_text("cam1", ALERT)}
    assert notify.build_webhook_payload("discord", "cam1", ALERT) == {"content": notify.alert_text("cam1", ALERT)}

    generic = notify.build_webhook_payload("generic", "cam1", ALERT)
    assert generic["camera_id"] == "cam1" and generic["value"] == 9.0 and generic["text"]

    teams = notify.build_webhook_payload("teams", "cam1", ALERT)
    card = teams["attachments"][0]
    assert card["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert "contentUrl" in card and card["contentUrl"] is None
    assert card["content"]["version"] == "1.2" and card["content"]["type"] == "AdaptiveCard"
    assert "cam1 / zone-1" in card["content"]["body"][1]["text"]


def test_nothing_configured_means_not_delivered():
    assert notify.deliver_alert("cam1", ALERT) is False


def test_webhook_success_and_failure(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://hook.test/x")
    monkeypatch.setenv("ALERT_WEBHOOK_FORMAT", "slack")
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(url=url, json=json)
        return Resp(True)

    monkeypatch.setattr(notify.requests, "post", fake_post)
    assert notify.deliver_alert("cam1", ALERT) is True
    assert sent["url"] == "http://hook.test/x" and "text" in sent["json"]

    monkeypatch.setattr(notify.requests, "post", lambda url, json, timeout: Resp(False))
    assert notify.deliver_alert("cam1", ALERT) is False

    def boom(url, json, timeout):
        raise notify.requests.ConnectionError("down")

    monkeypatch.setattr(notify.requests, "post", boom)
    assert notify.deliver_alert("cam1", ALERT) is False  # never raises


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host, self.port = host, port
        self.started_tls = False
        self.logged_in = None
        self.messages = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message):
        self.messages.append(message)


def test_email_delivery(monkeypatch):
    FakeSMTP.instances.clear()
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@example.com, manager@example.com")
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_USER", "alerts@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")

    assert notify.deliver_alert("cam1", ALERT) is True
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port, smtp.started_tls, smtp.logged_in) == ("smtp.test", 587, True, ("alerts@example.com", "secret"))
    msg = smtp.messages[0]
    assert msg["To"] == "ops@example.com, manager@example.com"
    assert "Wait time alert" in msg["Subject"] and "cam1" in msg["Subject"]
    assert "Wait time 9.0s" in msg.get_content()


def test_email_needs_host_and_recipients_and_failure_does_not_raise(monkeypatch):
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@example.com")
    assert notify.send_email("cam1", ALERT) is None  # no SMTP_HOST

    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_STARTTLS", "0")

    def refuse(*a, **k):
        raise ConnectionRefusedError()

    monkeypatch.setattr(notify.smtplib, "SMTP", refuse)
    assert notify.deliver_alert("cam1", ALERT) is False


def test_either_channel_counts_as_delivered(monkeypatch):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://hook.test/x")
    monkeypatch.setattr(notify.requests, "post", lambda url, json, timeout: Resp(False))
    monkeypatch.setenv("ALERT_EMAIL_TO", "ops@example.com")
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    assert notify.deliver_alert("cam1", ALERT) is True


def test_discord_content_is_capped_at_2000_characters():
    long_alert = Alert("z", "queue_wait_exceeded", "x" * 5000, 1.0, 1.0, 0.0)
    assert len(notify.build_webhook_payload("discord", "cam1", long_alert)["content"]) == 2000
