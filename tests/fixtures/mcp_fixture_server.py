"""A tiny real MCP server used by the test suite.

It is a genuine stdio MCP server built on the official SDK, not a mock: the
tests that use it exercise the same handshake, tool listing, resource reading
and prompt expansion that a third-party server would.
"""

# No ``from __future__ import annotations`` here on purpose: FastMCP inspects
# real annotation objects, and string annotations break its tool registration.
from mcp.server.fastmcp import FastMCP

server = FastMCP("tolesh-fixture")


@server.tool()
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


@server.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@server.tool()
def explode() -> str:
    """Always fails, to prove tool-level errors are reported as errors."""
    raise ValueError("fixture failure on purpose")


@server.resource("fixture://greeting")
def greeting() -> str:
    """A static resource the tests read back."""
    return "hello from the fixture server"


@server.prompt()
def summarise(topic: str) -> str:
    """A prompt with one argument."""
    return f"Summarise {topic} in three sentences."


if __name__ == "__main__":
    server.run()
