import asyncio
import json
from pathlib import Path

from aggregator.upstream import UpstreamManager

ROOT = Path(__file__).resolve().parent.parent
NOTES = {"command": "python", "args": [str(ROOT / "notes_server.py")]}


def test_resource_and_prompt_are_forwarded(tmp_path):
    path = tmp_path / "servers.json"
    path.write_text(json.dumps({"servers": {"notes": NOTES}, "policy": {"block": []}}))

    async def main():
        manager = UpstreamManager(str(path))
        await manager.start()
        try:
            uris = [str(r.uri) for r in manager.list_resources()]
            assert "agg://notes/notes://latest" in uris

            text = await manager.read_resource("agg://notes/notes://latest")
            assert text == "no notes yet"

            names = [p.name for p in manager.list_prompts()]
            assert "notes__summarize_notes" in names

            result = await manager.get_prompt("notes__summarize_notes", None)
            assert len(result.messages) >= 1
        finally:
            await manager.close()

    asyncio.run(main())