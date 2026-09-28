"""The MCP stdio transport and the tools part of the protocol, standard library only.

Why not the `mcp` package (2026-09-28): it needs Python 3.10, and the Python
macOS ships is 3.9, so `korely-mcp` could not start on a Mac out of the box
(found by the blind installs of korely-agent 0.1.13); and mcp 2.0 removed
`mcp.server.fastmcp`, which the server imported, so every major release of
that package was a way to break it. What this server needs is small: four
tools, over stdio.

The protocol (Model Context Protocol, spec 2025-06-18, "Base Protocol",
"Lifecycle", "Transports: stdio", "Server features: Tools"):
  * JSON-RPC 2.0 messages, one per line on stdin and stdout, UTF-8; nothing
    else is written to stdout (logs go to stderr);
  * `initialize` answers the protocol version the client asked for when this
    server speaks it, else the latest it speaks, with the `tools` capability;
  * `notifications/initialized` and other notifications get no answer;
  * `ping` answers {};
  * `tools/list` gives name, description and a JSON Schema of the arguments;
  * `tools/call` answers {content: [{type: text, text}], isError}; a tool that
    raises is a tool result with isError true, not a protocol error.
"""
from __future__ import annotations

import inspect
import json
import sys
import typing
from typing import Any, Callable, Dict, List, Optional

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _schema_of(annotation: Any) -> Dict[str, Any]:
    """JSON Schema of one parameter from its annotation (str, int, float,
    bool, Optional of those)."""
    origin = typing.get_origin(annotation) if hasattr(typing, "get_origin") else getattr(annotation, "__origin__", None)
    if origin is typing.Union:
        args = [a for a in annotation.__args__ if a is not type(None)]
        if len(args) == 1:
            inner = _schema_of(args[0])
            t = inner.get("type")
            return {"type": [t, "null"]} if t else {}
    t = _JSON_TYPES.get(annotation)
    return {"type": t} if t else {}


class Server:
    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version
        self._tools: Dict[str, Callable[..., str]] = {}

    def tool(self) -> Callable[[Callable[..., str]], Callable[..., str]]:
        def register(fn: Callable[..., str]) -> Callable[..., str]:
            self._tools[fn.__name__] = fn
            return fn
        return register

    def _describe(self, fn: Callable[..., str]) -> Dict[str, Any]:
        hints = typing.get_type_hints(fn)
        props: Dict[str, Any] = {}
        required: List[str] = []
        for name, p in inspect.signature(fn).parameters.items():
            schema = _schema_of(hints.get(name, str))
            if p.default is inspect.Parameter.empty:
                required.append(name)
            else:
                schema["default"] = p.default
            props[name] = schema
        return {
            "name": fn.__name__,
            "description": inspect.getdoc(fn) or "",
            "inputSchema": {"type": "object", "properties": props, "required": required},
        }

    def handle(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The answer to one JSON-RPC message, or None for a notification."""
        method = message.get("method")
        mid = message.get("id")
        if mid is None:
            return None                     # a notification: never answered
        params = message.get("params") or {}
        if method == "initialize":
            asked = params.get("protocolVersion")
            version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
            return self._ok(mid, {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": self.version},
            })
        if method == "ping":
            return self._ok(mid, {})
        if method == "tools/list":
            return self._ok(mid, {"tools": [self._describe(f) for f in self._tools.values()]})
        if method == "tools/call":
            fn = self._tools.get(params.get("name"))
            if fn is None:
                return self._error(mid, -32602, f"Unknown tool: {params.get('name')}")
            arguments = params.get("arguments") or {}
            try:
                inspect.signature(fn).bind(**arguments)
            except TypeError as e:
                return self._error(mid, -32602, f"Invalid arguments for {fn.__name__}: {e}")
            try:
                text = fn(**arguments)
                return self._ok(mid, {"content": [{"type": "text", "text": str(text)}], "isError": False})
            except Exception as e:  # noqa: BLE001 - a tool failure is a tool result
                return self._ok(mid, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
        return self._error(mid, -32601, f"Method not found: {method}")

    @staticmethod
    def _ok(mid: Any, result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    @staticmethod
    def _error(mid: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    def run(self, stdin=None, stdout=None) -> None:
        """Read messages from stdin until it closes; answer on stdout."""
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                answer: Optional[Dict[str, Any]] = self._error(None, -32700, "Parse error")
            else:
                if isinstance(message, list):   # a batch (2025-03-26)
                    answers = [a for a in (self.handle(m) for m in message if isinstance(m, dict)) if a]
                    answer = answers or None    # type: ignore[assignment]
                elif isinstance(message, dict):
                    answer = self.handle(message)
                else:
                    answer = self._error(None, -32600, "Invalid Request")
            if answer:
                stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
                stdout.flush()
