"""Email alerts for the deployment (PLAN.md §10): a failed cycle or backup, a filling disk.

The host's systemd units pipe the failed unit's recent log lines into `alert send`, which
emails them through Resend's HTTP API.
"""

import logging

import httpx2

from app.settings import Settings

logger = logging.getLogger(__name__)


class AlertError(Exception):
    """An alert couldn't be sent."""


async def send_alert(
    settings: Settings,
    subject: str,
    body: str,
    *,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> bool:
    """Email `subject` and `body` to ALERT_EMAIL_TO; False if no one is set to get alerts."""
    if not settings.alert_email_to:
        logger.warning("ALERT_EMAIL_TO is empty; not sending alert %r", subject)
        return False
    key = settings.resend_api_key
    if key is None or not key.get_secret_value():
        raise AlertError("RESEND_API_KEY is not set")
    if len(body) > settings.alert_max_body_chars:
        body = "…" + body[-settings.alert_max_body_chars :]
    async with httpx2.AsyncClient(
        base_url=settings.resend_base_url,
        headers={"Authorization": f"Bearer {key.get_secret_value()}"},
        timeout=settings.provider_timeout_s,
        transport=transport,
    ) as client:
        try:
            response = await client.post(
                "/emails",
                json={
                    "from": settings.alert_email_from,
                    "to": settings.alert_email_to,
                    "subject": f"[betterweb] {subject}",
                    "text": body or "(no log lines)",
                },
            )
        except httpx2.RequestError as error:
            raise AlertError(f"sending the alert failed: {error!r}") from error
    if not response.is_success:
        raise AlertError(f"Resend answered {response.status_code}: {response.text[:500]}")
    return True
