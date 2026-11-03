"""Tool selection: given a natural-language request, pick the best tool.

Default: deterministic keyword scoring (works offline, no cost).
Optional: Amazon Bedrock picks the tool; keyword scoring is the fallback if Bedrock fails.
"""
import json
import re

STOP = {"a", "an", "the", "to", "of", "and", "or", "for", "me", "my", "i", "please", "in", "on", "is", "it", "this", "that", "all"}


def tokens(text: str) -> list[str]:
    out = []
    for t in re.findall(r"[a-z0-9]+", text.lower()):
        if t in STOP:
            continue
        if len(t) > 3 and t.endswith("s"):
            t = t[:-1]  # crude stemming: notes -> note
        out.append(t)
    return out


# Small, general verb synonyms so "remember a note" can reach a "save" tool.
SYNONYMS = {
    "remember": "save", "store": "save", "write": "save", "record": "save",
    "show": "list", "display": "list", "view": "list", "see": "list",
    "sum": "add", "plus": "add", "total": "add",
    "repeat": "echo", "return": "echo", "mirror": "echo",
    "login": "auth", "authentication": "auth", "authenticated": "auth", "authorization": "auth",
}


def expand(words: list[str]) -> set[str]:
    return {SYNONYMS.get(w, w) for w in words}


def keyword_rank(query: str, catalog: dict[str, str], top_k: int = 3) -> list[dict]:
    """catalog maps namespaced tool name -> description.

    Score = sum of IDF weights of query words found in the tool's name/description.
    IDF down-weights words shared by many tools (e.g. "note" when every tool is about notes).
    The server prefix (before "__") is ignored: it is a namespace, not a capability.
    """
    import math

    q = expand(tokens(query))
    docs = {}
    for name, desc in catalog.items():
        short = name.split("__", 1)[-1].replace("_", " ")
        docs[name] = (expand(tokens(short)), expand(tokens(desc)))
    n = len(docs) or 1
    df = {}
    for nt, dt in docs.values():
        for w in nt | dt:
            df[w] = df.get(w, 0) + 1
    ranked = []
    for name, (nt, dt) in docs.items():
        score = sum((2 if w in nt else 1) * (1 + math.log(n / df[w])) for w in q if w in nt or w in dt)
        if score:
            ranked.append({"tool": name, "score": round(score, 3)})
    ranked.sort(key=lambda r: (-r["score"], r["tool"]))
    return ranked[:top_k]


def bedrock_rank(query: str, catalog: dict[str, str], client, model_id: str, top_k: int = 3) -> list[dict]:
    """Ask a Bedrock model to choose. Expects the model to answer with a JSON list of tool names."""
    listing = "\n".join(f"- {n}: {d}" for n, d in catalog.items())
    prompt = (
        "Pick the best tools for the request. Answer ONLY with a JSON list of tool names, best first.\n"
        f"Tools:\n{listing}\n\nRequest: {query}"
    )
    res = client.converse(modelId=model_id, messages=[{"role": "user", "content": [{"text": prompt}]}])
    text = res["output"]["message"]["content"][0]["text"]
    names = json.loads(re.search(r"\[.*\]", text, re.S).group(0))
    valid = [n for n in names if n in catalog][:top_k]
    if not valid:
        raise ValueError("model returned no valid tool")
    return [{"tool": n, "score": top_k - i} for i, n in enumerate(valid)]


class Router:
    def __init__(self, manager, bedrock_client=None, model_id: str = "anthropic.claude-3-haiku-20240307-v1:0"):
        self.manager = manager
        self.bedrock = bedrock_client
        self.model_id = model_id

    def catalog(self) -> dict[str, str]:
        return {k: (t.description or "") for k, (_, _, t) in self.manager.registry.items()}

    def route(self, query: str, top_k: int = 3) -> dict:
        catalog = self.catalog()
        if self.bedrock is not None:
            try:
                return {"method": "bedrock", "matches": bedrock_rank(query, catalog, self.bedrock, self.model_id, top_k)}
            except Exception:
                pass  # fall through to keywords
        return {"method": "keyword", "matches": keyword_rank(query, catalog, top_k)}
