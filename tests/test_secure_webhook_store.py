from pathlib import Path

from app.services.secure_webhook_store import DiscordWebhookStore


def test_webhook_store_round_trip(tmp_path: Path) -> None:
    store = DiscordWebhookStore(tmp_path / "discord_webhook.dat")
    url = "https://discord.com/api/webhooks/123/token"
    store.save(url)
    assert store.configured
    assert store.load() == url
    store.clear()
    assert not store.configured
    assert store.load() is None
