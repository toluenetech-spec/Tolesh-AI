"""Workspace stores: projects, uploaded files, assistants, templates, shares.

All additive modules — nothing here imports the chat interface, so a future
client (Android, CLI) can use them without the web UI.
"""

from .assistants import AssistantStore
from .attachments import AttachmentStore, chunk_text, extract_text
from .projects import ProjectStore
from .shares import ShareStore, render_error, render_page

__all__ = [
    "AssistantStore", "AttachmentStore", "ProjectStore", "ShareStore",
    "chunk_text", "extract_text", "render_error", "render_page",
]
