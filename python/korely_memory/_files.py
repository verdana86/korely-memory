"""The files this package keeps for one person: the config file with the keys
(`korely init`) and the buffers of the coding assistants' hooks.

One way to write them, so that nobody but the owner ever reads one, not even
for an instant, and a crash never leaves half a file. One way to read the
config file back before changing it, so that a file korely cannot read is
never replaced by a new one that has lost its keys and profiles.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .exceptions import KorelyError


def write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path``, readable by its owner alone.

    The folder is created 0700 when it does not exist (an existing one is
    left as it is: it may be shared on purpose). The text goes into a new
    file in the same folder, created by ``tempfile.mkstemp``: mode 0600 from
    its first byte, exclusive, never through a link. It is flushed to disk
    and renamed over ``path``: a rename replaces a symbolic link at ``path``
    instead of writing where it points, and the file it leaves is that new
    one, 0600 whatever the old one's mode was. Until then ``path`` holds the
    previous version, whole.

    Until 0.1.29 the config file was written in place and made 0600 only
    afterwards: a new file held the key for a moment with the default mode
    (0644 with the usual umask), and a link at its place was followed.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:  # the rename itself survives a crash once the folder is on disk
        dfd = os.open(str(path.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def read_config(path: Path) -> dict:
    """The config file as a dict, to change it: ``{}`` when there is none (or
    it is empty).

    A file that is there and cannot be read as a JSON object raises, with
    what to do: rewriting it would throw away the keys and profiles it holds.
    The reason never quotes the file, which holds keys.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as e:
        raise _unreadable(path, type(e).__name__)
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as e:
        raise _unreadable(path, f"not JSON, line {getattr(e, 'lineno', '?')}")
    if not isinstance(data, dict):
        raise _unreadable(path, "not a JSON object")
    return data


def _unreadable(path: Path, reason: str) -> KorelyError:
    return KorelyError(
        f"{path} cannot be read ({reason}), so nothing was saved: rewriting it would lose "
        "the keys and profiles it holds. Repair it, or move it away and run `korely init` "
        "again.", code="config_unreadable")
