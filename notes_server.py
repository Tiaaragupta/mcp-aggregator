import os

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("notes")

notes: list[str] = []


@mcp.tool()
def save_note(text: str) -> str:
    """Save a note and return its number."""
    notes.append(text)
    return f"saved note #{len(notes)}"


@mcp.tool()
def list_notes() -> str:
    """List all saved notes."""
    if not notes:
        return "no notes yet"
    return "\n".join(f"{i + 1}. {n}" for i, n in enumerate(notes))


@mcp.tool()
def auth_status() -> str:
    """Report whether the gateway passed this server a token. Never reveals the token."""
    token = os.environ.get("MCP_TOKEN")
    return f"token present (length {len(token)})" if token else "no token received"


@mcp.resource("notes://latest")
def latest_note() -> str:
    """The most recently saved note."""
    return notes[-1] if notes else "no notes yet"


@mcp.prompt()
def summarize_notes() -> str:
    """Ask the model to summarize every saved note."""
    body = "\n".join(notes) if notes else "(no notes)"
    return f"Summarize these notes:\n{body}"


if __name__ == "__main__":
    mcp.run()