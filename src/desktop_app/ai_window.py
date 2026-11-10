"""The integrated Tolesh AI surface for the desktop application.

This window is the desktop face of the shared backend. It loads the same
static frontend that a browser would get from ``http://127.0.0.1:<port>/``,
which means the chat, streaming replies, conversation list, memory, provider
settings and approval prompts are all talking to the *real* FastAPI backend
started by :class:`tolesh.server.embedded.EmbeddedServer` — not to a second
application and not to scripted data.

It follows the same rule as the inherited ``MemoryViewerWindow``: use
QtWebEngine when the platform has it, otherwise hand the same URL to the
system browser. That keeps Linux systems without ``PyQt6-WebEngine`` usable
instead of crashing on import.
"""

from __future__ import annotations

import webbrowser
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget,
)

try:  # optional dependency, exactly like the inherited memory viewer
    from PyQt6.QtWebEngineWidgets import QWebEngineView

    HAS_WEBENGINE = True
except ImportError:  # pragma: no cover - depends on the installed wheels
    HAS_WEBENGINE = False
    QWebEngineView = None  # type: ignore[assignment]

from desktop_app.themes import TOLESH_THEME_STYLESHEET


_ERROR_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; height:100vh; display:flex; align-items:center;
         justify-content:center; background:#14100c; color:#f3ece1;
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
  .box {{ max-width: 460px; padding: 36px; text-align: center; }}
  h1 {{ font-size: 20px; font-weight: 600; color:#d8a75b; margin: 0 0 14px; }}
  p {{ color:#a89c8c; line-height: 1.6; margin: 0 0 10px; }}
  code {{ background:#221b14; padding:2px 6px; border-radius:4px; color:#e7d6b8; }}
</style></head><body><div class="box">
  <h1>Tolesh could not reach its engine</h1>
  <p>{message}</p>
  <p>The voice assistant is still running. Reopen this window from the tray
     menu once the engine is available.</p>
</div></body></html>"""


class ToleshAIWindow(QMainWindow):
    """Embedded browser for the Tolesh AI frontend."""

    def __init__(self, url: str, *, on_retry: Optional[Callable[[], str]] = None):
        super().__init__()
        self.setWindowTitle("🤖 Tolesh")
        self.setGeometry(120, 90, 1280, 880)
        self.setStyleSheet(TOLESH_THEME_STYLESHEET)

        self._url = url
        self._on_retry = on_retry
        self.web_view = None

        central = QWidget()
        self.setCentralWidget(central)
        self._layout = QVBoxLayout(central)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        if HAS_WEBENGINE:
            try:
                self.web_view = QWebEngineView()
                self._layout.addWidget(self.web_view)
            except Exception:  # pragma: no cover - broken Qt install
                self.web_view = None

        if self.web_view is None:
            self._build_fallback(url)

    # ------------------------------------------------------------- building
    @classmethod
    def embedded_available(cls) -> bool:
        """True when the window can render the UI in-process."""
        return HAS_WEBENGINE

    def _build_fallback(self, url: str) -> None:
        """No QtWebEngine: show the address and open it in the real browser."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(14)

        title = QLabel("Tolesh is running")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)

        note = QLabel(
            "This build has no embedded browser component (PyQt6-WebEngine is "
            "not installed), so the Tolesh interface opens in your default "
            "browser. Everything else — chat, memory, providers — is served by "
            "the engine running inside this application."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #a89c8c;")
        layout.addWidget(note)

        self._address = QLabel(url)
        self._address.setWordWrap(True)
        self._address.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._address.setStyleSheet(
            "background:#221b14; border-radius:6px; padding:10px; color:#e7d6b8;"
        )
        layout.addWidget(self._address)

        row = QHBoxLayout()
        open_btn = QPushButton("Open in browser")
        open_btn.clicked.connect(self.open_in_browser)
        row.addWidget(open_btn)

        retry_btn = QPushButton("Reconnect")
        retry_btn.clicked.connect(self.retry)
        row.addWidget(retry_btn)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)

        self._layout.addWidget(panel)

    # ------------------------------------------------------------ behaviour
    def load(self) -> None:
        """Point the embedded view (or fallback address) at the backend."""
        if self.web_view is not None:
            self.web_view.setUrl(QUrl(self._url))
        else:
            self._address.setText(self._url)

    def set_url(self, url: str) -> None:
        self._url = url
        self.load()

    def open_in_browser(self) -> bool:
        return webbrowser.open(self._url)

    def retry(self) -> None:
        if self._on_retry is not None:
            try:
                self._url = self._on_retry() or self._url
            except Exception as exc:
                self.show_error(str(exc))
                return
        self.load()

    def show_error(self, message: str) -> None:
        """Render a readable failure instead of a blank white page."""
        safe = (message or "Unknown error").replace("<", "&lt;").replace(">", "&gt;")
        if self.web_view is not None:
            self.web_view.setHtml(_ERROR_TEMPLATE.format(message=safe))
        else:
            self._address.setText(f"Engine unavailable: {message}")

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        if self.web_view is not None and self.web_view.url().isEmpty():
            self.load()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # Hiding keeps the page (and its conversation state) alive for the
        # session, matching the memory viewer's lifecycle. The backend is
        # stopped by the tray's cleanup_on_exit, never here.
        event.accept()
