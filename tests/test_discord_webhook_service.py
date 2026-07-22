import pytest

from app.services.discord_webhook_service import DiscordWebhookError, DiscordWebhookService


def test_webhook_url_validation_adds_wait() -> None:
    host, port, path = DiscordWebhookService._validated_url(
        "https://discord.com/api/webhooks/123/token?thread_id=456",
        wait=True,
    )
    assert host == "discord.com"
    assert port == 443
    assert "thread_id=456" in path
    assert "wait=true" in path


def test_webhook_url_validation_rejects_non_discord_host() -> None:
    with pytest.raises(DiscordWebhookError):
        DiscordWebhookService._validated_url(
            "https://example.com/api/webhooks/123/token",
            wait=True,
        )
