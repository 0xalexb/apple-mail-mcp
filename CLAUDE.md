# Apple Mail MCP Server

## Quick Reference

```bash
uv sync                          # Install deps
uv run pytest                    # Run tests
uv run ruff check src/ tests/    # Lint
uv run apple-mail-mcp            # Run server
```

## Architecture

- **`server.py`** — MCPServer tool definitions, lazy service init
- **`mail_service.py`** — domain operations; every AppleScript template lives here
- **`applescript.py`** — osascript subprocess, relaunch/retry, quoting, US/RS framing
- **`config.py`** — YAML allowlist; deny-by-default permission resolution

Three layers, not two: unlike EventKit there is no object model, so the subprocess concerns
are separated from the domain ones. `applescript.py` is the seam tests fake.

## Conventions

- Python 3.11+, type hints via `from __future__ import annotations`
- Permissions: `read`, `mark_read`, `flag`, `move_from`, `move_to`; first matching rule wins
- Handles are `account/mailbox/path#id`; ids are reassigned on move, so mutations re-find the
  message by RFC Message-ID and return the new handle. Arrival is only half of it: a move also
  counts the message in the *source* afterwards and reports `verified` — Mail's local view at
  the moment of the move, not proof the server kept the change
- Flag index: 0-6 = red, orange, yellow, green, blue, purple, gray; -1 = unflagged
- AppleScript payloads are framed with `\x1f` (fields) and `\x1e` (records)

## Error Handling

- `ValueError` — invalid input, or a mailbox/capability outside the allowlist
- `AppleScriptError` (a `RuntimeError`) — Mail rejected the script
- `TimeoutError` — osascript exceeded `timeout_seconds`

## Deletion is out of scope

There is no delete tool and no code path sets `deleted status`. Trash and Junk mailboxes are
refused as move targets even when allowlisted, and as an `archive_mailbox` at config load.
`test_tools_write.py` asserts all of this; keep those tests passing.

## Gmail archiving is impossible, not merely broken

AppleScript's `move` does not clear Gmail's INBOX label — a Mail bug, not ours. Mail removes
the message locally and the server restores it under a new id, so it never leaves the INBOX.
Proven on a live account: two `archive_message` calls returned success and both messages were
still there under the new ids the calls had returned.

A source-side count taken in the same round trip reads zero and so reports a false success.
`_move` therefore counts twice, `SETTLE_SECONDS` apart, and `verified` requires both to be
zero. Do not remove the second check or the delay.

The documented workarounds are a Trash round-trip (violates the no-deletion guarantee) and GUI
scripting (rejected: needs Accessibility, steals focus, breaks on menu renames). Neither is
implemented. Only Trash and Junk are refused as targets; All Mail is attempted and reported on
honestly.

## AppleScript traps

See the "Notes on Apple Mail" section of README.md before touching `mail_service.py`. The
ones that will silently cost you: iterating `messages of mb` directly is 25x slower, `&`
returns a list unless the left operand is text, and moving to `[Gmail]/All Mail` no-ops while
reporting success.

Nothing in `_move`'s script may abort after `move m to target`, or the runner's `-600` retry
moves twice; see the comment above the script in `_move` for the shape that prevents it.

## Distribution

- **Not published to PyPI** — installed via GitHub releases
- `release.yml` generates a bash wrapper running
  `uvx --from "git+https://github.com/0xalexb/apple-mail-mcp@v${VERSION}" apple-mail-mcp`
- Homebrew tap: `0xalexb/homebrew-apps`, formula auto-updated by the release workflow
- To debug startup:
  `echo '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | uv run apple-mail-mcp`

## Testing

- Tests inject a fake runner in place of `OsascriptRunner`; no macOS runtime needed
- Server tools are tested by patching `_get_service()`
