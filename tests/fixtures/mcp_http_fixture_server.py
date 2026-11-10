"""The same fixture tools, served over the MCP streamable HTTP transport.

Used by the tests to prove the HTTP client path (session id handling, JSON
responses, auth headers) against a real server rather than a stub.
"""

import os
import sys

from mcp.server.fastmcp import FastMCP

PORT = int(os.environ.get("FIXTURE_PORT", "8931"))

server = FastMCP("tolesh-http-fixture", host="127.0.0.1", port=PORT)


@server.tool()
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


@server.tool()
def whoami() -> str:
    """Report whether an Authorization header reached the server."""
    return "authorised" if os.environ.get("FIXTURE_REQUIRE_AUTH") else "anonymous"


if __name__ == "__main__":
    server.run(transport="streamable-http")
    sys.exit(0)
