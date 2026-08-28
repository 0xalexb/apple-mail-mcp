# Apple Mail MCP Server

MCP server for Apple Mail — lets Claude triage your mail: read messages, mark them read,
flag them, move them between folders, and archive them.

**It cannot delete mail.** There is no delete tool, and Trash and Junk are refused as move
targets even if you allowlist them.

Built on Mail.app's AppleScript interface, in the shape of
[apple-reminders-mcp](https://github.com/0xalexb/apple-reminders-mcp) and
[apple-calendar-mcp](https://github.com/0xalexb/apple-calendar-mcp).

> Requires macOS with Mail.app configured. On first use, macOS will ask you to grant
> Automation access to Mail.

## Tools

| Tool | Description |
|------|-------------|
| `ping` | Health check |
| `list_mailboxes` | Allowed mailboxes with unread counts and the permissions usable on each |
| `list_messages` | Messages in one mailbox, with filters and paging |
| `read_message` | Full headers and body; optionally marks read |
| `mark_read` / `mark_unread` | Toggle read state |
| `set_flag` | Set or clear a colour flag |
| `move_message` | Move to another mailbox; reports `verified` |
| `archive_message` | Move to the account's configured archive mailbox; reports `verified` |
| `flag_colors` | The seven colours Apple Mail supports |

### Filtering is cheap; paging is not

A `whose` clause runs inside Mail and costs about the same on a 3-message mailbox as on a
3400-message one (~0.4s measured). Reading properties *out* of Mail costs roughly 0.1s per
message. So narrow with filters rather than paging:

```
list_messages(account="gmail", mailbox="INBOX", unread_only=true)
list_messages(account="gmail", mailbox="INBOX", since="2026-08-01", from_contains="@github.com")
```

Filters: `unread_only`, `flagged_only`, `from_contains`, `subject_contains`, `since`,
`before` (ISO 8601 dates), plus `limit` (default 25, max 100) and `offset`.

### Handles

`list_messages` returns a handle per message — `gmail/INBOX#127614` — which the other tools
take. Mail can reassign a message's id when it changes mailbox, so `move_message` and
`archive_message` re-find the message by its RFC Message-ID and return its **new** handle.
Use that; the old one may no longer resolve.

Arriving in the target is only half of a move. Both tools also count the message in the
*source* mailbox afterwards and report `verified`. `true` means Mail reported the message
gone from the source at the time of the move. That count runs in the same AppleScript round
trip as the move, so it is Mail's local view at that instant, not proof the mail server kept
the change: an IMAP expunge the server later rejects, or the message being re-downloaded into
the source afterwards, cannot be seen from that one round trip. `false` means one of two
different things — the message was still in the source, or Mail reported no count at all and
the move is merely unconfirmed — and the result carries a `warning` saying which, instead of
looking like success.

## Install

### Homebrew (recommended)

```bash
brew install 0xalexb/apps/apple-mail-mcp
```

### uvx (no local install)

```bash
uvx --from "git+https://github.com/0xalexb/apple-mail-mcp" apple-mail-mcp
```

### From source

```bash
git clone https://github.com/0xalexb/apple-mail-mcp.git
cd apple-mail-mcp
uv sync
```

## Configure

### The allowlist

**Every mailbox is denied until you list it.** With no config file the server refuses to do
anything, by design. Copy `config.example.yml` to
`~/.config/apple-mail-mcp/config.yml` (or point `APPLE_MAIL_MCP_CONFIG` at it) and edit.

```yaml
defaults:
  read: true
  mark_read: true
  flag: true
  move_from: false
  move_to: false

accounts:
  - name: gmail
    id: A1B2C3D4-1234-5678-90AB-CDEF12345678   # optional; survives renaming the account
    archive_mailbox: Archive
    mailboxes:
      - path: INBOX
        move_from: true
      - path: Archive          # the archive target needs no rule; list it to read it,
        move_from: true        # to file into it, and to move messages back out
        move_to: true
      - path: "Filed/*"
        move_to: true
```

| Permission | Grants |
|---|---|
| `read` | `list_messages`, `read_message` |
| `mark_read` | `mark_read`, `mark_unread`, `read_message(mark_read=true)` |
| `flag` | `set_flag` |
| `move_from` | Messages may **leave** this mailbox |
| `move_to` | Messages may be **filed into** this mailbox |

Splitting `move_from` from `move_to` is the point: an Inbox you triage *out of* gets
`move_from`, a filing folder gets `move_to`, and a mailbox with neither is read-only.

Rules are evaluated in order and **the first match wins**.

### Mailbox paths

Paths are **full paths from the account root**, slash-separated, exactly as
`list_mailboxes` reports them — `Filed/Finance/Statements`, not
`Finance/Statements`. Mail's own `mailboxes of account` returns a flat list with
leaf-only names, so nesting is not obvious; ask `list_mailboxes` rather than guessing.

In patterns `*` and `?` are the only wildcards, and `*` spans `/`, so `work/*` covers the
whole subtree. Square brackets are literal, so Gmail's `[Gmail]/Sent Mail` works as written.

### Archiving

`archive_mailbox` is per-account and required for `archive_message`, because there is no
universal answer: iCloud has a real top-level `Archive`, while a Gmail account typically has
none and Apple Mail designates `[Gmail]/All Mail` as its Archive Mailbox (Settings → Accounts
→ Mailbox Behaviours).

The server does not second-guess that choice. `[Gmail]/All Mail` is Gmail's label-less view of
every message, so a move into it can be a self-move that silently does nothing — but it is
often the only archive target an account has, and refusing it would leave `archive_message`
with nowhere to go. Instead the move is attempted and the result reports `verified`, so a
no-op is visible rather than reported as success. Trash and Junk remain refused as archive
targets: archiving is not deletion.

> **Gmail archiving does not work, and this server will tell you so.** Apple Mail's scriptable
> `move` does not clear Gmail's INBOX label — a long-standing Mail bug. The message is removed
> locally and the server restores it seconds later under a new id, so it never leaves the
> INBOX. `archive_message` attempts the move, waits, re-checks, and returns `verified: false`
> with an explanation. It does not pretend. Archiving Gmail mail requires Gmail's own web
> interface, a Gmail filter, or Mail's GUI Archive command — none of which are scriptable.

The archive mailbox does not need `move_to` — naming it as the account's archive is consent
enough for `archive_message`, though the example grants it so `move_message` can file into it
too. It does need its own entry with `move_from` if you want to move messages back *out* of
it; otherwise archiving is one-way.

### MCP client

Claude Code:

```bash
claude mcp add apple-mail-mcp apple-mail-mcp
```

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "apple-mail-mcp": {
      "command": "apple-mail-mcp"
    }
  }
}
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check src/ tests/
uv run apple-mail-mcp
```

Tests fake the AppleScript runner, so the suite runs on any platform without touching Mail.

## Notes on Apple Mail

Things that cost real time to discover, kept here so they are not rediscovered:

- **Mail quits itself when idle.** Scripts fail with `-600` mid-session; the runner relaunches
  Mail with `open -g -j` (background, hidden — never `activate`, which steals focus) and
  retries once.
- **Never `repeat with m in (messages ... of mb)`.** That re-resolves the whole list
  expression on every property access: 64s for 20 messages, against 2.6s for the same work
  after `set msgs to (get messages 1 thru N of mb)` and integer indexing.
- **Plural property gets fail.** `id of msgs` raises `-1728`; properties must be read per
  message.
- **Gmail reports the wrong mailbox.** A message fetched from `mailbox "INBOX"` reports
  `mailbox of m` as `[Gmail]/All Mail`. Permission checks therefore use the *requested*
  path, never `mailbox of m`, which would otherwise escape the allowlist.
- **Moving to `[Gmail]/All Mail` is a no-op that reports success.** It is a view, not a
  label, so Mail sees a self-move, does nothing, and raises no error; the message is still
  in the INBOX afterwards. There is no scriptable Archive command either — `Mail.sdef`
  contains no "archive" — and Apple Mail still designates it as the Archive Mailbox for
  Gmail accounts. Worse, the removal *looks* like it worked: a count taken in the same
  round trip as the move reads zero, and the message reappears moments later. `verified`
  therefore re-checks after a settle, which is the only way to catch it. A localized account names it
  `[Gmail]/Alle Nachrichten` or similar and slips past it, which is why every move also
  counts the message in the source afterwards and reports `verified`.
- **`&` builds a list, not a string,** unless the left operand is text — and `id of m` is an
  integer. Concatenations start with `""`.
- **Every mailbox has class `container`**, and accounts report a subclass such as
  `imap account`, so walking up `container` stops on `is not container`, not `is account`.
- **`date "..."` literals are locale-dependent**, so date filters are built by assigning
  components, resetting `day` to 1 first so setting the month cannot overflow.

## License

MIT
