"""Additional ways to reach Tolesh besides the local web UI.

Only Telegram for now: polling, because a laptop behind NAT cannot accept a
webhook, and an allowlist, because this is the one surface reachable from the
internet.
"""

from .telegram import (BOT_CONNECTION_ID, FakeTransport, HttpTransport,
                       TelegramChannel, TelegramError, TelegramLinks,
                       TelegramService, TelegramSettings, Transport,
                       looks_like_token)

__all__ = ["BOT_CONNECTION_ID", "FakeTransport", "HttpTransport",
           "TelegramChannel", "TelegramError", "TelegramLinks",
           "TelegramService", "TelegramSettings", "Transport",
           "looks_like_token"]
