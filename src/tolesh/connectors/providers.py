"""Provider catalogue for external connections.

Each entry describes how a provider authenticates and where its API lives.
Only providers whose endpoints are documented and stable are listed; a
half-configured provider that cannot be tested is worse than an absent one.

``base_url`` is overridable per connection because GitHub Enterprise, self
hosted GitLab and regional Google endpoints exist. It is *not* free-form: the
manager refuses a base that is not http(s) and refuses a path that could
redirect the request to another host.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    auth: str                     # "oauth" | "token" | "both"
    api_base: str
    docs: str
    scopes: tuple[str, ...] = ()
    authorize_url: str = ""
    token_url: str = ""
    token_style: str = "bearer"   # bearer | github | notion | slack
    extra_headers: dict[str, str] = field(default_factory=dict)
    test_path: str = ""
    # What the connector can actually do, shown in the UI before connecting.
    actions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id, "label": self.label, "auth": self.auth,
            "api_base": self.api_base, "docs": self.docs,
            "scopes": list(self.scopes), "actions": list(self.actions),
            "supports_oauth": self.auth in ("oauth", "both"),
            "supports_token": self.auth in ("token", "both"),
        }


PROVIDERS: dict[str, Provider] = {
    "github": Provider(
        id="github", label="GitHub", auth="both",
        api_base="https://api.github.com", docs="https://docs.github.com/rest",
        scopes=("read:user", "repo", "read:org"),
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        token_style="github",
        extra_headers={"Accept": "application/vnd.github+json",
                       "X-GitHub-Api-Version": "2022-11-28"},
        test_path="/user",
        actions=("list_repositories", "read_file", "list_issues",
                 "create_issue", "search_code"),
    ),
    "google_drive": Provider(
        id="google_drive", label="Google Drive", auth="oauth",
        api_base="https://www.googleapis.com", docs="https://developers.google.com/drive/api",
        scopes=("https://www.googleapis.com/auth/drive.readonly",),
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        test_path="/drive/v3/about?fields=user",
        actions=("list_files", "read_file"),
    ),
    "gmail": Provider(
        id="gmail", label="Gmail", auth="oauth",
        api_base="https://gmail.googleapis.com", docs="https://developers.google.com/gmail/api",
        scopes=("https://www.googleapis.com/auth/gmail.readonly",),
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        test_path="/gmail/v1/users/me/profile",
        actions=("list_messages", "read_message"),
    ),
    "google_calendar": Provider(
        id="google_calendar", label="Google Calendar", auth="oauth",
        api_base="https://www.googleapis.com", docs="https://developers.google.com/calendar/api",
        scopes=("https://www.googleapis.com/auth/calendar.readonly",),
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        test_path="/calendar/v3/users/me/calendarList",
        actions=("list_events",),
    ),
    "notion": Provider(
        id="notion", label="Notion", auth="both",
        api_base="https://api.notion.com", docs="https://developers.notion.com/reference",
        authorize_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        extra_headers={"Notion-Version": "2022-06-28"},
        test_path="/v1/users/me",
        actions=("search", "read_page"),
    ),
    "slack": Provider(
        id="slack", label="Slack", auth="both",
        api_base="https://slack.com/api", docs="https://api.slack.com/methods",
        scopes=("channels:read", "chat:write", "users:read"),
        authorize_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        test_path="/auth.test",
        actions=("list_channels", "post_message"),
    ),
}

# Actions that change something outside Tolesh. They are never run by an agent
# without an explicit approval, and the UI marks them before the click.
WRITE_ACTIONS = {"create_issue", "post_message", "send_message", "delete_file",
                 "update_page", "create_page"}


def provider(provider_id: str) -> Provider:
    try:
        return PROVIDERS[provider_id]
    except KeyError:
        raise KeyError(f"Unknown provider: {provider_id}") from None


def catalogue() -> list[dict[str, object]]:
    return [p.as_dict() for p in PROVIDERS.values()]
