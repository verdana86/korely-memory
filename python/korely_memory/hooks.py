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
~/.korely/hook.log (never the text of the session) and an exit code 0.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

TOOLS = ("claude-code", "codex", "cursor")
EVENTS = ("session-start", "prompt", "stop", "pre-compact", "session-end")
TOOL_NAMES = {"claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor"}

# Turns kept in the buffer before they are sent as one memory. Five exchanges
# of a working session are one write: a day of forty exchanges is eight, which
# the free plan (250 writes a month) holds for a month of weekdays.
DEFAULT_EVERY = 5
# The API refuses a memory longer than 16,000 characters
# (korely-agent `limits.MAX_CONTENT_CHARS`); the extractor reads all of it, in
# windows. One message keeps at most this much, its start and its end, where a
# reply says what it did and what it decided.
MESSAGE_CHARS = 3000
MEMORY_CHARS = 15000
# The context the assistant is handed. Claude Code reads up to 10,000
# characters of a SessionStart hook's output; 1,500 tokens stay under it.
START_BUDGET = 1500
PROMPT_BUDGET = 1200
# A session file nobody touched for this long belongs to a session that ended
# without saying so (a closed terminal): the next session start sends it.
STALE_SECONDS = 2 * 3600
NETWORK_TIMEOUT = 8.0


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
    no '/', at most 255 characters (korely-agent `services.ids`).
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


def _looks_random(run: str) -> bool:
    """A run that reads like a generated key, by its shape alone.

    No list of vendors' prefixes: a key from a vendor nobody listed is masked
    the same way. Random keys mix upper case, lower case and digits and use
    many different characters; identifiers, file names, hashes and UUIDs do
    not do all of it (a git hash and a UUID have no upper case). Shannon's
    entropy, in bits per character, measures "many different characters".
    """
    core = run.strip("=")
    if not (re.search(r"[a-z]", core) and re.search(r"[A-Z]", core)
            and re.search(r"[0-9]", core)):
        return False
    # A random string of n characters over ~64 symbols scores close to
    # log2(min(n, 64)); words and identifiers score well under it.
    return _entropy(core) >= 0.8 * math.log2(min(len(core), 64))


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
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


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


def send_outbox(box_path: Path, client_factory: Callable[[], Any]) -> bool:
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
        client = client_factory()
        client.add(memory_text(box), user_id=box.get("scope"),
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


def flush(session: Optional[str], client_factory: Callable[[], Any]) -> int:
    """Claim and send what one session (or, with None, every stale session and
    every outbox left by a failed send) has not sent yet. Returns memories sent."""
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


def _context_block(client: Any, scope: str, repo: str, query: str, budget: int) -> str:
    ctx = client.get_context(query=query[:2000], user_id=scope, token_budget=budget)
    body = (getattr(ctx, "context", "") or "").strip()
    if not body or not getattr(ctx, "sources", None):
        return ""
    return (f"Korely memory of the repository {repo} (user_id {scope}), what earlier "
            f"sessions stored, whichever assistant they ran in:\n\n{body}")


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
             client_factory: Optional[Callable[[], Any]] = None,
             background: Callable[[Optional[str]], None] = flush_in_background) -> str:
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

            client_factory = lambda: Korely(timeout=NETWORK_TIMEOUT)  # noqa: E731
        ev = Event(data)
        scope = scope_for(ev.cwd, fixed_scope)
        repo = repo_name(ev.cwd)

        if event == "session-start":
            background(None)  # what sessions that ended without a word left behind
            text = _context_block(client_factory(), scope, repo,
                                  f"{repo}: decisions, rules, open work and next steps",
                                  START_BUDGET)
            return _context_output(tool, event, text) if text else quiet

        if event == "prompt":
            remember_turn(ev, tool, scope, "user", ev.prompt)
            if tool == "cursor" or not ev.prompt.strip() or _primed(ev.session):
                return quiet
            # Once per session, the context of what the developer asked: the
            # start could only guess at a query.
            text = _context_block(client_factory(), scope, repo, ev.prompt, PROMPT_BUDGET)
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


def hook_command(base: str, event: str, tool: str, scope: Optional[str] = None) -> str:
    import shlex

    cmd = f"{base} hook {event} --tool {tool}"
    if scope:
        cmd += f" --user-id {shlex.quote(scope)}"
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


def merge_mcp_json(config: Dict[str, Any], command: Optional[List[str]]) -> Dict[str, Any]:
    """`config` with the `korely` server set to `command`, or removed (None)."""
    servers = config.get("mcpServers")
    servers = dict(servers) if isinstance(servers, dict) else {}
    if command:
        servers["korely"] = {"type": "stdio", "command": command[0], "args": command[1:]}
    else:
        servers.pop("korely", None)
    out = dict(config)
    if servers:
        out["mcpServers"] = servers
    else:
        out.pop("mcpServers", None)
    return out


_TOML_TABLE = re.compile(r"(?ms)^\[mcp_servers\.korely\]\n.*?(?=^\[|\Z)")


def merge_mcp_toml(text: str, command: Optional[List[str]]) -> str:
    """Codex's config.toml with a `[mcp_servers.korely]` table set or removed.
    Plain text: the standard library has no TOML writer, and every other line
    of the file stays as the developer wrote it."""
    text = _TOML_TABLE.sub("", text).rstrip("\n")
    if command:
        args = ", ".join(json.dumps(a) for a in command[1:])
        table = (f"[mcp_servers.korely]\ncommand = {json.dumps(command[0])}\n"
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
            run: Callable[..., Any] = subprocess.run) -> List[str]:
    """Write (or with `remove`, take out) this tool's korely hooks and MCP
    server. Returns one line per change, for the report."""
    base = base or korely_command()
    files = hook_files(tool, project, everywhere)
    report: List[str] = []
    if not everywhere:
        for path in files.values():
            if path.exists() and _tracked(path, project):
                raise InstallError(
                    f"{path} is committed to the repository: korely does not add personal "
                    f"hooks to a shared file. Use --global (every repository, each with its "
                    f"own memory) or add them by hand.")
    commands = {} if remove else {e: hook_command(base, e, tool, scope) for e in EVENTS}
    path = files["hooks"]
    if path.exists() or not remove:
        _save_json(path, merge_hooks(_load_json(path), tool, commands))
        report.append(f"{'removed from' if remove else 'hooks in'} {path}")

    server = None if (remove or not mcp) else _mcp_command(base, scope)
    if tool == "claude-code":
        # Claude Code keeps MCP servers in ~/.claude.json, which it rewrites
        # itself: its own command is the one safe way in.
        where = "user" if everywhere else "local"
        cmd = ["claude", "mcp", "remove", "--scope", where, "korely"]
        _quiet_run(run, cmd, project)
        if server:
            cmd = ["claude", "mcp", "add", "--scope", where, "korely", "--", *server]
            if _quiet_run(run, cmd, project):
                report.append(f"MCP server korely ({where} scope) added with `claude mcp add`")
            else:
                import shlex

                report.append("MCP server not added (no `claude` command found). Run: "
                              + " ".join(shlex.quote(c) for c in cmd))
        elif remove:
            report.append(f"MCP server korely removed ({where} scope)")
    elif tool == "cursor":
        mp = files["mcp"]
        if server or mp.exists():
            _save_json(mp, merge_mcp_json(_load_json(mp), server))
            if server:
                report.append(f"MCP server korely in {mp}")
    else:
        mp = files["mcp"]
        old = mp.read_text(encoding="utf-8") if mp.exists() else ""
        new = merge_mcp_toml(old, server)
        if new != old and (server or mp.exists()):
            mp.parent.mkdir(parents=True, exist_ok=True)
            mp.write_text(new, encoding="utf-8")
            if server:
                report.append(f"MCP server korely in {mp}")
    if not everywhere and not remove:
        for p in files.values():
            if p.exists():
                done = _keep_out_of_git(p, project)
                if done:
                    report.append(done)
    return report


def _mcp_command(base: str, scope: Optional[str]) -> List[str]:
    """The stdio server next to the CLI, scoped like the hooks: `--repo-scope`
    reads and writes `repo:<name>` of the folder the tool starts it in."""
    import shlex

    parts = shlex.split(base)
    if len(parts) == 1:  # the `korely` script: pip puts `korely-mcp` beside it
        cmd = [str(Path(parts[0]).with_name("korely-mcp"))]
    else:  # python -m korely_memory.cli
        cmd = [parts[0], "-m", "korely_memory.mcp_server"]
    return cmd + (["--user-id", scope] if scope else ["--repo-scope"])


def _quiet_run(run: Callable[..., Any], cmd: List[str], cwd: Path) -> bool:
    try:
        out = run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return getattr(out, "returncode", 1) == 0
