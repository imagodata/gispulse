"""Outbound HTTP webhook adapters for ActionDispatcher.

Public entry point :class:`HttpWebhookClient`. Inject its bound
:meth:`HttpWebhookClient.post` into :class:`ActionDispatcher` as
``webhook_client`` to enable ``ActionType.WEBHOOK`` dispatch.
"""

from gispulse.adapters.webhooks.http_client import (
    HttpWebhookClient,
    WebhookDeliveryError,
    WebhookSecurityError,
)

__all__ = [
    "HttpWebhookClient",
    "WebhookDeliveryError",
    "WebhookSecurityError",
]
