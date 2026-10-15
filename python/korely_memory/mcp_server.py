"""korely-mcp: a stdio MCP server that gives a coding assistant Korely memory.

It comes with `pip install korely-memory` and runs on Python 3.9 or later, with
no dependency: the MCP stdio protocol is implemented in `_mcp_stdio.py`
(`korely-memory[mcp]` still installs, and adds nothing). Point Claude Code /
Cursor / Windsurf at it and
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

# The protocol, standard library only (_mcp_stdio.py): the `mcp` package needs
# Python 3.10, and mcp 2.0 removed the module this server imported.
from ._mcp_stdio import Server
from .client import __version__

mcp = Server("korely", __version__)

# The user_id a tool call gets when the model leaves it out (`main`):
# `--user-id <id>`, or `--repo-scope` for `repo:<name>` of the folder the tool
# starts this server in, the scope `korely hook` writes to. Without either, as
# before: no user_id.
_DEFAULT_USER: Optional[str] = None
# The profile whose key this server uses (`main`, `--profile`): a second
# project next to the default one, e.g. a private one.
_PROFILE: Optional[str] = None


def _client() -> Korely:
    """The client the CLI would build: KORELY_API_KEY env, else the key saved
    in ~/.korely/config.json; with `--profile`, that profile's key."""
    try:
        return Korely(profile=_PROFILE)
    except KorelyError as e:
        raise KorelyError(f"{e} Or run `korely init --agent` to get one free.") from None


def _user(user_id: Optional[str]) -> Optional[str]:
    return user_id or _DEFAULT_USER


def _name(label: Optional[str], canonical: Optional[str]) -> str:
    """An entity under its current name, with the one it was written with:
    "Korely (then: Gordon)". The hosted line does the same."""
    lab = (label or "").strip()
    can = (canonical or "").strip()
    if not can or can.lower() == lab.lower():
        return lab
    return f"{can} (then: {lab})"


def _fact_line(f) -> str:
    """One fact as the tools print it: the line the hosted MCP prints for it.

    The same four tools, on two servers, should read the same. Since
    2026-09-28 the hosted line (memoria_core `riga_mcp`) says:

    - the entity under its current name, with the one it was written with;
    - when the fact began, when a memory last restated it and how many did:
      `(since 2026-03-15, confirmed 2026-09-01, seen 3x)`;
    - `[until DATE]` for an end still to come (a contract that ends on 31
      December is true until then), `[past, ended by DATE]` for history,
      `[superseded DATE]` for a fact a newer one replaced, and
      `[planned, not done]` for an intention, next to `until` when it has an
      end date;
    - every date as its UTC day.

    In 0.1.14 and earlier this copy said `[superseded 2027-01-01]` for a date
    still to come, as the hosted server did until the same day.
    """
    from .cli import _has_ended, _utc_day

    line = (f"{_name(f.subject, getattr(f, 'subject_canonical', None))} · "
            f"{f.predicate} · "
            f"{_name(f.object, getattr(f, 'object_canonical', None))}")
    vf = getattr(f, "valid_from", None)
    if vf:
        line += f"  (since {_utc_day(vf)}"
        lc = getattr(f, "last_confirmed_at", None)
        n = getattr(f, "observation_count", None) or 1
        if lc and _utc_day(lc) != _utc_day(vf):
            line += f", confirmed {_utc_day(lc)}"
        if int(n) > 1:
            line += f", seen {int(n)}x"
        line += ")"
    tense = getattr(f, "tense", None) or "current"
    ia = getattr(f, "invalid_at", None)
    if ia and not _has_ended(ia):
        line += f"  [until {_utc_day(ia)}]"
        if tense == "planned":
            line += "  [planned, not done]"
    elif ia and tense == "past":
        line += f"  [past, ended by {_utc_day(ia)}]"
    elif ia:
        line += f"  [superseded {_utc_day(ia)}]"
    elif tense == "planned":
        line += "  [planned, not done]"
    return line


@mcp.tool()
def korely_get_context(query: str, user_id: Optional[str] = None,
                       token_budget: int = 800, known_as_of: Optional[str] = None) -> str:
    """Recall what Korely knows for one end user, assembled into a prompt-ready block.

    THE primary recall path: call this before answering so the assistant has the
    user's currently-valid typed facts plus the most relevant memories. `user_id`
    scopes to one end user (omit for the account-wide default). `known_as_of`
    (ISO date) rebuilds the block as Korely knew it then.
    """
    try:
        ctx = _client().get_context(query=query, user_id=_user(user_id), token_budget=token_budget,
                                    known_as_of=known_as_of)
    except KorelyError as e:
        return f"error: {e}"
    return ctx.context or "(nothing remembered yet for this query)"


@mcp.tool()
def korely_add(content: str, user_id: Optional[str] = None,
               agent_id: Optional[str] = None, run_id: Optional[str] = None,
               timestamp: Optional[str] = None) -> str:
    """Remember something. Stores a memory and extracts typed bi-temporal facts.

    Call this after the user states a durable preference, decision, or fact so it
    persists across sessions. `user_id` scopes the memory to one end user.
    `timestamp` (ISO date or datetime, optional) is when the memory's events
    occurred; its facts inherit it as valid_from, so as_of reads answer what
    was true then. Omitted: now.
    """
    try:
        m = _client().add(content, user_id=_user(user_id), agent_id=agent_id, run_id=run_id,
                          timestamp=timestamp)
    except KorelyError as e:
        return f"error: {e}"
    out = [f"Stored {m.id}."]
    if getattr(m, "facts", None):
        out.append(f"Extracted {len(m.facts)} fact(s):")
        out += [f"  · {_fact_line(f)}" for f in m.facts]
    return "\n".join(out)


# `limit` defaults to 10, as `korely_search` on both hosted MCP servers
# (GordonPro app/mcp/agent_server.py, korely-agent korely_agent/mcp/server.py,
# checked 2026-10-06). It was 15, the REST default of POST /v1/memories/search,
# so the same tool call gave a model more hits here than on the hosted server.
@mcp.tool()
def korely_search(query: str, user_id: Optional[str] = None, limit: int = 10,
                  min_score: Optional[float] = None) -> str:
    """Semantic (vector) search over raw memories: ranked hits with a relevance score.
    min_score (0 to 1, optional) leaves out hits scored below it.

    Use when you want the underlying memories rather than the assembled context
    block (for that, use korely_get_context).
    """
    try:
        hits = _client().search(query, user_id=_user(user_id), limit=limit, min_score=min_score)
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
                     limit: int = 50, known_as_of: Optional[str] = None) -> str:
    """Read typed (subject, predicate, object) facts, the bi-temporal core.

    `entity` matches subject OR object; `as_of` (ISO date) reconstructs what was
    true on that day (time-travel); `known_as_of` (ISO date) what Korely knew on
    that day (a change told later is not in it); `include_invalidated` also
    shows superseded facts. Deterministic: no model runs.
    """
    try:
        facts = _client().get_facts(
            user_id=_user(user_id), entity=entity, subject=subject,
            predicate_family=predicate_family, as_of=as_of,
            include_invalidated=include_invalidated, limit=limit,
            known_as_of=known_as_of,
        )
    except KorelyError as e:
        return f"error: {e}"
    when = ", ".join(w for w in (f"as of {as_of}" if as_of else "",
                                 f"as known on {known_as_of}" if known_as_of else "") if w)
    if not facts:
        return "no facts." + (f" ({when})" if when else "")
    head = f"# facts {when}\n" if when else ""
    return head + "\n".join(f"- {_fact_line(f)}" for f in facts)


def main(argv: Optional[list] = None) -> None:
    """Console entry point (`korely-mcp`). Runs the stdio MCP server."""
    import argparse

    global _DEFAULT_USER, _PROFILE
    p = argparse.ArgumentParser(prog="korely-mcp")
    p.add_argument("--profile", help="the key saved with `korely init --profile <name>`")
    p.add_argument("--user-id", help="the user_id of a call that names none")
    p.add_argument("--repo-scope", action="store_true",
                   help="that user_id is repo:<name> of the current folder's repository, "
                        "the memory `korely hook` writes (korely init --tool)")
    args = p.parse_args(argv)
    _PROFILE = args.profile
    if args.user_id:
        _DEFAULT_USER = args.user_id
    elif args.repo_scope:
        from .hooks import _git, scope_for

        cwd = os.getcwd()
        # Outside a repository there is no repository's memory to default to:
        # a folder's name would invent one.
        if _git(cwd, "rev-parse", "--show-toplevel"):
            _DEFAULT_USER = scope_for(cwd)
    mcp.run()


if __name__ == "__main__":
    main()
