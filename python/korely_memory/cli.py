"""`korely`: the command-line front to Korely Agents memory.

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
from .client import Korely, _refuse_a_mismatched_pair, config_path, profile_config
from .exceptions import APIError, KorelyError

_DEFAULT_BASE = "https://api.korely.ai"


# ── config file (~/.korely/config.json) ─────────────────────────────────────
def _config_path() -> Path:
    return Path(config_path())


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
        os.chmod(p, 0o600)  # the key is a secret: owner read/write only
    except OSError:
        pass
    return p


def _resolve_key(args) -> Optional[str]:
    """Key precedence: --api-key flag > KORELY_API_KEY env > saved config.
    With --profile, the profile's saved key (a profile named is a decision:
    the environment does not override it)."""
    profile = getattr(args, "profile", None)
    if profile:
        return getattr(args, "api_key", None) or profile_config(profile).get("api_key")
    return (getattr(args, "api_key", None)
            or os.environ.get("KORELY_API_KEY")
            or profile_config().get("api_key"))


def _resolve_base_url(args) -> Optional[str]:
    profile = getattr(args, "profile", None)
    if profile:
        return getattr(args, "base_url", None) or profile_config(profile).get("base_url")
    return (getattr(args, "base_url", None)
            or os.environ.get("KORELY_BASE_URL")
            or profile_config().get("base_url"))


def _profile_slot(cfg: dict, profile: Optional[str]) -> dict:
    """Where `korely init` writes a key: the file's top level for the default
    profile, `profiles[<name>]` for `--profile <name>` (created)."""
    if not profile or profile == "default":
        return cfg
    return cfg.setdefault("profiles", {}).setdefault(profile, {})


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


def _has_ended(invalid_at: Optional[str]) -> bool:
    """Whether a fact's end date is already behind us.

    A fact can be closed on a future date (a contract that ends on 31 December,
    a subscription with a known last day), and until then it is still true.
    An unreadable date counts as ended, the safe half of the guess: it is
    closed for every date we can read.
    """
    from datetime import datetime, timezone

    if not invalid_at:
        return False
    try:
        end = datetime.fromisoformat(invalid_at.replace("Z", "+00:00"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return end <= datetime.now(timezone.utc)
    except (ValueError, AttributeError):
        return True


def _utc_day(value: Optional[str]) -> str:
    """The UTC calendar day of an ISO moment, `YYYY-MM-DD` ("" when absent).

    `value[:10]` printed the day in whatever offset the server wrote: a fact
    that ended at 2026-05-31T20:00Z, sent as `2026-06-01T10:00:00+14:00`, read
    "2026-06-01". The hosted MCP prints the UTC day since 2026-09-28
    (memoria_core `_data`), and so does this. A value with no offset is UTC; a
    string that is not ISO keeps its first ten characters.
    """
    from datetime import datetime, timezone

    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:10]
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.date().isoformat()


def _utc_moment(value: Optional[str]) -> str:
    """An ISO moment as its UTC time, `YYYY-MM-DD HH:MM:SS` ("" when absent):
    the timelines (`history`, `events`, `audit`) in one zone whatever offset
    the server wrote, as `_utc_day` does for fact dates. A string that is not
    ISO keeps its first nineteen characters."""
    from datetime import datetime, timezone

    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)[:19].replace("T", " ")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _snippet(text: Optional[str], width: int = 80) -> str:
    """A memory on one line of a listing: whitespace folded, cut at `width`."""
    one = " ".join((text or "").split())
    return one if len(one) <= width else one[:width - 1] + "…"


def _read_content(value: Optional[str]) -> Optional[str]:
    """The text of `add` and `update`: the argument, or stdin for '-' or a pipe."""
    if value == "-" or (value is None and not sys.stdin.isatty()):
        return sys.stdin.read()
    return value


def _fact_line(f) -> str:
    """One fact, and the right tense for its dates.

    `superseded 3026-01-01` reads as a thing that has happened, for a date a
    thousand years away. Saying so in the past tense is a small lie that makes
    somebody distrust the rest of the line.
    """
    base = f"{f.subject} · {f.predicate} · {f.object}"
    when = f" [from {_utc_day(f.valid_from)}]" if f.valid_from else ""
    if f.invalid_at:
        when += (f" (superseded {_utc_day(f.invalid_at)})" if _has_ended(f.invalid_at)
                 else f" (until {_utc_day(f.invalid_at)})")
    return base + when


# ── commands ───────────────────────────────────────────────────────────────
def cmd_auth(k: Korely, a) -> int:
    # GET /v1/ping (2026-10-06): no scope, no rate limit, no quota. It was
    # GET /v1/users, which needs memories:read, so a write-only key failed
    # the check it was meant to pass, and every check spent a query.
    p = k.ping()
    if a.json:
        _emit_json({"authenticated": bool(p.ok), "key": _mask(k.api_key),
                    "base_url": k.base_url, "tier": p.tier, "region": p.region,
                    **({"processing_region": p.processing_region} if p.processing_region else {}),
                    "scopes": p.scopes})
    else:
        print(f"Authenticated  key {_mask(k.api_key)}  base {k.base_url}")
        print(f"tier {p.tier}  region {p.region}{_processing(p)}  scopes {', '.join(p.scopes) or '(none)'}")
    return 0


def _processing(p) -> str:
    """"  processing eu" when the server says where a memory is read by a
    model (since 2026-10-07), nothing from an older one."""
    return f"  processing {p.processing_region}" if getattr(p, "processing_region", None) else ""


def cmd_ping(k: Korely, a) -> int:
    p = k.ping()
    if a.json:
        # The API's shape: processing_region only from a server that answers it.
        out = dataclasses.asdict(p)
        if out.get("processing_region") is None:
            out.pop("processing_region", None)
        _emit_json(out)
        return 0
    print(f"ok  tier {p.tier}  region {p.region}{_processing(p)}  scopes {', '.join(p.scopes) or '(none)'}")
    return 0


def _audit_line(e) -> str:
    line = (f"  {_utc_moment(e.ts):<19}  {e.actor or '':<9} {e.action or '':<15} "
            f"{e.result or '':<6} {e.user_id or '-':<20} {e.target_id or ''}").rstrip()
    if e.read:
        line += f"  (read {len(e.read.memories)} memories, {len(e.read.facts)} facts)"
    return line


def cmd_audit(k: Korely, a) -> int:
    filters = {"user_id": a.user_id, "action": a.action, "since": a.since, "until": a.until}
    if a.all:
        return _audit_export(k, a, filters)
    page = k.audit(limit=a.limit or 100, offset=a.offset, **filters)
    if a.json:
        _emit_json(page)  # {"events": [...], "total": n}, the API's shape
        return 0
    if not len(page):
        print("no events." + (f" (offset {a.offset} of {page.total})" if a.offset else ""))
        return 0
    if a.offset or len(page) < page.total:
        print(f"{a.offset + 1}-{a.offset + len(page)} of {page.total} event(s), newest first:")
    else:
        print(f"{page.total} event(s), newest first:")
    for e in page:
        print(_audit_line(e))
    return 0


def _audit_export(k: Korely, a, filters: dict) -> int:
    """`korely audit --all`: every page (iter_audit), written as it arrives.

    With --json it is still one document in the API's shape, `{"events":
    [...], "total": n}`, streamed event by event: a trail can be longer than
    what the machine exporting it wants to hold in memory. `--offset` resumes
    an export that stopped; pass the same `--until`, so the window is the
    same."""
    n = 0
    events = k.iter_audit(page_size=a.limit or 1000, offset=a.offset, **filters)
    if a.json:
        sys.stdout.write('{"events": [')
        for e in events:
            sys.stdout.write((",\n  " if n else "\n  ")
                             + json.dumps(dataclasses.asdict(e), ensure_ascii=False))
            n += 1
        sys.stdout.write(("\n" if n else "") + f'], "total": {n}}}\n')
        return 0
    for e in events:
        print(_audit_line(e))
        n += 1
    print(f"{n} event(s).")
    return 0


def cmd_delete_account(k: Korely, a) -> int:
    if not a.yes:
        print("error: this deletes the account of this key for good: every key, project, "
              "memory and fact of it. It is for an account made by `korely init --agent` "
              "(Cloud only). Re-run with --yes to confirm.", file=sys.stderr)
        return 2
    r = k.delete_account(confirm=True)
    # The key is dead now. Left in the config file, every later command would
    # fail with a 401 on it, and `korely init` would refuse to replace it.
    cfg = _load_config()
    forgot = cfg.get("api_key") == k.api_key
    if forgot:
        cfg.pop("api_key", None)
        cfg.pop("tier", None)
        _save_config(cfg)
    if a.json:
        _emit_json(r)  # {"deleted": true, "removed": {...}}, the API's shape
        return 0
    removed = ", ".join(f"{n} {kind}" for kind, n in (r.removed or {}).items()) or "nothing"
    print(f"deleted the account of key {_mask(k.api_key)}  (removed: {removed})")
    if forgot:
        print(f"  and the key from {_config_path()}: `korely init --agent` makes a new one.")
    return 0


def cmd_add(k: Korely, a) -> int:
    content = _read_content(a.content)
    if not content or not content.strip():
        print("error: no content (pass text, '-' for stdin, or pipe it).", file=sys.stderr)
        return 2
    m = k.add(content, user_id=a.user_id, agent_id=a.agent_id, run_id=a.run_id,
              timestamp=getattr(a, "timestamp", None))
    if a.json:
        _emit_json(m)
    else:
        print(f"stored  {m.id}")
        if m.facts:
            print(f"  extracted {len(m.facts)} fact(s):")
            for f in m.facts:
                print("   ·", _fact_line(f))
    return 0


def cmd_update(k: Korely, a) -> int:
    content = _read_content(a.content)
    if not content or not content.strip():
        print("error: no content (pass text, '-' for stdin, or pipe it).", file=sys.stderr)
        return 2
    m = k.update(a.memory_id, content=content, expected_updated_at=a.expected_updated_at)
    if a.json:
        _emit_json(m)
    else:
        print(f"updated  {m.id}")
        if m.facts:
            print(f"  extracted {len(m.facts)} fact(s):")
            for f in m.facts:
                print("   ·", _fact_line(f))
    return 0


def cmd_list(k: Korely, a) -> int:
    page = k.get_all(user_id=a.user_id, agent_id=a.agent_id, run_id=a.run_id,
                     limit=a.limit, offset=a.offset)
    if a.json:
        _emit_json(page)  # {"memories": [...], "total": n}, the API's shape
        return 0
    if not len(page):
        print("no memories." + (f" (offset {a.offset} of {page.total})" if a.offset else ""))
        return 0
    if a.offset or len(page) < page.total:
        print(f"{a.offset + 1}-{a.offset + len(page)} of {page.total} memory(ies):")
    else:
        print(f"{page.total} memory(ies):")
    for m in page:
        print(f"  {m.id}  {_utc_day(m.created_at)}  {(m.status or ''):<10}  {_snippet(m.content)}")
    return 0


def cmd_history(k: Korely, a) -> int:
    h = k.history(a.memory_id)
    if a.json:
        _emit_json(h)
        return 0
    print(f"{h.id}  ({len(h.events)} event(s))")
    for e in h.events:
        line = f"  {_utc_moment(e.at):<19}  {e.event or ''}"
        if e.fact:
            line += f"  {e.fact}"
        if e.fact_id:
            line += f"  ({e.fact_id})"
        print(line)
    return 0


def cmd_events(k: Korely, a) -> int:
    r = k.events(user_id=a.user_id, status=a.status, limit=a.limit)
    if a.json:
        _emit_json(r)  # still the API's dict: {"events": [...], "processing": n}
        return 0
    scope = f" for {a.user_id}" if a.user_id else ""
    print(f"{r.processing} write(s) still processing{scope}.")
    for e in r.events:
        print(f"  {e.memory_id}  {(e.status or ''):<10}  {_utc_moment(e.created_at)}")
    return 0


def cmd_search(k: Korely, a) -> int:
    hits = k.search(a.query, run_id=getattr(a, 'run_id', None), user_id=a.user_id, agent_id=a.agent_id,
                    limit=a.limit, min_score=getattr(a, 'min_score', None))
    if a.json:
        # The API's own shape (GET /v1/memories/search answers {"results": [...]}),
        # so `jq '.results[0].snippet'` from the docs works (2026-09-29: it was a
        # bare list, found by the public conformance test).
        _emit_json({"results": [dataclasses.asdict(h) for h in hits]})
        return 0
    if not hits:
        print("no matches.")
        return 0
    for h in hits:
        score = f"{h.score:.3f}" if isinstance(h.score, (int, float)) else "n/a"
        print(f"[{score}] {h.snippet or ''}".rstrip())
        print(f"        {h.id}")
    return 0


def cmd_context(k: Korely, a) -> int:
    ctx = k.get_context(query=a.query, user_id=a.user_id, agent_id=a.agent_id,
                        token_budget=a.token_budget, known_as_of=a.known_as_of)
    if a.json:
        _emit_json(ctx)
        return 0
    print(ctx.context or "(empty)")
    print(f"\n({ctx.tokens} tokens, {len(ctx.sources)} source(s))", file=sys.stderr)
    return 0


def _what_was(as_of, known_as_of) -> str:
    """The heading of a read at a moment: what was true then, what the store
    knew then, or what it knew on one day about another. `--known-as-of`
    alone printed "what was as known on ..." (the site's docs, 11 Oct 2026)."""
    if as_of and known_as_of:
        return f"what the store knew on {known_as_of} about {as_of}"
    if known_as_of:
        return f"what the store knew on {known_as_of}"
    if as_of:
        return f"what was true on {as_of}"
    return ""


def cmd_facts(k: Korely, a) -> int:
    facts = k.get_facts(entity=a.entity, subject=a.subject, predicate=a.predicate,
                        predicate_family=a.family,
                        user_id=a.user_id, agent_id=a.agent_id, as_of=a.as_of,
                        include_invalidated=a.include_invalidated, limit=a.limit,
                        known_as_of=a.known_as_of)
    if a.json:
        # The API's own shape ({"facts": [...], "total": n}), as the docs show
        # (`jq '.facts[]'`); it was a bare list until 2026-09-29.
        _emit_json({"facts": [dataclasses.asdict(f) for f in facts],
                    "total": getattr(facts, "total", len(facts))})
        return 0
    when = _what_was(a.as_of, a.known_as_of)
    if not facts:
        print("no facts." + (f" ({when})" if when else ""))
        return 0
    if when:
        print(f"# {when}\n")
    for f in facts:
        print(" ", _fact_line(f))
    return 0


def cmd_profile(k: Korely, a) -> int:
    p = k.get_profile(user_id=a.user_id, agent_id=a.agent_id, as_of=a.as_of,
                      known_as_of=a.known_as_of)
    if a.json:
        _emit_json(p)
        return 0
    when = _what_was(p.as_of, p.known_as_of)
    head = f"profile of {p.user_id}" + (f", {when}" if when else "")
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
        # The rows are physically deleted, and since 2026-09-28 the receipt
        # says so (memories_deleted, facts_deleted). BulkReceipt fills them
        # from the old names when an older server sends only those.
        print(f"forgot user {r.user_id}  ({r.memories_deleted or 0} memory(ies), "
              f"{r.facts_deleted or 0} fact(s) erased, audit {r.audit_id})")
    return 0


def cmd_agents(k: Korely, a) -> int:
    page = k.list_agents(limit=a.limit, offset=a.offset)
    if a.json:
        _emit_json(page)
        return 0
    cap = (f"{page.used} of {page.cap} agent slot(s) used across the account" if page.cap
           else "no agent cap")
    if not len(page):
        print(f"no agent namespaces in this project ({cap}).")
        return 0
    print(f"{page.total} agent namespace(s) in this project ({cap}):")
    for ag in page:
        print(f"  {ag.agent_id or '':30s} {ag.memories:>4} mem  {ag.facts:>4} facts  "
              f"{(ag.last_active or '')[:10]}")
    return 0


def cmd_delete_agent(k: Korely, a) -> int:
    # Named by --agent-id, as delete-all names its user by --user-id.
    if not a.agent_id:
        print("error: delete-agent needs --agent-id (which namespace to purge).", file=sys.stderr)
        return 2
    if not a.yes:
        print(f"error: this permanently deletes every memory + fact written under agent "
              f"'{a.agent_id}' in this project. Re-run with --yes to confirm.", file=sys.stderr)
        return 2
    r = k.delete_agent(a.agent_id)
    if a.json:
        _emit_json(r)
        return 0
    slot = {True: ", slot freed",
            False: ", slot still taken: another project of the account uses the name"}.get(
                r.slot_freed, "")
    print(f"deleted agent {r.agent_id}  ({r.memories_deleted or 0} memory(ies), "
          f"{r.facts_deleted or 0} fact(s) erased, audit {r.audit_id}{slot})")
    return 0


def cmd_add_fact(k: Korely, a) -> int:
    f = k.add_fact_triple(a.subject, a.predicate, a.object, user_id=a.user_id,
                          agent_id=a.agent_id, run_id=a.run_id, subject_type=a.subject_type,
                          object_is_literal=a.literal, confidence=a.confidence,
                          valid_from=a.valid_from, tense=a.tense)
    if a.json:
        _emit_json(f)
        return 0
    print(f"stored  {f.id}  {_fact_line(f)}")
    if f.invalidated:
        print(f"  superseded: {', '.join(f.invalidated)}")
    return 0


def cmd_correct_fact(k: Korely, a) -> int:
    if not (a.subject or a.predicate or a.object):
        print("error: correct-fact needs at least one of --subject, --predicate, --object.",
              file=sys.stderr)
        return 2
    f = k.correct_fact(a.fact_id, subject=a.subject, predicate=a.predicate, object=a.object)
    if a.json:
        _emit_json(f)
        return 0
    if f.id == a.fact_id and not f.invalidated:
        # The correction restated the fact as it stands: reconfirmed, nothing superseded.
        print(f"reconfirmed  {f.id}  {_fact_line(f)}")
        return 0
    print(f"corrected  {a.fact_id} -> {f.id}  {_fact_line(f)}")
    if f.invalidated:
        print(f"  superseded: {', '.join(f.invalidated)}")
    return 0


def cmd_forget_fact(k: Korely, a) -> int:
    r = k.forget_fact(a.fact_id, at=a.at)
    if a.json:
        _emit_json(r)
        return 0
    word = "already forgotten" if r.status == "already_forgotten" else "forgotten"
    ended = f", true until {_utc_day(r.invalid_at)}" if r.invalid_at else ""
    print(f"{word}  {r.id}  (kept in history{ended}, audit {r.audit_id})")
    return 0


def _read_batch(source: str) -> list:
    """The memories of `korely batch`, from a file or stdin ('-'): a JSON
    array, an object with `memories` (the body of POST /v1/batch), or JSON
    Lines, one memory per line. A memory is an object (the body of one `add`)
    or a string, taken as its content."""
    text = (sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        data = []
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    data.append(json.loads(line))
                except ValueError as e:
                    raise ValueError(f"line {n} is not JSON ({e})")
    if isinstance(data, dict):
        data = data["memories"] if "memories" in data else [data]
    if not isinstance(data, list):
        raise ValueError("expected a list of memories")
    items = []
    for i, m in enumerate(data):
        if isinstance(m, str):
            m = {"content": m}
        if not isinstance(m, dict):
            raise ValueError(f"memory {i} is neither an object nor a string")
        items.append(dict(m))
    return items


def cmd_batch(k: Korely, a) -> int:
    try:
        items = _read_batch(a.file)
    except (OSError, ValueError) as e:
        print(f"error: cannot read {a.file}: {e}", file=sys.stderr)
        return 2
    if not items:
        print(f"error: no memories in {a.file}.", file=sys.stderr)
        return 2
    # --user-id / --agent-id are the scope of the items that name none: every
    # command takes them, and a flag that a command ignores in silence is a
    # batch that lands in the wrong namespace.
    for item in items:
        if a.user_id:
            item.setdefault("user_id", a.user_id)
        if a.agent_id:
            item.setdefault("agent_id", a.agent_id)
    job = k.batch(items)
    if a.json:
        _emit_json(job)
        return 0
    print(f"queued  {job.id}  ({job.received} memory(ies), {job.status})")
    print(f"  follow it:  korely batch-status {job.id}")
    return 0


def cmd_batch_status(k: Korely, a) -> int:
    job = k.batch_status(a.job_id)
    if a.json:
        _emit_json(job)
        return 0
    print(f"{job.id}  {job.status}  {job.imported or 0} of {job.received or 0} imported, "
          f"{job.failed or 0} failed")
    errors = job.errors or []
    for e in errors[:10]:
        if isinstance(e, dict) and "index" in e:
            print(f"  memory {e.get('index')}: {e.get('error')}")
        else:
            print(f"  {e}")
    if len(errors) > 10:
        print(f"  ...and {len(errors) - 10} more (--json shows them all)")
    return 0


def _init_key(args) -> int:
    """Self-serve signup: mint a hobby key with no Firebase, save it locally.

    The one command that runs WITHOUT a key: it is how you get one. Calls
    POST /v1/agents/init (Korely.init_agent), writes the key to
    ~/.korely/config.json (chmod 600), and from then on every other `korely`
    command (and the SDK, if you export the key) just works.
    """
    base = (getattr(args, "base_url", None) or os.environ.get("KORELY_BASE_URL")
            or _DEFAULT_BASE).rstrip("/")

    # `--api-key` means "I already have one, save it" and skips signup entirely.
    # Somebody running their own install has a key their own dashboard minted;
    # there is nothing to sign up for, and until now there was no way to put it
    # in the config file the library reads. The self-hosted README documented
    # this flag as one of the three ways to point the client at your own
    # server, which made the README right about the idea and wrong about the
    # command: it did not exist, and the error was an unrecognized argument.
    given = (getattr(args, "api_key", None) or "").strip()

    # A key already saved is not replaced without --force (2026-10-06). The
    # account `init --agent` makes has no login: its key is the only way to
    # use it and the only way to delete it (`korely delete-account`). A second
    # `init` overwrote the file, and the first account stayed on the server,
    # with its memories, impossible to close, its key held by nobody. Checked
    # before the signup call, so a refused init mints no account either.
    profile = getattr(args, "profile", None)
    saved = _profile_slot(_load_config(), profile).get("api_key")
    if saved and saved != given and not getattr(args, "force", False):
        print(f"error: nothing changed. A key is already saved in {_config_path()} "
              f"({_mask(saved)}).\n"
              "  If it came from `korely init --agent`, its account has no login: that "
              "key is the only way\n"
              "  to use the account, and the only way to delete it.\n"
              "  To close that account first:  korely delete-account --yes\n"
              "  To keep it, copy the key somewhere safe, then re-run with --force.",
              file=sys.stderr)
        return 2

    region = getattr(args, "processing_region", None)
    if given and region:
        # A key already made has its project and its region: the flag would
        # be silently ignored, and the developer would believe the memories
        # are read in the region they asked for.
        print("error: nothing saved. --processing-region chooses the region of a NEW "
              "account; a key you already have keeps its project's region "
              "(change it in the dashboard).", file=sys.stderr)
        return 2

    if given:
        # A pair the client will refuse on every later command must not be
        # saved with "Saved" printed over it. `korely init --api-key kor_self_...`
        # without --base-url used to write the hosted address next to a
        # self-hosted key and report success; every command after it failed.
        try:
            _refuse_a_mismatched_pair(given, base)
        except KorelyError as e:
            print(f"error: nothing saved. {e}", file=sys.stderr)
            return 2
        cfg = _load_config()
        slot = _profile_slot(cfg, profile)
        if slot.get("api_key") != given:
            slot.pop("tier", None)  # the plan of the key this one replaces
        slot["api_key"] = given
        slot["base_url"] = base
        path = _save_config(cfg)
        if getattr(args, "json", False):
            _emit_json({"api_key": given, "base_url": base, "config": str(path)})
            return 0
        print(f"Saved to {path} (chmod 600)" + (f", profile {profile}." if profile else "."))
        print(f"  key       {_mask(given)}")
        print(f"  base_url  {base}")
        print()
        print("Check it reaches your server:")
        print("  korely auth")
        return 0

    # The SDK's call since 2026-10-06, so the transport, the TLS settings and
    # the reading of the error envelope are the ones every other command has
    # (`message`, else what `detail` says, never the raw JSON). This command
    # used to carry its own urllib request and its own copy of the parsing.
    try:
        # --processing-region (12 Oct 2026, the founder's request): the API,
        # the Python SDK and the JS SDK could choose "eu" at signup, the CLI
        # could not, so a developer who started here got a Global project.
        # Left out, the server's default (Global, the founder's 6 Oct choice).
        result = Korely.init_agent(getattr(args, "agent_caller", None), base_url=base,
                                   processing_region=region)
    except APIError as e:
        print(f"error: signup failed ({e.status}): {e.message or e}", file=sys.stderr)
        return 1
    except KorelyError as e:
        # Never reached the server, or got no usable answer: connection
        # refused, a timeout, a body that is not JSON.
        print(f"error: signup at {base} failed: {e}", file=sys.stderr)
        return 1

    key = result.api_key
    if not key:
        print("error: server did not return an api_key.", file=sys.stderr)
        return 1
    cfg = _load_config()
    slot = _profile_slot(cfg, profile)
    slot.pop("tier", None)  # the plan of a key replaced with --force
    slot["api_key"] = key
    slot["base_url"] = base
    if result.tier:
        slot["tier"] = result.tier
    path = _save_config(cfg)

    if getattr(args, "json", False):
        _emit_json(dataclasses.asdict(result))
        return 0

    q = result.quotas or {}
    print(f"You're set: a free hobby key was minted and saved to {path} (chmod 600).")
    print(f"  key     {_mask(key)}")
    print(f"  tier    {result.tier}    region {result.region}{_processing(result)}")
    if q:
        print(f"  quotas  {q.get('writes_per_month')} writes / "
              f"{q.get('queries_per_month')} queries per month · {q.get('agents')} agents")
    # Creating the account accepts the Terms and the Privacy Policy (the
    # founder's decision of 11 Oct 2026): the server says which, from 0.1.27.
    if result.terms_url and result.privacy_url:
        print("  terms   creating the account accepts the Terms and the Privacy Policy:")
        print(f"          {result.terms_url}")
        print(f"          {result.privacy_url}")
    print()
    print("Next:")
    print('  korely add "I am using Korely"')
    print('  korely search "am I using Korely"')
    return 0


def cmd_init(args) -> int:
    """`korely init`, and with `--tool` the hooks of a coding assistant.

    With `--tool` and a key already saved there is nothing to sign up for:
    the hooks are installed with it. Without a key, the key first (a signup,
    or `--api-key`), then the hooks. `--remove` needs no key at all.
    """
    if getattr(args, "api_key", None) is not None and not args.api_key.strip():
        # `--api-key ""` is a key that did not arrive (a shell variable left
        # empty, a paste that missed), never a request for a new account. On
        # 17 Oct 2026 `read -s K && korely init --api-key "$K"` left K empty,
        # and init fell through to the signup: a second account with no
        # login, saved as the default profile, in the founder's own test.
        print("error: the key is empty: nothing saved, nothing installed, and no account "
              "created. Paste the key again (korely init --api-key <key>).", file=sys.stderr)
        return 2
    names = _names(args)
    if names and not getattr(args, "user_id", None):
        # A repository's memory (repo:<name>) is not a person: a name declared
        # there would read your own sentences as the repository's, the defect
        # of 17 Oct 2026 this option exists to get away from.
        print("error: --name needs --user-id: a name belongs to a person's memory, never to "
              "a repository's (repo:<name>).", file=sys.stderr)
        return 2
    code = _init_without_the_name(args)
    if code or not names:
        return code
    for name in names:
        code = _declare_name(args, name)
        if code:
            return code
    return 0


def _names(args) -> list:
    """The names of `--name`, which can be given more than once: the full name
    and the short one a person is also called by ("Tomas Rossi", "Tomas"). On
    the identity bench of 18 Oct 2026 a user called by an undeclared short name
    read as somebody else, and no rule can tell that short name from a
    namesake's ("Marco" and "Marco Bianchi"): only the person can, by
    declaring it. Each once, in the order given."""
    given = getattr(args, "name", None) or []
    if isinstance(given, str):
        given = [given]
    out = []
    for n in given:
        n = (n or "").strip()
        if n and n not in out:
            out.append(n)
    return out


def _init_without_the_name(args) -> int:
    tool = getattr(args, "tool", None)
    if not tool:
        if getattr(args, "name", None) and not getattr(args, "api_key", None) \
                and _resolve_key(args):
            # The key is already there and only the name is asked for: no
            # signup, and no "a key is already saved" refusal.
            return 0
        return _init_key(args)
    if not getattr(args, "remove", False):
        given = (getattr(args, "api_key", None) or "").strip()
        have = (profile_config(args.profile).get("api_key") if getattr(args, "profile", None)
                else os.environ.get("KORELY_API_KEY") or profile_config().get("api_key"))
        if given or not have:
            code = _init_key(args)
            if code:
                return code
            print()
    return _install_tool(args)


def _declare_name(args, name: str) -> int:
    """`--name`: the person's full name, declared for `--user-id` in the key's
    project (POST /v1/users/{id}/names). The store never merges two names of a
    person by itself; on the identity bench a declared name united the full
    name and the id in 23 pairs of 28, with no wrong merge."""
    key = _resolve_key(args)
    if not key:
        print("error: no API key to declare the name with.", file=sys.stderr)
        return 2
    try:
        d = Korely(api_key=key, base_url=_resolve_base_url(args)).declare_name(
            name, user_id=args.user_id)
    except KorelyError as e:
        print(f"error: the name was not declared: {e}", file=sys.stderr)
        return 1
    print(f"name     \"{d.name}\" is {d.user_id}: what the memories say about it is theirs, "
          "the facts already written included")
    return 0


def _install_tool(args) -> int:
    from . import hooks

    tool = args.tool
    cwd = os.getcwd()
    project = Path(hooks._git(cwd, "rev-parse", "--show-toplevel") or cwd)
    everywhere = bool(getattr(args, "global_", False))
    remove = bool(getattr(args, "remove", False))
    scope = getattr(args, "user_id", None)
    try:
        report = hooks.install(tool, project, everywhere=everywhere, remove=remove,
                               mcp=not getattr(args, "no_mcp", False), scope=scope,
                               also_read=getattr(args, "also_read", None) or (),
                               also_read_user=getattr(args, "also_read_user", None) or ())
    except (hooks.InstallError, ValueError, OSError) as e:
        print(f"error: nothing installed. {e}", file=sys.stderr)
        return 2
    name = hooks.TOOL_NAMES[tool]
    if remove:
        print(f"Korely hooks for {name} removed.")
        for line in report:
            print(f"  {line}")
        return 0
    memory = scope or (hooks.scope_for(cwd) if not everywhere else "repo:<name of each repository>")
    print(f"Korely is connected to {name} "
          f"({'every repository' if everywhere else 'this repository'}).")
    for line in report:
        print(f"  {line}")
    print()
    print(f"  memory   user_id {memory}: every assistant you connect reads and writes it")
    print("  start    the assistant is handed what Korely holds for the repository, and on "
          "the first prompt what matches it")
    print(f"  saving   every {hooks.DEFAULT_EVERY} answers, before a compaction and at the end, "
          "as one memory; what looks like a key is masked first")
    if tool == "codex":
        print()
        print("Codex runs a new hook only once you trust it: open /hooks in Codex and trust "
              "the five korely hooks.")
    if tool == "cursor":
        print()
        print("Cursor runs Claude Code's hooks too; korely's Claude Code hooks step aside "
              "inside Cursor, so a turn is stored once.")
        print("Cursor does not wait for sessionStart: the first answer can come before the "
              "context does.")
    if tool == "claude-code":
        print()
        print("Claude Code runs hooks in a folder you trusted. Restart the session to load them.")
    if not everywhere:
        print()
        print(f"See what it holds:  korely context \"what did we decide\" --user-id {memory}")
    return 0


def cmd_hook(args) -> int:
    """`korely hook <event>`: what an assistant's hook runs. Reads the event
    on stdin, prints what the tool expects, and exits 0 whatever happens."""
    from . import hooks

    if args.event == "flush":
        from .client import Korely

        try:
            # Each outbox names the tool that filled it: the memory says which
            # assistant wrote it (X-Korely-Client), outside the agent cap.
            hooks.flush(args.session, lambda tool=None: Korely(timeout=hooks.NETWORK_TIMEOUT,
                                                              client=tool))
        except Exception as e:  # noqa: BLE001
            hooks._log(f"flush: {type(e).__name__}: {e}")
        return 0
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        data = json.loads(raw) if raw.strip() else {}
        if not isinstance(data, dict):
            data = {}
    except (ValueError, OSError):
        data = {}
    out = hooks.run_hook(args.event, args.tool, data, every=args.every,
                         fixed_scope=getattr(args, "user_id", None),
                         also_read=getattr(args, "also_read", None) or (),
                         also_read_user=getattr(args, "also_read_user", None) or ())
    if out:
        print(out)
    return 0


# ── administration: projects and keys (an admin key) ────────────────────────
# What the dashboard does, the terminal does too (the founder, 17 Oct 2026).
_SCOPE_WORDS = {"read": "memories:read", "write": "memories:write", "admin": "admin"}


def _project_of(k: Korely, ref: str) -> dict:
    """A project by id or by name (case folded), archived ones included."""
    rows = k.projects(include_archived=True)
    hit = [p for p in rows if p.get("id") == ref] or \
        [p for p in rows if (p.get("name") or "").lower() == ref.lower()]
    if len(hit) != 1:
        raise KorelyError(f"no single project named or numbered {ref!r} "
                          f"({len(hit)} match; `korely projects` lists them)")
    return hit[0]


def cmd_projects(k: Korely, a) -> int:
    action = a.action or "list"
    if action == "list":
        rows = k.projects(include_archived=a.archived)
        if a.json:
            _emit_json(rows)
            return 0
        for p in rows:
            flags = " ".join(f for f in ("default" if p.get("is_default") else "",
                                         "archived" if p.get("archived_at") else "") if f)
            print(f"  {p.get('id')}  {p.get('name', ''):24s} region {p.get('region') or '-':6s}"
                  f"{'  ' + flags if flags else ''}")
        return 0
    if action == "create":
        out = k.create_project(a.name, region=a.region, description=a.description)
    elif action == "rename":
        out = k.update_project(_project_of(k, a.project)["id"], name=a.new_name)
    elif action == "archive":
        out = k.archive_project(_project_of(k, a.project)["id"])
    elif action == "unarchive":
        out = k.unarchive_project(_project_of(k, a.project)["id"])
    else:  # delete
        p = _project_of(k, a.project)
        if not a.yes:
            what = "erases every memory and fact it holds" if a.erase else \
                "moves its keys, memories and facts to the default project"
            print(f"error: deleting {p.get('name')!r} {what}. Re-run with --yes to confirm.",
                  file=sys.stderr)
            return 2
        out = k.delete_project(p["id"], erase=a.erase)
    if a.json:
        _emit_json(out)
    else:
        print(f"{action}: {out.get('name') or out.get('id') or 'done'}")
    return 0


def _scopes(text: Optional[str]) -> Optional[list]:
    if not text:
        return None
    return [_SCOPE_WORDS.get(w.strip(), w.strip()) for w in text.split(",") if w.strip()]


def _users(text: Optional[str]) -> Optional[list]:
    if not text:
        return None
    return [u.strip() for u in text.split(",") if u.strip()] or None


def cmd_keys(k: Korely, a) -> int:
    action = a.action or "list"
    if action == "list":
        rows = k.keys()
        if a.json:
            _emit_json(rows)
            return 0
        for r in rows:
            users = f"  only {','.join(r['users'])}" if r.get("users") else ""
            print(f"  {r.get('id')}  {r.get('masked', ''):22s} {(r.get('name') or '')[:24]:24s} "
                  f"{r.get('state', ''):8s} {','.join(r.get('scopes') or [])}"
                  f"  [{r.get('project_name') or '-'}]{users}")
        return 0
    if action == "create":
        project = _project_of(k, a.project)["id"] if a.project else None
        expires: Any = ...
        if a.expires is not None:
            expires = None if a.expires == "never" else int(a.expires)
        out = k.create_key(a.name, project_id=project, scopes=_scopes(a.scopes),
                           users=_users(a.users), expires_in_days=expires)
        secret = out.pop("key", None)
        if a.save_as and secret:
            # Straight into a profile, never on the screen nor in the
            # terminal's scrollback.
            cfg = _load_config()
            slot = _profile_slot(cfg, a.save_as)
            slot.pop("tier", None)
            slot["api_key"] = secret
            slot["base_url"] = k.base_url
            path = _save_config(cfg)
            print(f"created {out.get('name')} ({out.get('masked')}), saved to {path} as profile "
                  f"{a.save_as}; use it with --profile {a.save_as}")
            return 0
        if a.json:
            _emit_json(dict(out, key=secret))
            return 0
        print(f"created {out.get('name')}: {secret}")
        print("This is the only time the key is shown. Next time, --save-as <profile> keeps it "
              "off the screen.")
        return 0
    if action == "move":
        out = k.move_key(a.key_id, _project_of(k, a.project)["id"])
    else:  # revoke
        if not a.yes:
            print("error: a revoked key stops working at once and for good. Re-run with --yes.",
                  file=sys.stderr)
            return 2
        out = k.revoke_key(a.key_id)
    if a.json:
        _emit_json(out)
    else:
        print(f"{action}: {out.get('name') or out.get('id') or 'done'}")
    return 0


# ── parser ──────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="korely",
        description="Memory for AI agents: bi-temporal typed facts, semantic retrieval.",
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
    common.add_argument("--profile",
                        help="use the key saved with `korely init --profile <name>` "
                             "(else KORELY_PROFILE, else the default one)")

    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser(
        "init",
        help="save a key to ~/.korely/config.json, or sign up for a free one")
    sp.add_argument("--api-key",
                    help="save this key instead of signing up (self-hosted: the "
                         "key your own dashboard minted)")
    sp.add_argument("--agent", action="store_true",
                    help="agent self-signup (default; mints an anonymous hobby account)")
    sp.add_argument("--agent-caller", help="who is signing up, e.g. 'claude-code'")
    sp.add_argument("--base-url", help="API base URL (default https://api.korely.ai)")
    sp.add_argument("--processing-region", choices=["eu", "global"],
                    help="where the new account's memories are read by a model: eu "
                         "(gpt-oss-120b on Scaleway, Paris) or global (Gemini, by Google; "
                         "the default)")
    sp.add_argument("--force", action="store_true",
                    help="replace a key already saved in ~/.korely/config.json (it is lost)")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.add_argument("--profile",
                    help="save the key under this name, next to the default one (a second "
                         "project, e.g. a private one); other commands take --profile too")
    sp.add_argument("--tool", choices=["claude-code", "codex", "cursor"],
                    help="also connect this coding assistant: hooks that hand it the "
                         "repository's memory at the start and save the session to it")
    sp.add_argument("--global", dest="global_", action="store_true",
                    help="with --tool: every repository (your user settings), each with its "
                         "own memory; default: this repository only")
    sp.add_argument("--user-id",
                    help="with --tool: one memory for all of it instead of repo:<name>; "
                         "with --name: the person the name is declared for")
    sp.add_argument("--name", action="append",
                    help="declare a person's name for --user-id (e.g. --name "
                         "\"Maria Rossi\" --name Maria --user-id maria): the memories' facts "
                         "about that name are read as that user's. Give it once per name the "
                         "person is called by: the store never merges two names of a person "
                         "by itself")
    sp.add_argument("--no-mcp", action="store_true",
                    help="with --tool: hooks only, no korely MCP server")
    sp.add_argument("--remove", action="store_true",
                    help="with --tool: take korely's hooks and MCP server out again")
    sp.add_argument("--also-read-user", action="append", default=[], metavar="USER_ID",
                    help="with --tool: the session start also reads this user_id of the same "
                         "project (a memory across repositories, e.g. your own); sessions are "
                         "still saved to repo:<name>")
    sp.add_argument("--also-read", action="append", default=[], metavar="PROFILE",
                    help="with --tool: the session start also reads this profile's project "
                         "(the whole project), and a second MCP server korely-<profile> is "
                         "added; sessions are still saved to the default profile only")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("hook", help="what a coding assistant's hook runs (korely init --tool)")
    sp.add_argument("event", choices=["session-start", "prompt", "stop", "pre-compact",
                                      "session-end", "flush"])
    sp.add_argument("--tool", choices=["claude-code", "codex", "cursor"], default="claude-code")
    sp.add_argument("--user-id", help="the memory to use instead of repo:<name>")
    sp.add_argument("--every", type=int, default=5,
                    help="answers kept before they are sent as one memory (default 5)")
    sp.add_argument("--session", help="flush: only this session")
    sp.add_argument("--also-read", action="append", default=[], metavar="PROFILE",
                    help="session-start and prompt: also read this profile's project")
    sp.add_argument("--also-read-user", action="append", default=[], metavar="USER_ID",
                    help="session-start and prompt: also read this user_id of the same project")
    sp.set_defaults(func=cmd_hook)

    sp = sub.add_parser("auth", parents=[common], help="verify your API key")
    sp.set_defaults(func=cmd_auth)

    sp = sub.add_parser("ping", parents=[common],
                        help="the key's tier, region and scopes (no scope, no quota)")
    sp.set_defaults(func=cmd_ping)

    sp = sub.add_parser("add", parents=[common], help="store a memory ('-' or pipe for stdin)")
    sp.add_argument("content", nargs="?", help="text to remember; '-' reads stdin")
    sp.add_argument("--run-id", help="optional run/session id")
    sp.add_argument("--timestamp",
                    help="ISO date the events happened (backfill): the facts take it as valid_from")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("update", parents=[common],
                        help="replace a memory's text and re-run extraction ('-' or pipe for stdin)")
    sp.add_argument("memory_id")
    sp.add_argument("content", nargs="?", help="the new text; '-' reads stdin")
    sp.add_argument("--expected-updated-at",
                    help="the updated_at you read: refused (stale_write) if it changed since")
    sp.set_defaults(func=cmd_update)

    sp = sub.add_parser("search", parents=[common], help="semantic search over memories")
    sp.add_argument("query")
    sp.add_argument("--run-id", help="scope to one run/session")
    # No default of our own (2026-10-06): it was 10 against the API's 15, so
    # the same search answered fewer hits here than in the SDK or over REST.
    # Not sent, the server applies its own, which is the API's by definition.
    sp.add_argument("--limit", type=int, default=None,
                    help="max hits (default: the server's, 15; max 50)")
    sp.add_argument("--min-score", type=float, default=None,
                    help="leave out hits scored below this, 0 to 1 (0.20 measured on LongMemEval)")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("context", parents=[common],
                        help="assembled, prompt-ready context block (the agent call)")
    sp.add_argument("query")
    sp.add_argument("--token-budget", type=int, default=800)
    sp.add_argument("--known-as-of", help="ISO date: the block as Korely knew it then")
    sp.set_defaults(func=cmd_context)

    sp = sub.add_parser("facts", parents=[common],
                        help="typed facts; --as-of DATE for point-in-time (time-travel)")
    sp.add_argument("--entity", help="match subject OR object")
    sp.add_argument("--subject")
    sp.add_argument("--predicate", help="exact predicate, e.g. subscribes_to")
    sp.add_argument("--family", help="predicate family")
    sp.add_argument("--as-of", help="ISO date: what was true on that day")
    sp.add_argument("--known-as-of",
                    help="ISO date: what Korely knew on that day (a change told later is not in it)")
    sp.add_argument("--include-invalidated", action="store_true",
                    help="also show superseded facts")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_facts)

    sp = sub.add_parser("add-fact", parents=[common],
                        help="write a typed fact directly, no extraction (contradiction-checked)")
    sp.add_argument("subject")
    sp.add_argument("predicate")
    sp.add_argument("object")
    sp.add_argument("--run-id", help="optional run/session id")
    sp.add_argument("--valid-from", help="ISO date it became true (default: now)")
    sp.add_argument("--tense", choices=["current", "past", "planned"],
                    help="past closes the open fact it restates (default: the server's, current)")
    sp.add_argument("--subject-type", default="unknown", help="person, company... (default unknown)")
    sp.add_argument("--literal", action="store_true",
                    help="the object is a value (a price, a date), not an entity")
    sp.add_argument("--confidence", type=float, default=0.9)
    sp.set_defaults(func=cmd_add_fact)

    sp = sub.add_parser("correct-fact", parents=[common],
                        help="supersede a fact with a corrected one (the old one stays in history)")
    sp.add_argument("fact_id")
    sp.add_argument("--subject")
    sp.add_argument("--predicate")
    sp.add_argument("--object")
    sp.set_defaults(func=cmd_correct_fact)

    sp = sub.add_parser("forget-fact", parents=[common],
                        help="close a fact: no longer current, kept in history")
    sp.add_argument("fact_id")
    sp.add_argument("--at", help="ISO date it STOPPED being true (default: now)")
    sp.set_defaults(func=cmd_forget_fact)

    sp = sub.add_parser("profile", parents=[common], help="assembled profile of one end user")
    sp.add_argument("--as-of", help="ISO date: the point-in-time profile")
    sp.add_argument("--known-as-of", help="ISO date: the profile as Korely knew it then")
    sp.set_defaults(func=cmd_profile)

    sp = sub.add_parser("users", parents=[common], help="end users you've stored data for")
    sp.add_argument("--limit", type=int, default=50)
    sp.set_defaults(func=cmd_users)

    sp = sub.add_parser("agents", parents=[common],
                        help="this project's agent namespaces, and the agent cap")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--offset", type=int, default=0)
    sp.set_defaults(func=cmd_agents)

    sp = sub.add_parser("list", parents=[common], help="the memories of a scope, newest first")
    sp.add_argument("--run-id", help="one run/session")
    sp.add_argument("--limit", type=int, default=50, help="up to 200 (default 50)")
    sp.add_argument("--offset", type=int, default=0)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("get", parents=[common], help="one memory by id")
    sp.add_argument("memory_id")
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("history", parents=[common],
                        help="a memory's timeline, and every fact it produced")
    sp.add_argument("memory_id")
    sp.set_defaults(func=cmd_history)

    sp = sub.add_parser("events", parents=[common],
                        help="which writes are still being extracted (status per memory)")
    sp.add_argument("--status", choices=["processing", "ready", "error"],
                    help="only this state, before --limit")
    sp.add_argument("--limit", type=int, default=50, help="up to 200 (default 50)")
    sp.set_defaults(func=cmd_events)

    sp = sub.add_parser("delete", parents=[common], help="forget one memory (audited)")
    sp.add_argument("memory_id")
    sp.set_defaults(func=cmd_delete)

    sp = sub.add_parser("delete-all", parents=[common],
                        help="GDPR: forget EVERY memory + fact for one --user-id (needs --yes)")
    sp.add_argument("--yes", action="store_true", help="confirm the irreversible wipe")
    sp.set_defaults(func=cmd_delete_all)

    sp = sub.add_parser("delete-agent", parents=[common],
                        help="purge one --agent-id of this project, freeing its slot (needs --yes)")
    sp.add_argument("--yes", action="store_true", help="confirm the irreversible purge")
    sp.set_defaults(func=cmd_delete_agent)

    sp = sub.add_parser("batch", parents=[common],
                        help="bulk import (up to 500): JSON array, {\"memories\": [...]} or JSON Lines")
    sp.add_argument("file", help="the file; '-' reads stdin")
    sp.set_defaults(func=cmd_batch)

    sp = sub.add_parser("batch-status", parents=[common], help="poll an import job")
    sp.add_argument("job_id")
    sp.set_defaults(func=cmd_batch_status)

    sp = sub.add_parser("audit", parents=[common],
                        help="the audit trail of this key's project, newest first (--all to export)")
    sp.add_argument("--action",
                    help="read | write | fact_write | fact_invalidate | erase | key_create | "
                         "key_revoke (and tenant_create on the Self-hosted)")
    sp.add_argument("--since", help="ISO 8601 date or datetime: events at or after it")
    sp.add_argument("--until", help="ISO 8601 date or datetime: events at or before it")
    sp.add_argument("--limit", type=int, default=None,
                    help="events per request, up to 1000 (default 100; 1000 with --all)")
    sp.add_argument("--offset", type=int, default=0)
    sp.add_argument("--all", action="store_true",
                    help="every page, for an export (with --json: one streamed JSON document)")
    sp.set_defaults(func=cmd_audit)

    sp = sub.add_parser("projects", parents=[common],
                        help="the account's projects (an admin key): list, create, rename, "
                             "archive, unarchive, delete")
    act = sp.add_subparsers(dest="action")
    x = act.add_parser("list", parents=[common])
    x.add_argument("--archived", action="store_true", help="archived ones too")
    x = act.add_parser("create", parents=[common])
    x.add_argument("name")
    x.add_argument("--region", choices=["eu", "global"],
                   help="where its memories are read by a model (default: the install's)")
    x.add_argument("--description")
    x = act.add_parser("rename", parents=[common])
    x.add_argument("project", help="id or name")
    x.add_argument("new_name")
    for name in ("archive", "unarchive"):
        x = act.add_parser(name, parents=[common])
        x.add_argument("project", help="id or name")
    x = act.add_parser("delete", parents=[common])
    x.add_argument("project", help="id or name")
    x.add_argument("--erase", action="store_true",
                   help="erase its memories and facts instead of moving them to the default project")
    x.add_argument("--yes", action="store_true", help="confirm")
    sp.set_defaults(func=cmd_projects, archived=False)

    sp = sub.add_parser("keys", parents=[common],
                        help="the account's API keys (an admin key): list, create, move, revoke")
    act = sp.add_subparsers(dest="action")
    act.add_parser("list", parents=[common])
    x = act.add_parser("create", parents=[common])
    x.add_argument("--name", required=True)
    x.add_argument("--project", help="id or name (default: the default project)")
    x.add_argument("--scopes", help="read,write (the default), read, write, admin")
    x.add_argument("--users",
                   help="confine it to these end users: founder,repo:* (a trailing * is a prefix)")
    x.add_argument("--expires", help="days, or never (default: the server's)")
    x.add_argument("--save-as", metavar="PROFILE",
                   help="save the new key in this profile instead of printing it")
    x = act.add_parser("move", parents=[common])
    x.add_argument("key_id")
    x.add_argument("--project", required=True, help="id or name")
    x = act.add_parser("revoke", parents=[common])
    x.add_argument("key_id")
    x.add_argument("--yes", action="store_true", help="confirm")
    sp.set_defaults(func=cmd_keys)

    sp = sub.add_parser("delete-account", parents=[common],
                        help="delete this key's account for good (Cloud, `init --agent` accounts; "
                             "needs --yes)")
    sp.add_argument("--yes", action="store_true", help="confirm: the account and its keys go")
    sp.set_defaults(func=cmd_delete_account)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # `init` is the one command that runs WITHOUT a key: it mints one.
    if getattr(args, "command", None) == "init":
        return cmd_init(args)
    if getattr(args, "command", None) == "hook":
        # A hook finds its key itself, and never fails the assistant for want of one.
        return cmd_hook(args)
    key = _resolve_key(args)
    if not key:
        print("error: no API key. Run `korely init --agent` to get one free, "
              "or set KORELY_API_KEY (kor_live_...).", file=sys.stderr)
        return 2
    try:
        client = Korely(api_key=key, base_url=_resolve_base_url(args))
    except KorelyError as e:
        # Every refusal here used to print "no API key", including the one the
        # constructor exists for: a kor_self_ key with no address, or a
        # kor_live_ key pointed at somebody else's server. The key was there;
        # the explanation of what was wrong with it was thrown away.
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        return args.func(client, args)
    except KorelyError as e:
        code = getattr(e, "code", None)
        if getattr(args, "json", False):
            # The docs: with --json an error is a machine-readable object with
            # a `code`, on stderr (2026-09-29: it was the plain line).
            print(json.dumps({"code": code or "error", "message": str(e),
                              "status": getattr(e, "status", None)}, ensure_ascii=False),
                  file=sys.stderr)
        else:
            print(f"error: {e}" + (f" [{code}]" if code else ""), file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
