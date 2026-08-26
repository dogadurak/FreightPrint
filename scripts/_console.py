"""Make stdout able to print the names in this project's data.

Windows hands Python a stdout encoded in the system codepage - cp1254 on this machine -
and the corridor is full of names it cannot encode: Köln Eifeltor, Ostrava-Kunčice,
Halkalı. A script then does its work correctly, writes a correct CSV, and dies printing
its own summary. That looks exactly like a failed download, and it has now cost three
separate debugging sessions in this project.

The fix belongs in one place rather than at the top of every script, so that a script
added later inherits it instead of rediscovering the same crash.

    from _console import speak_utf8
    speak_utf8()

`errors="replace"` rather than strict: a console that cannot show a character should
print a question mark, not abort a run whose real output is a file on disk.
"""

import sys


def speak_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
