import json

import httpx2
import pytest
from pydantic import SecretStr

from app.alerts import AlertError, send_alert
from app.settings import Settings

pytestmark = pytest.mark.anyio

SETTINGS = Settings(
    _env_file=None,
    alert_email_to=["me@example.com"],
    resend_api_key=SecretStr("re_test"),
    alert_max_body_chars=10,
)


class Resend:
    def __init__(self, status: int = 200) -> None:
        self.requests: list[httpx2.Request] = []
        self._status = status

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self._status, json={"id": "email-1"})

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)


async def test_an_alert_is_emailed_with_the_end_of_the_log() -> None:
    resend = Resend()
    sent = await send_alert(SETTINGS, "cycle failed", "0123456789END", transport=resend.transport())
    assert sent
    [request] = resend.requests
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["authorization"] == "Bearer re_test"
    assert json.loads(request.content) == {
        "from": SETTINGS.alert_email_from,
        "to": ["me@example.com"],
        "subject": "[betterweb] cycle failed",
        "text": "…3456789END",
    }


async def test_no_recipients_means_no_alert() -> None:
    resend = Resend()
    settings = Settings(_env_file=None, resend_api_key=SecretStr("re_test"))
    assert not await send_alert(settings, "x", "y", transport=resend.transport())
    assert resend.requests == []


async def test_a_missing_key_is_an_error() -> None:
    settings = Settings(_env_file=None, alert_email_to=["me@example.com"])
    with pytest.raises(AlertError, match="RESEND_API_KEY"):
        await send_alert(settings, "x", "y", transport=Resend().transport())


async def test_a_refused_alert_is_an_error() -> None:
    with pytest.raises(AlertError, match="422"):
        await send_alert(SETTINGS, "x", "y", transport=Resend(422).transport())
