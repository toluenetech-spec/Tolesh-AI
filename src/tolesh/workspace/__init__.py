"""Workspace stores: projects and uploaded files.

Both are additive modules — nothing here imports the chat interface, so a
future client (Android, CLI) can use them without the web UI.
"""

from .attachments import AttachmentStore, chunk_text, extract_text
from .projects import ProjectStore

__all__ = ["AttachmentStore", "ProjectStore", "chunk_text", "extract_text"]
