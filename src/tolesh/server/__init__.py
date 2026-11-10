"""Shared local API. The desktop QtWebEngine shell, a future Cloudflare-hosted
web app and a future Android client all talk to this same contract."""

from .app import create_app

__all__ = ["create_app"]
