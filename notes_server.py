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


if __name__ == "__main__":
    mcp.run()