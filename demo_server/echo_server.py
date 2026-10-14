from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@mcp.tool()
def echo(text: str) -> str:
    """Return the text unchanged."""
    return text


@mcp.tool()
def delete_note(note_id: str) -> str:
    """Pretend to delete a note. The policy will block this later."""
    return f"deleted {note_id}"


if __name__ == "__main__":
    mcp.run()