"""`korely` — the command-line front to Korely Agents memory.

A thin wrapper over the same client the SDK exposes: every command is one API
call. Zero extra dependencies (argparse + stdlib), so `pip install korely-memory`
gives you both `import korely_memory` and the `korely` command.

    export KORELY_API_KEY=kor_live_...
    korely add "User prefers TypeScript" --user-id alex
    korely context "what stack does the user like?" --user-id alex
    korely facts --user-id alex --as-of 2026-01-01      # time-travel
    korely delete-all --user-id alex --yes              # GDPR: forget this user

Add `--json` to any command for machine-readable output (scripts, jq).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .client import Korely
from .exceptions import KorelyError

_DEFAULT_BASE = "https://api.korely.ai"


# ── config file (~/.korely/config.json) ─────────────────────────────────────
def _config_path() -> Path:
    home = os.environ.get("KORELY_CONFIG_HOME") or str(Path.home() / ".korely")
    return Path(home) / "config.json"


def _load_config() -> dict:
    try:
        return json.loads(_config_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config(data: dict) -> Path:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(p, 0o600)  # the key is a secret — owner read/write only
    except OSError:
        pass
    return p


def _resolve_key(args) -> Optional[str]:
    """Key precedence: --api-key flag > KORELY_API_KEY env > saved config."""
    return (getattr(args, "api_key", None)
            or os.environ.get("KORELY_API_KEY")
            or _load_config().get("api_key"))


def _resolve_base_url(args) -> Optional[str]:
    return (getattr(args, "base_url", None)
            or os.environ.get("KORELY_BASE_URL")
            or _load_config().get("base_url"))


# ── output helpers ─────────────────────────────────────────────────────────
def _emit_json(obj: Any) -> None:
    def enc(o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        raise TypeError(type(o))
    if isinstance(obj, list):
        print(json.dumps([dataclasses.asdict(x) if dataclasses.is_dataclass(x) else x
                          for x in obj], indent=2, ensure_ascii=False))
    else:
        print(json.dumps(obj, default=enc, indent=2, ensure_ascii=False))


def _mask(key: str) -> str:
    return key[:9] + "…" + key[-4:] if key and len(key) > 14 else "set"


def _fact_line(f) -> str:
    base = f"{f.subject} · {f.predicate} · {f.object}"
    when = f" [from {f.valid_from[:10]}]" if f.valid_from else ""
    if f.invalid_at:
        when += f" (superseded {f.invalid_at[:10]})"
    return base + when


# ── commands ───────────────────────────────────────────────────────────────
def cmd_auth(k: Korely, a) -> int:
    page = k.users(limit=1)  # cheapest authenticated call
    if a.json:
        _emit_json({"authenticated": True, "key": _mask(k.api_key),
                    "base_url": k.base_url, "end_users": page.total})
    else:
        print(f"Authenticated  key {_mask(k.api_key)}  base {k.base_url}")
        print(f"{page.total} end user(s) stored.")
    return 0


def cmd_add(k: Korely, a) -> int:
    content = a.content
    if content == "-" or (content is None and not sys.stdin.isatty()):
        content = sys.stdin.read()
    if not content or not content.strip():
        print("error: no content (pass text, '-' for stdin, or pipe it).", file=sys.stderr)
        return 2
    m = k.add(content, user_id=a.user_id, agent_id=a.agent_id, run_id=a.run_id)
    if a.json:
        _emit_json(m)
    else:
        print(f"stored  {m.id}")
        if m.facts:
            print(f"  extracted {len(m.facts)} fact(s):")
            for f in m.facts:
                print("   ·", _fact_line(f))
    return 0


def cmd_search(k: Korely, a) -> int:
    hits = k.search(a.query, run_id=getattr(a, 'run_id', None), user_id=a.user_id, agent_id=a.agent_id, limit=a.limit)
    if a.json:
        _emit_json(hits)
        return 0
    if not hits:
        print("no matches.")
        return 0
    for h in hits:
        score = f"{h.score:.3f}" if isinstance(h.score, (int, float)) else "—"
        print(f"[{score}] {h.snippet or ''}".rstrip())
        print(f"        {h.id}")
    return 0


def cmd_context(k: Korely, a) -> int:
    ctx = k.get_context(query=a.query, user_id=a.user_id, agent_id=a.agent_id,
                        token_budget=a.token_budget)
    if a.json:
        _emit_json(ctx)
        return 0
    print(ctx.context or "(empty)")
    print(f"\n— {ctx.tokens} tokens, {len(ctx.sources)} source(s)", file=sys.stderr)
    return 0


def cmd_facts(k: Korely, a) -> int:
    facts = k.get_facts(entity=a.entity, subject=a.subject, predicate=a.predicate,
                        predicate_family=a.family,
                        user_id=a.user_id, agent_id=a.agent_id, as_of=a.as_of,
                        include_invalidated=a.include_invalidated, limit=a.limit)
    if a.json:
        _emit_json(facts)
        return 0
    if not facts:
        print("no facts." + (f" (as of {a.as_of})" if a.as_of else ""))
        return 0
    if a.as_of:
        print(f"# what was true as of {a.as_of}\n")
    for f in facts:
        print(" ", _fact_line(f))
    return 0


def cmd_profile(k: Korely, a) -> int:
    p = k.get_profile(user_id=a.user_id, agent_id=a.agent_id, as_of=a.as_of)
    if a.json:
        _emit_json(p)
        return 0
    head = f"profile of {p.user_id}" + (f" as of {p.as_of}" if p.as_of else "")
    print(head + f"  ({p.total} fact(s))")
    for family, facts in (p.by_family or {}).items():
        print(f"\n{family}:")
        for f in facts:
            print("   ·", _fact_line(f))
    if not p.by_family:
        for f in p.facts:
            print("   ·", _fact_line(f))
    return 0


def cmd_users(k: Korely, a) -> int:
    page = k.users(agent_id=a.agent_id, limit=a.limit)
    if a.json:
        _emit_json(page)
        return 0
    if not len(page):
        print("no end users yet.")
        return 0
    print(f"{page.total} end user(s):")
    for u in page:
        print(f"  {u.user_id:30s} {u.memories:>4} mem  {u.facts:>4} facts  "
              f"{(u.last_active or '')[:10]}")
    return 0


def cmd_get(k: Korely, a) -> int:
    m = k.get(a.memory_id)
    if a.json:
        _emit_json(m)
        return 0
    print(f"{m.id}  ({(m.created_at or '')[:19]})")
    print(m.content or "")
    if m.facts:
        print(f"\nfacts ({len(m.facts)}):")
        for f in m.facts:
            print("   ·", _fact_line(f))
    return 0


def cmd_delete(k: Korely, a) -> int:
    r = k.delete(a.memory_id)
    if a.json:
        _emit_json(r)
    else:
        print(f"deleted  {r.id}  (status {r.status}, {r.facts_invalidated or 0} fact(s) invalidated)")
    return 0


def cmd_delete_all(k: Korely, a) -> int:
    if not a.user_id:
        print("error: delete-all needs --user-id (whose data to forget).", file=sys.stderr)
        return 2
    if not a.yes:
        print(f"error: this permanently forgets every memory + fact for user "
              f"'{a.user_id}'. Re-run with --yes to confirm.", file=sys.stderr)
        return 2
    r = k.delete_all(user_id=a.user_id)
    if a.json:
        _emit_json(r)
    else:
        print(f"forgot user {r.user_id}  ({r.memories_forgotten or 0} memory(ies), "
              f"{r.facts_invalidated or 0} fact(s) invalidated, audit {r.audit_id})")
    return 0


def cmd_init(args) -> int:
    """Self-serve signup: mint a hobby key with no Firebase, save it locally.

    The one command that runs WITHOUT a key — it is how you get one. Calls
    POST /v1/agents/init, writes the key to ~/.korely/config.json (chmod 600),
    and from then on every other `korely` command (and the SDK, if you export
    the key) just works.
    """
    import urllib.error
    import urllib.request

    base = (getattr(args, "base_url", None) or os.environ.get("KORELY_BASE_URL")
            or _DEFAULT_BASE).rstrip("/")
    payload = json.dumps({"agent_caller": getattr(args, "agent_caller", None)}).encode("utf-8")
    req = urllib.request.Request(
        base + "/v1/agents/init", data=payload,
        headers={"Content-Type": "application/json", "User-Agent": f"korely-cli/{__version__}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("message", detail)
        except Exception:
            pass
        print(f"error: signup failed ({e.code}): {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"error: could not reach {base}: {e.reason}", file=sys.stderr)
        return 1

    key = data.get("api_key")
    if not key:
        print("error: server did not return an api_key.", file=sys.stderr)
        return 1
    cfg = _load_config()
    cfg["api_key"] = key
    cfg["base_url"] = base
    if data.get("tier"):
        cfg["tier"] = data["tier"]
    path = _save_config(cfg)

    if getattr(args, "json", False):
        _emit_json(data)
        return 0

    q = data.get("quotas") or {}
    print(f"You're set — a free hobby key was minted and saved to {path} (chmod 600).")
    print(f"  key     {_mask(key)}")
    print(f"  tier    {data.get('tier')}    region {data.get('region')}")
    if q:
        print(f"  quotas  {q.get('writes_per_month')} writes / "
              f"{q.get('queries_per_month')} queries per month · {q.get('agents')} agents")
    print()
    print("Next:")
    print('  korely add "I am using Korely"')
    print('  korely search "am I using Korely"')
    return 0


# ── parser ──────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="korely",
        description="Memory for AI agents — bi-temporal typed facts, hybrid retrieval.",
        epilog="Set KORELY_API_KEY (kor_live_...). Docs: https://korely.ai/agents/docs",
    )
    p.add_argument("--version", action="version", version=f"korely {__version__}")
    # shared flags live on a parent so they work AFTER the subcommand too
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("--api-key", help="override KORELY_API_KEY")
    common.add_argument("--base-url", help="API base URL (default https://api.korely.ai)")
    common.add_argument("--user-id", help="end-user namespace (unlimited per tier)")
    common.add_argument("--agent-id", help="agent namespace (counts against your agent cap)")

    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init", help="sign up and save a free hobby key (no key needed)")
    sp.add_argument("--agent", action="store_true",
                    help="agent self-signup (default; mints an anonymous hobby account)")
    sp.add_argument("--agent-caller", help="who is signing up, e.g. 'claude-code'")
    sp.add_argument("--base-url", help="API base URL (default https://api.korely.ai)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("auth", parents=[common], help="verify your API key")
    sp.set_defaults(func=cmd_auth)

    sp = sub.add_parser("add", parents=[common], help="store a memory ('-' or pipe for stdin)")
    sp.add_argument("content", nargs="?", help="text to remember; '-' reads stdin")
    sp.add_argument("--run-id", help="optional run/session id")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("search", parents=[common], help="hybrid search over memories")
    sp.add_argument("query")
    sp.add_argument("--run-id", help="scope to one run/session")
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("context", parents=[common],
                        help="assembled, prompt-ready context block (the agent call)")
    sp.add_argument("query")
    sp.add_argument("--token-budget", type=int, default=800)
    sp.set_defaults(func=cmd_context)

    sp = sub.add_parser("facts", parents=[common],
                        help="typed facts; --as-of DATE for point-in-time (time-travel)")
    sp.add_argument("--entity", help="match subject OR object")
    sp.add_argument("--subject")
    sp.add_argument("--predicate", help="exact predicate, e.g. subscribes_to")
    sp.add_argument("--family", help="predicate family")
    sp.add_argument("--as-of", help="ISO date: what was true on that day")
    sp.add_argument("--include-invalidated", action="store_true",
                    help="also show superseded facts")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_facts)

    sp = sub.add_parser("profile", parents=[common], help="assembled profile of one end user")
    sp.add_argument("--as-of", help="ISO date: the point-in-time profile")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("users", parents=[common], help="end users you've stored data for")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_users)

    sp = sub.add_parser("get", parents=[common], help="one memory by id")
    sp.add_argument("memory_id")
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("delete", parents=[common], help="forget one memory (audited)")
    sp.add_argument("memory_id")
    sp.set_defaults(func=cmd_delete)

    sp = sub.add_parser("delete-all", parents=[common],
                        help="GDPR: forget EVERY memory + fact for one --user-id (needs --yes)")
    sp.add_argument("--yes", action="store_true", help="confirm the irreversible wipe")
    sp.set_defaults(func=cmd_delete_all)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # `init` is the one command that runs WITHOUT a key — it mints one.
    if getattr(args, "command", None) == "init":
        return cmd_init(args)
    try:
        client = Korely(api_key=_resolve_key(args), base_url=_resolve_base_url(args))
    except KorelyError:
        print("error: no API key. Run `korely init --agent` to get one free, "
              "or set KORELY_API_KEY (kor_live_...).", file=sys.stderr)
        return 2
    try:
        return args.func(client, args)
    except KorelyError as e:
        code = getattr(e, "code", None)
        print(f"error: {e}" + (f" [{code}]" if code else ""), file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
