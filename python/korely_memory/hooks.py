"""One memory for several coding assistants: `korely hook` and `korely init --tool`.

Claude Code, Codex and Cursor run a command at the moments of a session that
matter to a memory: when it starts, when the developer sends a prompt, when the
assistant finishes a turn, before the conversation is compacted, when it ends.
The three read the same kind of hook (a command, JSON on stdin, JSON or text on
stdout), so one command serves them: `korely hook <event> --tool <tool>`.

What it does, and no more:

- at the start, and once more on the first prompt with the prompt as the
  query, it prints the context Korely holds for this repository, which the
  assistant reads before its first answer;
- it keeps each prompt and each final answer in a local buffer
  (~/.korely/sessions), and every few turns, before a compaction and at the
  end of the session it sends them to Korely as ONE memory, from a process of
  its own, so the assistant never waits for the network;
- before anything leaves the machine it masks what looks like a secret
  (`redact`).

The memory of a repository is one `user_id`, `repo:<name>`, the same whichever
assistant wrote it: what Claude Code learnt on Monday, Codex reads on Tuesday.
A user_id costs nothing against the agent cap, which counts `agent_id`s.

A hook never breaks the assistant: every failure is a line in
~/.korely/hook.log (never the text of the session) and an exit code 0. A
read that fails is also one line in what the assistant is handed, in the
place of the memory it could not read, and a key that ends within
KEY_WARN_DAYS is one line too: an empty memory is never how either reaches
the session.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ._files import write_private

TOOLS = ("claude-code", "codex", "cursor")
EVENTS = ("session-start", "prompt", "stop", "pre-compact", "session-end")
TOOL_NAMES = {"claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor"}

# Turns kept in the buffer before they are sent as one memory. Five exchanges
# of a working session are one write: a day of forty exchanges is eight, which
# the free plan (250 writes a month) holds for a month of weekdays.
DEFAULT_EVERY = 5
# The API refuses a memory longer than 16,000 characters (the server's own
# limit); the extractor reads all of it, in
# windows. One message keeps at most this much, its start and its end, where a
# reply says what it did and what it decided.
MESSAGE_CHARS = 3000
MEMORY_CHARS = 15000
# The context the assistant is handed. Claude Code reads up to 10,000
# characters of a SessionStart hook's output; 1,500 tokens stay under it.
START_BUDGET = 1500
PROMPT_BUDGET = 1200
# A profile read next to the repository's memory (`--also-read`): the two stay
# under Codex's ~2,500 tokens of hook context and Claude Code's 10,000
# characters.
EXTRA_BUDGET = 700
# A session file nobody touched for this long belongs to a session that ended
# without saying so (a closed terminal): the next session start sends it.
STALE_SECONDS = 2 * 3600
NETWORK_TIMEOUT = 8.0
# The reads of one hook share NETWORK_TIMEOUT seconds, not one each: the
# assistant gives the hook 10 s (TIMEOUTS), and a start that also reads a
# user_id and a profile made three reads in turn, each allowed 8 s, so a slow
# server kept it 24 s and Claude Code cut it off, the context of all three
# lost. A read with less than MIN_READ left is not begun.
MIN_READ = 1.0
_monotonic = time.monotonic
# How long before the key ends the context says so: the server's own horizon
# for the `key.expiring` webhook (14 days), so an agent with no webhook, which
# is most of them, hears it when one would. Korely 1.0.23 and later say the
# end on every answer (Korely.key_expires_at); after it every call is a 401.
KEY_WARN_DAYS = 14
# The words of a failed read the context keeps, start and end: enough for
# the kind and the server's code, never a page of an HTML error.
REASON_CHARS = 300


# ── where things live ────────────────────────────────────────────────────────
def _home() -> Path:
    from .cli import _config_path

    return _config_path().parent


def _sessions() -> Path:
    p = _home() / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p, 0o700)  # the buffer holds what was said: owner only
    except OSError:
        pass
    return p


def _log(message: str) -> None:
    """One line in ~/.korely/hook.log: what failed, never what was said."""
    try:
        path = _home() / "hook.log"
        if path.exists() and path.stat().st_size > 256_000:
            path.write_text("", encoding="utf-8")
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}\n")
    except OSError:
        pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── the scope: one repository, whichever assistant ───────────────────────────
def _git(cwd: str, *args: str) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                             timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value if out.returncode == 0 and value else None


def repo_name(cwd: str) -> str:
    """The name of the repository `cwd` is in, the same from any worktree.

    `--git-common-dir` is the main checkout's `.git` from a worktree too: a
    session in `.claude/worktrees/feature` belongs to the repository, not to a
    project called "feature". Outside git, the folder's name.
    """
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common:
        p = Path(common)
        name = (p.parent if p.name == ".git" else p).name
        if name:
            return name
    top = _git(cwd, "rev-parse", "--show-toplevel")
    return Path(top or cwd).resolve().name or "workspace"


def scope_for(cwd: str, fixed: Optional[str] = None) -> str:
    """The user_id of the memory: `fixed` when given, else `repo:<name>`.

    A user_id is one URL path segment (DELETE /v1/users/<user_id>/memories):
    no '/', at most 255 characters (the server's rule for an id).
    """
    if fixed:
        return fixed
    name = repo_name(cwd).replace("/", "-")
    return f"repo:{name}"[:255]


# ── what never leaves the machine ────────────────────────────────────────────
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                  re.S)
_URL_CREDENTIALS = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@")
_RUN = re.compile(r"[A-Za-z0-9_\-+=]{24,}")


def _entropy(s: str) -> float:
    counts: Dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


# A hex token: digits and the letters a-f of one case, `0x` allowed in front.
_HEX = re.compile(r"(?:0[xX])?([0-9a-f]+|[0-9A-F]+)")
# What joins the pieces of a run: `name=value`, `prefix_value`, `a-b`.
_JOINS = re.compile(r"[_\-+=]")


def _spread(text: str, symbols: int) -> bool:
    """Many different characters, for an alphabet of `symbols`: a random
    string of n characters scores close to log2(min(n, symbols)) bits per
    character (Shannon's entropy), words and identifiers well under it."""
    return _entropy(text) >= 0.8 * math.log2(min(len(text), symbols))


def _random_hex(piece: str) -> bool:
    """A hex token of at least 24 digits (96 bits), digits and letters
    mixed and spread over the 16 symbols."""
    m = _HEX.fullmatch(piece)
    digits = m.group(1) if m else ""
    return (len(digits) >= 24 and bool(re.search(r"[0-9]", digits))
            and bool(re.search(r"[a-fA-F]", digits)) and _spread(digits, 16))


def _looks_random(run: str) -> bool:
    """A run that reads like a generated key, by its shape alone.

    No list of vendors' prefixes: a key from a vendor nobody listed is masked
    the same way. Random keys mix upper case, lower case and digits and use
    many different characters; identifiers and file names do not do all of
    it. A hex token has one case and 16 symbols: it is masked when the run,
    or a piece of it (`TOKEN=<hex>`, `prefix_<hex>`), is one. By its shape a
    hex key cannot be told from a hex identifier (a full git commit hash, a
    sha256 or md5 digest, an ObjectId, a UUID written without dashes), so
    those are masked too; the pieces of a UUID with dashes are too short,
    and the 7 to 12 characters git shows of a hash are under the 24 of a run.
    What this does not catch is listed in the README ("Not caught").
    """
    core = run.strip("=")
    if any(_random_hex(piece) for piece in [core, *_JOINS.split(core)]):
        return True
    if not (re.search(r"[a-z]", core) and re.search(r"[A-Z]", core)
            and re.search(r"[0-9]", core)):
        return False
    # Over ~64 symbols: upper case, lower case, digits and a few signs.
    return _spread(core, 64)


def redact(text: str) -> str:
    """The text with private keys, credentials in URLs and generated-looking
    keys replaced by `[redacted]`. It masks the obvious, by shape: a short
    password typed in a sentence still reads as a word."""
    text = _PEM.sub("[redacted private key]", text)
    text = _URL_CREDENTIALS.sub(r"\1[redacted]@", text)
    return _RUN.sub(lambda m: "[redacted]" if _looks_random(m.group(0)) else m.group(0), text)


def _clip(text: str, limit: int = MESSAGE_CHARS) -> str:
    """At most `limit` characters: the start and the end, the middle marked."""
    text = text.strip()
    if len(text) <= limit:
        return text
    half = (limit - 20) // 2
    return text[:half].rstrip() + "\n[...]\n" + text[-half:].lstrip()


# ── reading what a tool sends ────────────────────────────────────────────────
def _text_of(content: Any) -> str:
    """The text of a transcript message: a string, or a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") in ("text", "output_text", "input_text"):
                parts.append(p.get("text") or "")
            elif isinstance(p, str):
                parts.append(p)
        return "\n".join(x for x in parts if x)
    return ""


def last_reply_from_transcript(path: Optional[str]) -> str:
    """The last assistant text in a JSON Lines transcript, for a tool whose
    stop event carries only the transcript's path. Reads the last 512 KB."""
    if not path:
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 512_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        msg = row.get("message") if isinstance(row.get("message"), dict) else row
        role = msg.get("role") or row.get("type") or row.get("role")
        if role == "assistant":
            text = _text_of(msg.get("content"))
            if text.strip():
                return text
    return ""


class Event:
    """What a hook was told, read the same way for the three tools."""

    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self.session = str(data.get("session_id") or data.get("conversation_id")
                           or data.get("sessionId") or "")
        roots = data.get("workspace_roots") or []
        self.cwd = str(data.get("cwd") or (roots[0] if roots else "") or os.getcwd())
        self.prompt = str(data.get("prompt") or "")
        self.transcript = data.get("transcript_path")

    def reply(self) -> str:
        for k in ("last_assistant_message", "text", "response"):
            v = self.data.get(k)
            if isinstance(v, str) and v.strip():
                return v
        return last_reply_from_transcript(self.transcript)


# ── the buffer ───────────────────────────────────────────────────────────────
def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:120] or "session"


class _Lock:
    """An exclusive lock on a file, where the platform has one (POSIX)."""

    def __init__(self, path: Path):
        self.path = path
        self.fd = None

    def __enter__(self):
        try:
            import fcntl

            self.fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except (ImportError, OSError):
            self.fd = None
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)


def _session_file(session: str) -> Path:
    return _sessions() / f"{_safe(session)}.json"


def _read(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: Dict[str, Any]) -> None:
    write_private(path, json.dumps(data, ensure_ascii=False))


def remember_turn(ev: Event, tool: str, scope: str, role: str, text: str) -> int:
    """Add one message to the session's buffer; returns the answers not sent yet."""
    text = text.strip()
    path = _session_file(ev.session or "no-session")
    with _Lock(path.with_suffix(".lock")):
        state = _read(path) or {"session": ev.session, "tool": tool, "scope": scope,
                                "repo": repo_name(ev.cwd), "turns": [], "sent": 0}
        state["scope"], state["tool"] = scope, tool
        if text:
            state["turns"].append({"role": role, "text": _clip(redact(text)), "at": _now()})
        state["touched"] = time.time()
        _write(path, state)
        return sum(1 for t in state["turns"][state["sent"]:] if t["role"] == "assistant")


def _claim(path: Path) -> Optional[Path]:
    """Move the turns not sent yet to an outbox file, under the lock, so two
    senders never send the same turns twice. None when there is nothing."""
    with _Lock(path.with_suffix(".lock")):
        state = _read(path)
        turns = state.get("turns", [])[state.get("sent", 0):]
        if not turns:
            return None
        box = path.with_name(f"{path.stem}.out{int(time.time() * 1000)}.json")
        out = {k: state.get(k) for k in ("session", "tool", "scope", "repo")}
        out["turns"] = turns
        _write(box, out)
        state["sent"] = len(state["turns"])
        _write(path, state)
        return box


def memory_text(box: Dict[str, Any]) -> str:
    """The buffered turns as one memory: who, where, when, then the dialogue."""
    turns = box.get("turns") or []
    day = (turns[0]["at"] if turns else _now())[:10]
    tool = TOOL_NAMES.get(box.get("tool") or "", box.get("tool") or "a coding assistant")
    head = f"A coding session in the repository {box.get('repo') or '?'}, with {tool}, on {day}."
    lines = [head, ""]
    for t in turns:
        who = "Developer" if t["role"] == "user" else "Assistant"
        lines.append(f"{who}: {t['text']}")
        lines.append("")
    text = "\n".join(lines).strip()
    return _clip(text, MEMORY_CHARS)


def send_outbox(box_path: Path, client_factory: Callable[[Optional[str]], Any]) -> bool:
    """Send one outbox file as one memory; delete it once Korely has it.

    The file is renamed to `.sending` first: a rename succeeds for one
    process only, so two senders started together (an answer and a
    compaction) never send the same turns twice."""
    work = box_path.with_name(box_path.name[: -len(".json")] + ".sending")
    try:
        os.rename(box_path, work)
    except OSError:
        return False  # another sender has it
    box = _read(work)
    turns = box.get("turns") or []
    if not turns:
        work.unlink(missing_ok=True)
        return True
    try:
        client = client_factory(box.get("tool"))
        _save_the_session(client, memory_text(box), user_id=box.get("scope"),
                          run_id=(box.get("session") or None) and str(box["session"])[:255],
                          metadata={"source": box.get("tool"), "repo": box.get("repo")},
                          timestamp=turns[0]["at"])
    except Exception as e:  # noqa: BLE001 - a hook must never raise
        attempts = int(box.get("attempts", 0)) + 1
        if attempts >= 5:
            _log(f"gave up on {box_path.name} after {attempts} attempts: {type(e).__name__}: {e}")
        else:
            box["attempts"] = attempts
            _write(box_path, box)
            _log(f"send failed ({attempts}/5) {box_path.name}: {type(e).__name__}: {e}")
        work.unlink(missing_ok=True)
        return False
    work.unlink(missing_ok=True)
    return True


def _save_the_session(client, text: str, **kw) -> None:
    """The session, saved as what it is: `kind="session"`, so a read can
    leave the work sessions out (`exclude_sessions`; Korely Agent after
    0.1.41, the founder's yes to question 16.1 of 18 Oct 2026). A server that
    does not know the field refuses it (422, the body takes no unknown field):
    then it is saved as before, a note, rather than lost."""
    from .exceptions import APIError

    try:
        client.add(text, kind="session", **kw)
    except APIError as e:
        if getattr(e, "status", None) != 422 or "kind" not in str(e):
            raise
        client.add(text, **kw)


def flush(session: Optional[str], client_factory: Callable[[Optional[str]], Any]) -> int:
    """Claim and send what one session (or, with None, every stale session and
    every outbox left by a failed send) has not sent yet. Returns memories sent.
    `client_factory(tool)` makes the client, told which tool filled the outbox."""
    sent = 0
    root = _sessions()
    paths: List[Path] = []
    if session:
        paths = [_session_file(session)]
    else:
        now = time.time()
        for p in root.glob("*.json"):
            if ".out" in p.name:
                continue
            if now - float(_read(p).get("touched", 0)) > STALE_SECONDS:
                paths.append(p)
        # A sender killed in the middle of a send left its file `.sending`.
        for p in root.glob("*.out*.sending"):
            if now - p.stat().st_mtime > 600:
                os.replace(p, p.with_name(p.name[: -len(".sending")] + ".json"))
    for p in paths:
        if p.exists():
            _claim(p)
    prefix = f"{_safe(session)}.out" if session else ""
    for box in sorted(root.glob("*.out*.json")):
        if box.name.startswith(prefix):
            sent += 1 if send_outbox(box, client_factory) else 0
    if session:
        forget_ended(session)
    if not session:
        # A stale session sent in full has nothing left to keep.
        for p in paths:
            state = _read(p)
            if state and state.get("sent", 0) >= len(state.get("turns", [])):
                for q in (p, p.with_suffix(".lock")):
                    q.unlink(missing_ok=True)
    return sent


def flush_in_background(session: Optional[str]) -> None:
    """Send from a process of its own: Claude Code gives SessionEnd 1.5 s, and
    no hook should make the assistant wait for the network."""
    cmd = [sys.executable, "-m", "korely_memory.cli", "hook", "flush"]
    if session:
        cmd += ["--session", session]
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True,
                         env={**os.environ, "PYTHONPATH": os.pathsep.join(
                             [str(Path(__file__).resolve().parent.parent)]
                             + ([os.environ["PYTHONPATH"]] if os.environ.get("PYTHONPATH")
                                else []))})
    except OSError as e:
        _log(f"could not start the sender: {e}")


# ── the hook itself ──────────────────────────────────────────────────────────
# The event names each tool uses for the five moments, and which of them can
# hand the model a context. Cursor's `stop` carries no text, so the answer is
# read at `afterAgentResponse` (cursor.com/docs/hooks, 15 Oct 2026).
NATIVE = {
    "claude-code": {"session-start": "SessionStart", "prompt": "UserPromptSubmit",
                    "stop": "Stop", "pre-compact": "PreCompact", "session-end": "SessionEnd"},
    "codex": {"session-start": "SessionStart", "prompt": "UserPromptSubmit",
              "stop": "Stop", "pre-compact": "PreCompact", "session-end": "SessionEnd"},
    "cursor": {"session-start": "sessionStart", "prompt": "beforeSubmitPrompt",
               "stop": "afterAgentResponse", "pre-compact": "preCompact",
               "session-end": "sessionEnd"},
}


def _context_output(tool: str, event: str, text: str) -> str:
    """What the tool reads as extra context for the model."""
    if tool == "cursor":
        # Top-level and snake_case; sessionStart only (beforeSubmitPrompt can
        # answer `continue` and `user_message`, nothing for the model).
        return json.dumps({"additional_context": text}) if event == "session-start" else ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": NATIVE[tool][event],
                                              "additionalContext": text}})


def _quiet_output(tool: str, event: str) -> str:
    """What says "nothing to add, go on". Claude Code and Codex: no output at
    all (Codex refuses plain text after Stop). Cursor: a JSON answer, with
    `continue` where the prompt is waiting for one."""
    if tool != "cursor":
        return ""
    return json.dumps({"continue": True}) if event == "prompt" else "{}"


def _block(client: Any, query: str, budget: int, user_id: Optional[str], heading: str,
           deadline: Optional[float] = None) -> str:
    """One context block under its heading, or "" when Korely holds nothing.
    With a `deadline` (time.monotonic), the read is given what is left of it
    as the client's timeout. That timeout bounds each wait for the server,
    not the whole reply: a server that keeps sending a few bytes at a time
    holds the read longer (12.2 s measured against a deadline of 8 s)."""
    if deadline is not None:
        left = deadline - _monotonic()
        if left < MIN_READ:
            raise TimeoutError("the hook's time for reading is spent")
        client.timeout = min(getattr(client, "timeout", left), left)
    ctx = client.get_context(query=query[:2000], user_id=user_id, token_budget=budget)
    body = (getattr(ctx, "context", "") or "").strip()
    if not body or not getattr(ctx, "sources", None):
        return ""
    return f"{heading}\n\n{body}"


def _shown(path: Path) -> str:
    """A path as the developer would type it: the home folder as `~`."""
    home, text = str(Path.home()), str(path)
    return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text


def _not_read(what: str, error: BaseException) -> str:
    """The line in the place of a block that could not be read. Left out, a
    read that failed (a timeout, a 401 of a key that ended, no key at all)
    handed the assistant nothing, as an empty memory does: it went on
    without knowing that the memory was there and unread, and only
    hook.log knew. The error's kind and words, masked as a session's text
    is (`redact`) and kept to REASON_CHARS on one line."""
    reason = " ".join(_clip(redact(f"{type(error).__name__}: {error}"), REASON_CHARS).split())
    # Said of this context, not of the session: on the first prompt the
    # start's blocks may already be there.
    return (f"Korely memory of {what} was not read ({reason}): it is missing from this "
            f"context. {_shown(_home() / 'hook.log')} has the line.")


def _moment(value: Any) -> Optional[datetime]:
    """An ISO 8601 moment in UTC (no offset: UTC), or None, also for one
    that UTC cannot hold: `0001-01-01T00:30:00+01:00` is a year 0, and its
    OverflowError, raised after the reads, took the blocks already read
    out of the hook's answer."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        at = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return (at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)) \
            .astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _key_ends(client: Any, whose: str) -> str:
    """One line when the key `client` sends ends within KEY_WARN_DAYS, as
    the server's last answer said (`Korely.key_expires_at`); "" for a key
    that never ends, one further off, a server that does not say, and an
    end that cannot be read.

    What to do names where the client found the key (`Korely._key_profile`):
    a profile's saved key is replaced by `korely init --force` with that
    profile, KORELY_PROFILE's included, which `korely init` does not read;
    a key from KORELY_API_KEY (the hooks pass none as an argument) by the
    variable, which `korely init` does not write. A `korely init --force`
    of the other profile would have replaced a key that does not end, lost
    for good, and left this one in place. On the Cloud, the key of an
    account `korely init --agent` made and nobody adopted lasts 7 days, and
    signing in to the dashboard with it lifts that end: no new key."""
    ends = _moment(getattr(client, "key_expires_at", None))
    if ends is None:
        return ""
    left = ends - datetime.now(timezone.utc)
    if left > timedelta(days=KEY_WARN_DAYS):
        return ""
    days = left.days
    when = "in less than a day" if days < 1 else f"in {days} day{'' if days == 1 else 's'}"
    from .client import _REGIONS, _is_ours as _the_service

    server = str(getattr(client, "base_url", "") or "")
    saved = getattr(client, "_key_profile", None)
    if saved is None:
        how = ("put it in KORELY_API_KEY, where these hooks read this one (`korely init` "
               "does not change that variable)")
    else:
        # `korely init --api-key` saves the server of --base-url, else of
        # KORELY_BASE_URL, else the Cloud's: any other server is named, or
        # init refuses a key of one's own server (`_refuse_a_mismatched_pair`).
        # Of an address only a user:password is masked: `redact` would also
        # take a long host name for a key, and the command would save that.
        named = _URL_CREDENTIALS.sub(r"\1[redacted]@", server)
        how = ("save it with `korely init --api-key <new key> --force"
               + ("" if server == _REGIONS["eu"] else f" --base-url {named}")
               + ("" if saved == "default" else f" --profile {saved}") + "`")
    trial = (" A key `korely init --agent` made, whose account nobody has signed in to yet, "
             "needs no new key: signing in to the Korely dashboard with it lifts that end."
             if _the_service(server) else "")
    return (f"The Korely key {whose} ends on {ends:%Y-%m-%d %H:%M} UTC ({when}): after that "
            f"Korely refuses its reads and writes (401). Make a new key in the Korely "
            f"dashboard and {how}.{trial}")


def _context_text(client_factory: Callable[..., Any], scope: str, repo: str, query: str,
                  budget: int, also_read: Sequence[str] = (),
                  also_read_user: Sequence[str] = ()) -> str:
    """The repository's memory, then each user_id of `also_read_user` in the
    same project (a memory that spans repositories, e.g. `founder`), then the
    whole project of each profile in `also_read` (a private project next to
    the shared one), each block under its own heading. A block that cannot be
    read, the repository's included, is a line in hook.log and, in its place,
    a line that says so (`_not_read`), never the end of the rest: the
    session starts with every block that could be read. Last, one line for
    each key that ends within KEY_WARN_DAYS (`_key_ends`). The reads share
    NETWORK_TIMEOUT seconds; one with less than MIN_READ left is not begun
    (see `_block` for a reply that keeps arriving slowly)."""
    deadline = _monotonic() + NETWORK_TIMEOUT
    made: Dict[Optional[str], Any] = {}

    def client_of(profile: Optional[str]) -> Callable[[], Any]:
        # Made once per profile (None the default one, for the repository
        # and the user_ids): a factory that fails fails each read of that
        # profile, and the others still go.
        def get() -> Any:
            if profile not in made:
                made[profile] = client_factory(profile) if profile else client_factory()
            return made[profile]
        return get

    reads = [(f"user_id {scope}", client_of(None), budget, scope,
              f"Korely memory of the repository {repo} (user_id {scope}), what earlier "
              f"sessions stored, whichever assistant they ran in:")]
    reads += [(f"user_id {user}", client_of(None), EXTRA_BUDGET, user,
               f"Korely memory of user_id {user} (across repositories):")
              for user in also_read_user if user != scope]
    reads += [(f"profile {name}", client_of(name), EXTRA_BUDGET, None,
               f"Korely memory of the profile {name} (its whole project):")
              for name in also_read]
    blocks = []
    for what, client, size, user_id, heading in reads:
        try:
            blocks.append(_block(client(), query, size, user_id, heading, deadline))
        except Exception as e:  # noqa: BLE001 - the other blocks still go
            _log(f"{what}: {type(e).__name__}: {e}")
            blocks.append(_not_read(what, e))
    blocks += [_key_ends(c, "these hooks use" if p is None else f"of the profile {p}")
               for p, c in made.items()]
    return "\n\n".join(b for b in blocks if b)


def _primed(session: str) -> bool:
    """Whether this session was already handed the context of a prompt; marks it."""
    path = _session_file(session or "no-session")
    with _Lock(path.with_suffix(".lock")):
        state = _read(path)
        if state.get("primed"):
            return True
        if state:
            state["primed"] = True
            _write(path, state)
        else:
            _write(path, {"session": session, "turns": [], "sent": 0, "primed": True,
                          "touched": time.time()})
        return False


def run_hook(event: str, tool: str, data: Dict[str, Any], *, every: int = DEFAULT_EVERY,
             fixed_scope: Optional[str] = None,
             client_factory: Optional[Callable[..., Any]] = None,
             background: Callable[[Optional[str]], None] = flush_in_background,
             also_read: Sequence[str] = (), also_read_user: Sequence[str] = ()) -> str:
    """Handle one event; return what to print on stdout. Never raises."""
    quiet = _quiet_output(tool, event)
    try:
        if tool == "claude-code" and os.environ.get("CURSOR_VERSION"):
            # Cursor runs Claude Code's hook files too, on by default
            # (cursor.com/docs/reference/third-party-hooks): without this,
            # with both installed, every turn would be stored twice. Cursor
            # has its own hooks (`korely init --tool cursor`).
            return quiet
        if client_factory is None:
            from .client import Korely

            def client_factory(profile: Optional[str] = None) -> Any:
                return Korely(timeout=NETWORK_TIMEOUT, client=tool, profile=profile)
        ev = Event(data)
        scope = scope_for(ev.cwd, fixed_scope)
        repo = repo_name(ev.cwd)

        if event == "session-start":
            background(None)  # what sessions that ended without a word left behind
            text = _context_text(client_factory, scope, repo,
                                 f"{repo}: decisions, rules, open work and next steps",
                                 START_BUDGET, also_read, also_read_user)
            return _context_output(tool, event, text) if text else quiet

        if event == "prompt":
            remember_turn(ev, tool, scope, "user", ev.prompt)
            if tool == "cursor" or not ev.prompt.strip() or _primed(ev.session):
                return quiet
            # Once per session, the context of what the developer asked: the
            # start could only guess at a query.
            text = _context_text(client_factory, scope, repo, ev.prompt, PROMPT_BUDGET,
                                 also_read, also_read_user)
            return _context_output(tool, event, text) if text else quiet

        if event == "stop":
            if remember_turn(ev, tool, scope, "assistant", ev.reply()) >= max(1, every):
                background(ev.session or "no-session")
            return quiet

        if event == "pre-compact":
            background(ev.session or "no-session")
            return quiet

        if event == "session-end":
            path = _session_file(ev.session or "no-session")
            with _Lock(path.with_suffix(".lock")):
                state = _read(path)
                if state:
                    state["ended"] = True
                    _write(path, state)
            background(ev.session or "no-session")
            return quiet
    except Exception as e:  # noqa: BLE001 - a hook must never break the assistant
        _log(f"{tool} {event}: {type(e).__name__}: {e}")
    return quiet


def forget_ended(session: str) -> None:
    """After a flush: an ended session sent in full leaves no file behind."""
    path = _session_file(session)
    with _Lock(path.with_suffix(".lock")):
        state = _read(path)
        if state and state.get("ended") and state.get("sent", 0) >= len(state.get("turns", [])):
            path.unlink(missing_ok=True)
    path.with_suffix(".lock").unlink(missing_ok=True)


# ── installing: `korely init --tool` ─────────────────────────────────────────
# Seconds each hook may take. The start and the first prompt wait for one read
# (NETWORK_TIMEOUT 8 s); the others only write a local file and start the
# sender. Codex allows SessionEnd at most 3 s; Claude Code gives it 1.5 s
# unless the hook asks for more (code.claude.com/docs/en/hooks).
TIMEOUTS = {"session-start": 10, "prompt": 10, "stop": 10, "pre-compact": 10,
            "session-end": 3}
MATCHERS = {"claude-code": {"session-start": "startup|resume|clear|compact"},
            "codex": {"session-start": "startup|resume|clear|compact"}}


def korely_command() -> str:
    """How a hook calls this CLI: the absolute path of `korely`, because an
    editor started from the Dock does not have the shell's PATH."""
    import shlex
    import shutil

    found = shutil.which("korely")
    if found:
        return shlex.quote(str(Path(found).resolve()))
    return f"{shlex.quote(sys.executable)} -m korely_memory.cli"


def hook_command(base: str, event: str, tool: str, scope: Optional[str] = None,
                 also_read: Sequence[str] = (), also_read_user: Sequence[str] = ()) -> str:
    import shlex

    cmd = f"{base} hook {event} --tool {tool}"
    if scope:
        cmd += f" --user-id {shlex.quote(scope)}"
    if event in ("session-start", "prompt"):  # the events that read
        cmd += "".join(f" --also-read-user {shlex.quote(u)}" for u in also_read_user)
        cmd += "".join(f" --also-read {shlex.quote(p)}" for p in also_read)
    return cmd


def _is_ours(command: Any, tool: str) -> bool:
    return (isinstance(command, str) and " hook " in command
            and f"--tool {tool}" in command and "korely" in command)


def hook_files(tool: str, project: Path, everywhere: bool) -> Dict[str, Path]:
    home = Path.home()
    if tool == "claude-code":
        # settings.local.json: the personal file of a project, never shared.
        return {"hooks": home / ".claude" / "settings.json" if everywhere
                else project / ".claude" / "settings.local.json"}
    if tool == "codex":
        return {"hooks": (home if everywhere else project) / ".codex" / "hooks.json",
                "mcp": (home if everywhere else project) / ".codex" / "config.toml"}
    return {"hooks": (home if everywhere else project) / ".cursor" / "hooks.json",
            "mcp": (home if everywhere else project) / ".cursor" / "mcp.json"}


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)  # a file we cannot read is not one we rewrite
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def merge_hooks(settings: Dict[str, Any], tool: str, commands: Dict[str, str]) -> Dict[str, Any]:
    """`settings` with this tool's korely hooks replaced by `commands`
    (event -> command; empty to remove them). Everything else stays."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    for name, groups in list(hooks.items()):
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            if tool == "cursor":
                if isinstance(g, dict) and _is_ours(g.get("command"), tool):
                    continue
                kept.append(g)
                continue
            if isinstance(g, dict) and isinstance(g.get("hooks"), list):
                inner = [h for h in g["hooks"]
                         if not (isinstance(h, dict) and _is_ours(h.get("command"), tool))]
                if not inner:
                    continue
                g = {**g, "hooks": inner}
            kept.append(g)
        if kept:
            hooks[name] = kept
        else:
            del hooks[name]
    for event, command in commands.items():
        native = NATIVE[tool][event]
        if tool == "cursor":
            hooks.setdefault(native, []).append({"command": command,
                                                 "timeout": TIMEOUTS[event]})
            continue
        group: Dict[str, Any] = {}
        matcher = MATCHERS.get(tool, {}).get(event)
        if matcher:
            group["matcher"] = matcher
        group["hooks"] = [{"type": "command", "command": command,
                           "timeout": TIMEOUTS[event]}]
        hooks.setdefault(native, []).append(group)
    out = dict(settings)
    if hooks:
        out["hooks"] = hooks
    else:
        out.pop("hooks", None)
    if tool == "cursor" and hooks:
        out.setdefault("version", 1)
    return out


def merge_mcp_json(config: Dict[str, Any], command: Optional[List[str]],
                   name: str = "korely") -> Dict[str, Any]:
    """`config` with the server `name` set to `command`, or removed (None)."""
    servers = config.get("mcpServers")
    servers = dict(servers) if isinstance(servers, dict) else {}
    if command:
        servers[name] = {"type": "stdio", "command": command[0], "args": command[1:]}
    else:
        servers.pop(name, None)
    out = dict(config)
    if servers:
        out["mcpServers"] = servers
    else:
        out.pop("mcpServers", None)
    return out


def merge_mcp_toml(text: str, command: Optional[List[str]], name: str = "korely") -> str:
    """Codex's config.toml with a `[mcp_servers.<name>]` table set or removed.
    Plain text: the standard library has no TOML writer, and every other line
    of the file stays as the developer wrote it."""
    table_re = re.compile(rf"(?ms)^\[mcp_servers\.{re.escape(name)}\]\n.*?(?=^\[|\Z)")
    text = table_re.sub("", text).rstrip("\n")
    if command:
        args = ", ".join(json.dumps(a) for a in command[1:])
        table = (f"[mcp_servers.{name}]\ncommand = {json.dumps(command[0])}\n"
                 f"args = [{args}]\n")
        text = (text + "\n\n" if text else "") + table
    return text + ("\n" if text and not text.endswith("\n") else "")


def _tracked(path: Path, project: Path) -> bool:
    return _git(str(project), "ls-files", "--error-unmatch", str(path)) is not None


def _keep_out_of_git(path: Path, project: Path) -> Optional[str]:
    """Add a personal file to .git/info/exclude (local, never committed),
    unless git ignores it already. Returns what was done, for the report."""
    try:
        out = subprocess.run(["git", "-C", str(project), "check-ignore", "-q", str(path)],
                             capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 1:  # 0: ignored already; 128: not a repository
        return None
    info = _git(str(project), "rev-parse", "--path-format=absolute", "--git-path",
                "info/exclude")
    top = _git(str(project), "rev-parse", "--show-toplevel")
    if not info or not top:
        return None
    rel = os.path.relpath(str(path), top)
    exclude = Path(info)
    exclude.parent.mkdir(parents=True, exist_ok=True)
    old = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if rel in old.splitlines():
        return None
    exclude.write_text(old + ("" if old.endswith("\n") or not old else "\n") + rel + "\n",
                       encoding="utf-8")
    return f"{rel} added to .git/info/exclude"


class InstallError(Exception):
    pass


def install(tool: str, project: Path, *, everywhere: bool = False, remove: bool = False,
            mcp: bool = True, scope: Optional[str] = None,
            base: Optional[str] = None,
            run: Callable[..., Any] = subprocess.run,
            also_read: Sequence[str] = (), also_read_user: Sequence[str] = ()) -> List[str]:
    """Write (or with `remove`, take out) this tool's korely hooks and MCP
    server. `also_read`: profiles the session start also reads, each with an
    MCP server of its own, `korely-<profile>`. Returns one line per change,
    for the report.

    `mcp=False` (`--no-mcp`, hooks only) touches no MCP setting, with
    `remove` too: it neither reads, writes nor removes a server, and the MCP
    file is not its to judge (committed or not) nor to keep out of git. It
    removed a server named `korely` and added none, so a hooks-only install
    deleted the server a developer had added by hand under that name (the
    remote one of the docs). Supermemory's Claude Code plugin registers its
    hooks and ships its MCP declaration inside the plugin, and documents no
    step that edits the servers the developer configured
    (github.com/supermemoryai/claude-supermemory, read 15 Nov 2026)."""
    base = base or korely_command()
    files = hook_files(tool, project, everywhere)
    ours = files if mcp else {"hooks": files["hooks"]}
    report: List[str] = []
    if not everywhere:
        for path in ours.values():
            if path.exists() and _tracked(path, project):
                raise InstallError(
                    f"{path} is committed to the repository: korely does not add personal "
                    f"hooks to a shared file. Use --global (every repository, each with its "
                    f"own memory) or add them by hand.")
    commands = {} if remove else {e: hook_command(base, e, tool, scope, also_read, also_read_user)
                                  for e in EVENTS}
    path = files["hooks"]
    if path.exists() or not remove:
        _save_json(path, merge_hooks(_load_json(path), tool, commands))
        report.append(f"{'removed from' if remove else 'hooks in'} {path}")

    servers = [("korely", _mcp_command(base, scope, also_read_user=also_read_user))]
    servers += [(f"korely-{p}", _mcp_command(base, None, profile=p)) for p in also_read]
    if not mcp:
        report.append("MCP settings not touched (--no-mcp): a korely server already there "
                      "stays as it is")
    for name, command in servers if mcp else ():
        _install_server(tool, files, project, name, None if remove else command,
                        everywhere=everywhere, remove=remove, run=run, report=report)
    if not everywhere and not remove:
        for p in ours.values():
            if p.exists():
                done = _keep_out_of_git(p, project)
                if done:
                    report.append(done)
    return report


def _install_server(tool: str, files: Dict[str, Path], project: Path, name: str,
                    server: Optional[List[str]], *, everywhere: bool, remove: bool,
                    run: Callable[..., Any], report: List[str]) -> None:
    """One MCP server `name` set to `server`, or taken out (None), in the
    tool's own place for it."""
    if tool == "claude-code":
        # Claude Code keeps MCP servers in ~/.claude.json, which it rewrites
        # itself: its own command is the one safe way in.
        where = "user" if everywhere else "local"
        _quiet_run(run, ["claude", "mcp", "remove", "--scope", where, name], project)
        if server:
            cmd = ["claude", "mcp", "add", "--scope", where, name, "--", *server]
            if _quiet_run(run, cmd, project):
                report.append(f"MCP server {name} ({where} scope) added with `claude mcp add`")
            else:
                import shlex

                report.append("MCP server not added (no `claude` command found). Run: "
                              + " ".join(shlex.quote(c) for c in cmd))
        elif remove:
            report.append(f"MCP server {name} removed ({where} scope)")
    elif tool == "cursor":
        mp = files["mcp"]
        if server or mp.exists():
            _save_json(mp, merge_mcp_json(_load_json(mp), server, name))
            if server:
                report.append(f"MCP server {name} in {mp}")
    else:
        mp = files["mcp"]
        old = mp.read_text(encoding="utf-8") if mp.exists() else ""
        new = merge_mcp_toml(old, server, name)
        if new != old and (server or mp.exists()):
            mp.parent.mkdir(parents=True, exist_ok=True)
            mp.write_text(new, encoding="utf-8")
            if server:
                report.append(f"MCP server {name} in {mp}")


def _mcp_command(base: str, scope: Optional[str], profile: Optional[str] = None,
                 also_read_user: Sequence[str] = ()) -> List[str]:
    """The stdio server next to the CLI, scoped like the hooks: `--repo-scope`
    reads and writes `repo:<name>` of the folder the tool starts it in, and
    `--also-read-user` reads the same user_ids the hooks read. With
    `profile`, that profile's key and its whole project (no scope)."""
    import shlex

    parts = shlex.split(base)
    if len(parts) == 1:  # the `korely` script: pip puts `korely-mcp` beside it
        cmd = [str(Path(parts[0]).with_name("korely-mcp"))]
    else:  # python -m korely_memory.cli
        cmd = [parts[0], "-m", "korely_memory.mcp_server"]
    if profile:
        return cmd + ["--profile", profile]
    return (cmd + (["--user-id", scope] if scope else ["--repo-scope"])
            + [arg for u in also_read_user for arg in ("--also-read-user", u)])


def _quiet_run(run: Callable[..., Any], cmd: List[str], cwd: Path) -> bool:
    try:
        out = run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(out, "returncode", 1) == 0
