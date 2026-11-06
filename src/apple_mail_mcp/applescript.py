from __future__ import annotations

import re
import subprocess
import time

US = "\x1f"
RS = "\x1e"

_MAIL_BUNDLE_ID = "com.apple.mail"

# Mail terminates itself when idle, so any script may fail with -600 even though
# the previous call moments earlier succeeded. Detected by the trailing code, not
# by message text: the message is localized and uses a typographic apostrophe, and
# caller data echoed mid-message can hold "-600" too.
_NOT_RUNNING = "-600"

_TRAILING_CODE = re.compile(r"\((-\d+)\)\s*$", re.M)


class AppleScriptError(RuntimeError):
    """osascript exited non-zero, or Mail rejected the script."""


def error_code(exc: BaseException) -> str | None:
    """The AppleScript error code that ends a line of the message, or None.

    The last match wins, because stderr can carry XPC noise before the real error.
    """
    found = _TRAILING_CODE.findall(str(exc))[-1:]
    return found[0] if found else None


def quote(value: str) -> str:
    """Render a Python string as an AppleScript string literal."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class OsascriptRunner:
    """Runs AppleScript against Mail, launching it on demand."""

    def __init__(self, timeout: float = 120.0, launch_wait: float = 20.0) -> None:
        self._timeout = timeout
        self._launch_wait = launch_wait

    def __call__(self, script: str) -> str:
        try:
            return self._exec(script)
        except AppleScriptError as err:
            if error_code(err) != _NOT_RUNNING:
                raise
        self._ensure_running()
        return self._exec(script)

    def _exec(self, script: str) -> str:
        try:
            proc = subprocess.run(
                ["osascript", "-"],
                input=script,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as err:
            raise TimeoutError(
                f"Mail did not respond within {self._timeout}s"
            ) from err
        if proc.returncode != 0:
            raise AppleScriptError(
                proc.stderr.strip() or f"osascript exited {proc.returncode}"
            )
        return proc.stdout

    def _ensure_running(self) -> None:
        # -g keeps Mail in the background and -j keeps it hidden, so an agent
        # acting on mail never steals the user's focus. `activate` would.
        subprocess.run(
            ["open", "-g", "-j", "-b", _MAIL_BUNDLE_ID],
            capture_output=True,
            check=False,
        )
        deadline = time.monotonic() + self._launch_wait
        while True:
            try:
                self._exec('tell application "Mail" to return "ok"')
                return
            except AppleScriptError:
                if time.monotonic() >= deadline:
                    raise AppleScriptError(
                        f"Mail is not running and could not be launched ({_NOT_RUNNING})"
                    ) from None
                time.sleep(0.5)
