"""korely-mcp: a stdio MCP server that gives a coding assistant Korely memory.

`pip install 'korely-memory[mcp]'` adds this server (the `mcp` package it needs
runs on Python 3.10 or later). Point Claude Code / Cursor / Windsurf at it and
your assistant gains four memory tools over your Korely agent store: remember,
recall (the moat), search, and read typed facts, persistent across sessions.
They are the same four tools the hosted server at /agent/mcp offers, for
clients that prefer a local process.

It authenticates with your `kor_live_` key, resolved from KORELY_API_KEY or the
key `korely init --agent` saved to ~/.korely/config.json. Nothing here talks to
the OAuth vault MCP: this is the agent memory API (/v1), keyed, not the personal
vault.

Claude Code / Cursor config:

    {
      "mcpServers": {
        "korely": {
          "command": "korely-mcp",
          "env": { "KORELY_API_KEY": "kor_live_..." }
        }
      }
    }

(Omit env if the key is already in ~/.korely/config.json from `korely init`.)
"""
from __future__ import annotations

import os
from typing import Optional

from .client import Korely
from .exceptions import KorelyError

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as e:  # pragma: no cover - import-time guard
    # Two different problems raise the same exception here. Telling everybody
    # to install the extra was right when `mcp` was missing, and wrong for
    # anyone who had installed it: mcp 2.0 removed `mcp.server.fastmcp`, the
    # extra had no upper bound, so a fresh install got 2.x and was told to
    # install what it had just installed.
    import importlib.util

    try:
        _installed = importlib.util.find_spec("mcp") is not None
    except (ValueError, ImportError):
        _installed = True
    if not _installed:
        raise SystemExit(
            "korely-mcp needs the MCP extra (Python 3.10 or later). Install it with:\n"
            "    pip install 'korely-memory[mcp]'"
        ) from e
    try:
        from importlib.metadata import version as _version
        _found = _version("mcp")
    except Exception:
        _found = "unknown"
    raise SystemExit(
        f"korely-mcp runs on mcp 1.x, and the installed mcp is {_found}, which "
        f"no longer has {e.name or 'mcp.server.fastmcp'}. Install a 1.x release:\n"
        "    pip install 'mcp>=1.2.0,<2'"
    ) from e


mcp = FastMCP("korely")


def _client() -> Korely:
    """Resolve the key like the CLI: KORELY_API_KEY env, else ~/.korely/config.json."""
    from .cli import _load_config

    cfg = _load_config()
    key = os.environ.get("KORELY_API_KEY") or cfg.get("api_key")
    base = os.environ.get("KORELY_BASE_URL") or cfg.get("base_url")
    if not key:
        raise KorelyError(
            "No API key. Run `korely init --agent` to get one free, or set KORELY_API_KEY."
        )
    return Korely(api_key=key, base_url=base)


def _fact_line(f) -> str:
    """One fact as the tools print it, in the tense its dates call for.

    In 0.1.14 and earlier a fact closed on a future date read `[superseded 2027-01-01]`,
    the defect the CLI stopped making in 52e29b7 and this copy kept. It now
    also says what `tense` says, as the hosted MCP does: `past` is history,
    `planned` is an intention, not a fact that happened.
    """
    from .cli import _has_ended

    line = f"{f.subject} · {f.predicate} · {f.object}"
    if getattr(f, "valid_from", None):
        line += f"  (since {f.valid_from[:10]})"
    tense = getattr(f, "tense", None) or "current"
    ia = getattr(f, "invalid_at", None)
    if ia:
        if not _has_ended(ia):
            line += f"  [until {ia[:10]}]"
        elif tense == "past":
            line += f"  [past, ended by {ia[:10]}]"
        else:
            line += f"  [superseded {ia[:10]}]"
    elif tense == "planned":
        line += "  [planned, not done]"
    return line


@mcp.tool()
def korely_get_context(query: str, user_id: Optional[str] = None,
                       token_budget: int = 800) -> str:
    """Recall what Korely knows for one end user, assembled into a prompt-ready block.

    THE primary recall path: call this before answering so the assistant has the
    user's currently-valid typed facts plus the most relevant memories. `user_id`
    scopes to one end user (omit for the account-wide default).
    """
    try:
        ctx = _client().get_context(query=query, user_id=user_id, token_budget=token_budget)
    except KorelyError as e:
        return f"error: {e}"
    return ctx.context or "(nothing remembered yet for this query)"


@mcp.tool()
def korely_add(content: str, user_id: Optional[str] = None,
               agent_id: Optional[str] = None, run_id: Optional[str] = None) -> str:
    """Remember something. Stores a memory and extracts typed bi-temporal facts.

    Call this after the user states a durable preference, decision, or fact so it
    persists across sessions. `user_id` scopes the memory to one end user.
    """
    try:
        m = _client().add(content, user_id=user_id, agent_id=agent_id, run_id=run_id)
    except KorelyError as e:
        return f"error: {e}"
    out = [f"Stored {m.id}."]
    if getattr(m, "facts", None):
        out.append(f"Extracted {len(m.facts)} fact(s):")
        out += [f"  · {_fact_line(f)}" for f in m.facts]
    return "\n".join(out)


@mcp.tool()
def korely_search(query: str, user_id: Optional[str] = None, limit: int = 15) -> str:
    """Semantic (vector) search over raw memories: ranked hits with a relevance score.

    Use when you want the underlying memories rather than the assembled context
    block (for that, use korely_get_context).
    """
    try:
        hits = _client().search(query, user_id=user_id, limit=limit)
    except KorelyError as e:
        return f"error: {e}"
    if not hits:
        return "no matches."
    lines = []
    for h in hits:
        score = f"{h.score:.3f}" if isinstance(h.score, (int, float)) else "n/a"
        lines.append(f"[{score}] {h.snippet or ''}".rstrip() + f"  ({h.id})")
    return "\n".join(lines)


@mcp.tool()
def korely_get_facts(user_id: Optional[str] = None, entity: Optional[str] = None,
                     subject: Optional[str] = None, predicate_family: Optional[str] = None,
                     as_of: Optional[str] = None, include_invalidated: bool = False,
                     limit: int = 50) -> str:
    """Read typed (subject, predicate, object) facts, the bi-temporal core.

    `entity` matches subject OR object; `as_of` (ISO date) reconstructs what was
    true on that day (time-travel); `include_invalidated` also shows superseded
    facts. Deterministic: no model runs.
    """
    try:
        facts = _client().get_facts(
            user_id=user_id, entity=entity, subject=subject,
            predicate_family=predicate_family, as_of=as_of,
            include_invalidated=include_invalidated, limit=limit,
        )
    except KorelyError as e:
        return f"error: {e}"
    if not facts:
        return "no facts." + (f" (as of {as_of})" if as_of else "")
    head = f"# facts as of {as_of}\n" if as_of else ""
    return head + "\n".join(f"- {_fact_line(f)}" for f in facts)


def main() -> None:
    """Console entry point (`korely-mcp`). Runs the stdio MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
