"""Request/response schemas for the shared Tolesh API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=32000)
    conversation_id: str | None = None
    model: str | None = None
    task: str = "chat"
    temperature: float = 0.7
    max_tokens: int = 1024


class ConversationCreate(BaseModel):
    title: str = "New conversation"


class ConversationRename(BaseModel):
    title: str


class MemoryCreate(BaseModel):
    category: Literal["conversation", "preference", "personal", "project", "task"]
    content: str = Field(..., min_length=1, max_length=8000)
    source: str = "user"
    pinned: bool = False


class MemoryForget(BaseModel):
    confirm: str


class PermissionDecision(BaseModel):
    token: str
    execute: bool = True


class ToolInvoke(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class ProviderPatch(BaseModel):
    """Partial update for one provider.

    ``api_key`` accepts an empty string to clear a stored key. Omitted fields
    are left untouched, so the UI can save a base URL without resending the
    secret it never received.
    """

    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None
    timeout: float | None = Field(default=None, ge=1, le=600)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    notes: str | None = Field(default=None, max_length=1000)


class ConfigPatch(BaseModel):
    """Application-level routing preference."""

    default_model: str | None = Field(default=None, max_length=200)
