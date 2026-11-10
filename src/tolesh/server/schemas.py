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
    # Files already uploaded through /api/attachments; only the chunks that
    # match the question are put in the prompt.
    attachment_ids: list[str] = Field(default_factory=list, max_length=20)
    # Pages to read before answering, and whether to search the web for the
    # question itself. Both are opt-in per message: silent network access would
    # make a private assistant unpredictable.
    web_urls: list[str] = Field(default_factory=list, max_length=5)
    web_search: bool = False
    project_id: str | None = None


class ConversationCreate(BaseModel):
    title: str = "New conversation"
    project_id: str | None = None


class ConversationRename(BaseModel):
    title: str


class ConversationFlags(BaseModel):
    """Pin, archive or file a conversation. Omitted fields are untouched."""

    pinned: bool | None = None
    archived: bool | None = None
    project_id: str | None = None
    clear_project: bool = False


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    instructions: str = Field(default="", max_length=4000)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    instructions: str | None = Field(default=None, max_length=4000)


class WebFetchRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2000)


class WebSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


class ExecRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=100_000)
    timeout: int = Field(default=10, ge=1, le=120)
    memory_mb: int = Field(default=512, ge=64, le=4096)
    isolate_network: bool = True


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
    # Wire model ids pulled from the endpoint's own /models listing. Sending a
    # list replaces the provider's catalogue with exactly those ids.
    models: list[str] | None = Field(default=None, max_length=200)


class ConfigPatch(BaseModel):
    """Application-level routing preference."""

    default_model: str | None = Field(default=None, max_length=200)


class ModelPatch(BaseModel):
    """Switch one model on or off without touching its provider."""

    enabled: bool


class FallbackOrder(BaseModel):
    """The user's own chain for the conversational tasks.

    An empty list restores the built-in order, which is why it is accepted
    rather than rejected: "reset" has to be expressible.
    """

    models: list[str] = Field(default_factory=list, max_length=200)


class MCPServerPatch(BaseModel):
    """Create or partially update one MCP server.

    ``env`` and ``headers`` accept masked values (``sk-***xyz``) that mean
    "keep what is stored", so the UI can save a form without ever holding the
    real secret.
    """

    name: str | None = Field(default=None, max_length=40)
    transport: Literal["stdio", "http"] | None = None
    command: str | None = Field(default=None, max_length=500)
    args: list[str] | None = Field(default=None, max_length=100)
    env: dict[str, str] | None = None
    url: str | None = Field(default=None, max_length=2000)
    headers: dict[str, str] | None = None
    enabled: bool | None = None
    timeout_sec: float | None = Field(default=None, ge=1, le=300)
    default_policy: Literal["allow", "ask", "deny"] | None = None
    tool_permissions: dict[str, str] | None = None
    notes: str | None = Field(default=None, max_length=1000)


class MCPInvoke(BaseModel):
    server: str = Field(..., min_length=1, max_length=40)
    tool: str = Field(..., min_length=1, max_length=200)
    args: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = None
    approval_token: str | None = None
    reason: str = Field(default="", max_length=500)


class MCPPromptRun(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ConnectorTokenCreate(BaseModel):
    """Connect an account with a personal access token."""

    provider: str = Field(..., min_length=1, max_length=40)
    token: str = Field(..., min_length=1, max_length=4000)
    label: str = Field(default="", max_length=80)
    base_url: str = Field(default="", max_length=500)


class ConnectorOAuthStart(BaseModel):
    provider: str = Field(..., min_length=1, max_length=40)
    client_id: str = Field(default="", max_length=300)
    client_secret: str = Field(default="", max_length=300)
    redirect_uri: str = Field(..., min_length=1, max_length=500)
    scopes: list[str] = Field(default_factory=list, max_length=20)
    base_url: str = Field(default="", max_length=500)


class ConnectorOAuthComplete(BaseModel):
    state: str = Field(..., min_length=1, max_length=200)
    code: str = Field(..., min_length=1, max_length=4000)
    base_url: str = Field(default="", max_length=500)


class ConnectorActionRun(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = None
    approval_token: str | None = None
