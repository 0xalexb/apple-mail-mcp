# Fix Gmail archiving: refuse All Mail, verify the message left the source

## Overview

`archive_message` reports success while nothing leaves the Gmail INBOX. Eight VPN messages and
a PCIDSS batch were "archived" and all reappeared in the inbox under fresh ids.

Two independent defects produce that:

1. **The archive target is wrong for Gmail.** `archive_message` is a thin wrapper over
   `move m to target` with `target = [Gmail]/All Mail`. All Mail is not a folder — it is
   Gmail's label-less view of every message, inbox included. README's existing
   *Notes on Apple Mail* already records that a message fetched from `mailbox "INBOX"` reports
   `mailbox of m` as `[Gmail]/All Mail`, so Mail sees a self-move and no-ops.
2. **`_move` never checks the message left the source.** It re-finds the message by RFC
   Message-ID *in the target*, which always succeeds on Gmail because the message was there all
   along. Success is therefore reported unconditionally.

The fix addresses both: refuse the label-less view as a move target *or source*, and make every
move prove the message is gone from where it started.

**Verified constraint:** `/System/Applications/Mail.app/Contents/Resources/Mail.sdef` contains
zero occurrences of "archive". There is no scriptable Archive command and no archive-mailbox
property; Mail's Archive toolbar button cannot be scripted. The only lever is moving to a
mailbox that is a *real Gmail label*, which forces a relabel (IMAP copy + expunge from INBOX,
and expunge-in-INBOX is label removal) instead of a no-op. `deleted status` is settable but
deletion is out of scope per CLAUDE.md and must not be used.

**HARD RULE: the user's real mailbox must not be touched.** No `osascript`, no launching Mail,
no live verification anywhere in this plan. Tests inject a fake runner; no macOS runtime is
needed. The first real-world check is the user archiving one message themselves, and `verified`
is what makes that safe.

## Context (from discovery)

- files/components involved:
  - `src/apple_mail_mcp/config.py` — `is_trash` / `_TRASH_LEAVES` (line 17), `Config.require`
    (line 90), `_account` (line 158)
  - `src/apple_mail_mcp/mail_service.py` — `archive_message` (line 269) including the
    misleading error string at line 275, `_move` (line 281), the archive-branch Trash guard
    (lines 296-300), the `try` block around the target-side lookup (lines 317-320)
  - `src/apple_mail_mcp/server.py` — `move_message` and `archive_message` tool docstrings
  - `config.example.yml` — gmail `archive_mailbox`
  - `README.md` — handles prose (lines 47-48), tool table (lines 26-27), config sample
    (line 92), Archiving prose (lines 125-127), *Notes on Apple Mail* (line 177)
  - `tests/conftest.py:38`, `tests/test_tools_write.py`, `tests/test_config.py`
- baseline: `uv run pytest` → 101 passed. No test reaches a real `osascript`
  (`test_applescript.py` patches `subprocess.run`; everything else injects `FakeRunner`).
- related patterns found:
  - `is_trash` + `_TRASH_LEAVES` is the existing shape for "a mailbox that is never a valid
    target"; `is_all_mail` mirrors it exactly, frozenset included, for symmetry.
  - Trash is refused **twice**: in `Config.require` (`config.py:97`) and again inside `_move`'s
    archive branch (`mail_service.py:296-300`), because `archive_message` bypasses `require`
    entirely via `self._config.account()` at `mail_service.py:295`. All Mail needs the same
    belt-and-braces.
  - `FakeRunner` replays positional canned strings; move/archive tests feed two-field payloads
    at `tests/test_tools_write.py` lines 91, 103, 131, 156, 170, 180 (exactly six).
  - Strict field-count parsing already exists at `mail_service.py:185-186` (`read_message`).
  - Three-layer split: subprocess (`applescript.py`) / domain (`mail_service.py`) / policy
    (`config.py`). Policy decisions belong in `config.py`, script text only in `mail_service.py`.
- dependencies identified: none new. No new packages, no runtime deps.

## Development Approach

- **testing approach**: Regular (code first, then tests within the same task)
- complete each task fully before moving to the next
- make small, focused changes
- **CRITICAL: every task MUST include new/updated tests** for code changes in that task
- **CRITICAL: all tests must pass before starting next task** — no exceptions
- **CRITICAL: update this plan file when scope changes during implementation**
- run `uv run pytest` after each change
- Python 3.11+, `from __future__ import annotations`, newline at EOF
- comments only where they earn their place — why, not what
- do not run linters unless explicitly asked

### Task ordering constraint

Task 2 (fixture migration) **must** land before Task 3 (load-time guard). The guard goes in
`_account`, which `parse_config` calls at `config.py:150`, which is what the `config` fixture
invokes — and `tests/conftest.py:38` currently holds the value the guard rejects. Reversing the
two fails fixture construction and errors nearly the whole suite. Verified, not assumed.

Task 1 must precede Task 3 as a **code** dependency: `_account` calls `is_all_mail`, and
`mail_service.py` needs it imported alongside `is_trash` (the import at `mail_service.py:6` is
currently `from apple_mail_mcp.config import Config, is_trash`).

It is only *test* ordering that Task 1 leaves unconstrained — no existing test grants `move_from`
or `move_to` on an All Mail path, so the new refusals break nothing on their own.

## Testing Strategy

- **unit tests**: required for every task, via the injected `FakeRunner`
- **e2e tests**: none — this project has no UI and no e2e suite. The equivalent is a single
  manual archive by the user after the work lands; see Post-Completion.
- the no-deletion guarantee is load-bearing: `test_service_never_emits_a_delete_command` asserts
  no emitted script contains "delete" or "deleted status". The new source-side check must not
  introduce either word.
- `test_bracketed_gmail_names_are_literal` (`tests/test_config.py:134-140`) uses
  `[Gmail]/All Mail` to prove square brackets are not a character class. That test and the two
  doc lines it mirrors (`README.md:121`, `config.example.yml:8`) stay exactly as they are —
  they are about glob syntax, not about archiving. Do not "fix" them.

## Progress Tracking

- mark completed items with `[x]` immediately when done
- add newly discovered tasks with ➕ prefix
- document issues/blockers with ⚠️ prefix
- update plan if implementation deviates from original scope

## Solution Overview

Layered defence, static then dynamic:

- **Static (config):** the known-bad mailbox is refused before it can be used — at config load
  for `archive_mailbox`, and at call time for both `move_to` and `move_from`. Fails fast, with
  an error that names the fix.
- **Dynamic (service):** every move proves the message left the source, in the same AppleScript
  round trip. This catches what the static check cannot — notably localized Gmail names such as
  `[Gmail]/Alle Nachrichten` — without needing to know why a move failed.

Key design decisions:

- **Load-time hard failure** on an All Mail `archive_mailbox`, not a call-time refusal. A
  deliberate breaking change: the currently-shipped `config.example.yml` teaches the broken
  setup, and the server is install-from-source. Loud beats silent.
- **`verified: false` + `warning`, never a raised error.** IMAP sync lag can make a genuine move
  look unverified for a few seconds; raising would be its own false report.
- **All Mail is refused as a `move_from` source too.** In Gmail, adding a label never removes a
  message from All Mail, so the source-side count is permanently ≥1 and every such move would
  report `verified: false` — the new check would manufacture a false failure in the very mailbox
  this bug is about. A "move" that provably cannot move is not a move; this server has no
  vocabulary for "add a label", so it refuses rather than lies. The shipped example config does
  not allowlist All Mail at all, so nothing in the documented setup regresses.
  *Alternative if this proves too strict in practice:* keep `move_from` legal and make the
  source-side check advisory when `is_all_mail(source)`. Not chosen — it reintroduces an
  unverifiable success path.
- **The warning never says "archived".** `_move` serves both `move_message` and
  `archive_message`; wording that assumes archiving would be false half the time.
- **Known gap, do not try to fix:** localized All Mail names will not match the leaf test.
  Enumerating Gmail's locale table is not worth it — runtime verification covers them.

## Technical Details

**Predicate** (`config.py`, beside `is_trash`):

```python
_ALL_MAIL_LEAVES = frozenset({"all mail"})

def is_all_mail(path: str) -> bool:
    return path.rsplit("/", 1)[-1].strip().lower() in _ALL_MAIL_LEAVES
```

**Script fragment** (`_move`). The placement is load-bearing — `set stillThere` goes **outside**
the existing `try`, between `move m to target` and `set newId to ""`:

```applescript
move m to target
set stillThere to (count of (messages of mb whose message id is rfc))
set newId to ""
try
	set newId to "" & (id of (first message of target whose message id is rfc))
end try
return "" & rfc & "<US>" & newId & "<US>" & stillThere
```

Inside the `try`, a target-side lookup failure — precisely the sync-lag case the existing `note`
exists for — would skip the assignment and leave `stillThere` undefined at the `return`, turning
a successful move into an `AppleScriptError`. That failure only reproduces against a live
account, which this plan cannot test, so it has to be prevented by construction.

`mb` stays valid after the move. `whose` filters inside Mail — the repo's own performance note
says that costs about the same on a 3400-message mailbox as on a 3-message one. Never iterate
`messages of mb` directly. The leading `""` is required: `&` builds a *list* unless the left
operand is text.

**Parse** (strict — we generate the script, so a short payload is a bug; mirrors
`mail_service.py:185-186`):

```python
fields = self._run(script).split(US)
if len(fields) < 3:
    raise RuntimeError(f"Unexpected response moving {handle}")
rfc, new_id, still_there = fields[0], fields[1].strip(), fields[2]
verified = int(still_there.strip() or 0) == 0
```

**Warning text** when `verified` is false:

> The message is still in '&lt;source&gt;' after the move, so it did not leave that mailbox. On
> Gmail this happens when the target is the label-less All Mail view rather than a real label.
> It can also mean the account is still syncing — re-check with list_messages before retrying.

`warning` coexists with the existing `note` key: they answer different questions — *did it
arrive?* versus *did it leave?* The bug was invisible because only the first was ever asked. All
four combinations are legal and two of them need tests (see Task 4).

## What Goes Where

- **Implementation Steps** (`[ ]`): code, tests, docs — all achievable in this repo
- **Post-Completion** (no checkboxes): the single manual archive against the live mailbox, which
  only the user may perform

## Implementation Steps

### Task 1: Add the `is_all_mail` predicate and refuse it as a move source and target

**Files:**
- Modify: `src/apple_mail_mcp/config.py`
- Modify: `tests/test_config.py`

- [x] add `_ALL_MAIL_LEAVES` and `is_all_mail(path)` beside `_TRASH_LEAVES` / `is_trash`, with a
      why-comment: the label-less view containing every message, so a move into it is a
      self-move that silently does nothing
- [x] in `Config.require`, refuse `is_all_mail(path)` for **both** `move_to` and `move_from`,
      alongside the existing `is_trash` branch, with errors naming a real label as the fix
- [x] write tests for `is_all_mail`: matches `[Gmail]/All Mail`, `all mail`, mixed case and
      surrounding whitespace; does not match `Archive`, `Filed/All Mail Backups`
- [x] write test that `require(..., "move_to")` on an allowlisted All Mail path raises
- [x] write test that `require(..., "move_from")` on an allowlisted All Mail path raises
- [x] confirm `test_bracketed_gmail_names_are_literal` (`tests/test_config.py:134-140`) still
      passes untouched — it is about glob syntax, not archiving
- [x] run `uv run pytest` — must pass before task 2

### Task 2: Migrate the shared fixture off the broken archive target

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_tools_write.py`
- Modify: `tests/test_mail_service.py`

- [x] change `tests/conftest.py:38` gmail `archive_mailbox` from `"[Gmail]/All Mail"` to
      `"Archive"` (required: Task 3's guard would otherwise raise in every test using the fixture)
- [x] do **not** add an `Archive` mailbox entry to the gmail fixture — archive targets bypass the
      allowlist by design (`mail_service.py:295` uses `account()`, not `require()`), and
      `test_archive_target_need_not_carry_move_to` (`tests/test_tools_write.py:178-181`) pins
      that behaviour
- [x] update `test_archive_moves_to_the_configured_mailbox`: expected handle becomes
      `gmail/Archive#200`, script assertion becomes `mailbox "Archive"`
- [x] fix the now-stale comment and name at `tests/test_mail_service.py:49-52` — "despite being
      the archive target" stops being true once gmail's archive is `Archive`; the assertion
      itself (`== []`, All Mail is not allowlisted) still holds
- [x] run `grep -rn 'All Mail' tests/` and confirm the remaining hits are glob-syntax tests, not
      fixture dependencies
- [x] run `uv run pytest` — must pass before task 3

### Task 3: Reject an All Mail `archive_mailbox` at config load and in the archive branch

**Files:**
- Modify: `src/apple_mail_mcp/config.py`
- Modify: `src/apple_mail_mcp/mail_service.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_tools_write.py`

- [x] in `_account`, raise `ValueError` when `archive_mailbox` is `is_all_mail`, with the message:
      Gmail archives by moving to a real label such as 'Archive'; '[Gmail]/All Mail' is not a
      folder and the move silently does nothing
- [x] give the archive-branch guard a **different** error string from the load-time one, sharing
      no matchable substring — otherwise no test can prove which of the two layers fired
- [x] add an `is_all_mail(target_mailbox)` refusal beside the existing `is_trash` check at
      `mail_service.py:296`, matching how Trash is guarded in both layers — `archive_message`
      never goes through `require`, so a config not built by `parse_config` would otherwise
      archive to All Mail unchecked
- [x] write test that `parse_config` raises on `archive_mailbox: "[Gmail]/All Mail"`, asserting
      the error names the fix
- [x] write test that a config with `archive_mailbox: "Archive"` still parses
- [x] write test that the `_move` archive branch refuses an All Mail target — construct the
      frozen dataclasses **directly**, bypassing `parse_config`:
      `Config(accounts=(AccountConfig(name="a", account_id=None, archive_mailbox="[Gmail]/All Mail",
      mailboxes=(MailboxRule("INBOX", Permissions(move_from=True)),)),))`.
      Do **not** mirror `test_archive_into_trash_is_refused` (`tests/test_tools_write.py:213-232`):
      it builds its config with `parse_config`, which for All Mail now raises at the previous
      checkbox, so the test would die in config parsing, never reach `archive_message`, and still
      go green on a shared error substring — proving nothing about the guard whose whole
      justification is configs that bypass `parse_config`
- [x] assert that test matches the archive-branch error specifically, not the load-time one
- [x] run `uv run pytest` — must pass before task 4

### Task 4: Verify the message left the source mailbox

**Files:**
- Modify: `src/apple_mail_mcp/mail_service.py`
- Modify: `tests/test_tools_write.py`

- [x] add the source-side count to the `_move` script **outside** the existing `try` block, per
      Technical Details, and return it as a third `US`-separated field
- [x] parse three fields strictly; raise `RuntimeError` on a short payload
- [x] add `verified: bool` to every `move_message` and `archive_message` result, plus a `warning`
      key when false; the warning must not use the word "archived", since `_move` serves both
      operations
- [x] append the third field to the six canned `FakeRunner` responses in
      `tests/test_tools_write.py` — `test_move_returns_the_reassigned_handle`,
      `test_move_reports_when_the_message_cannot_be_relocated`, `test_move_across_accounts`,
      `test_service_never_emits_a_delete_command`, `test_archive_moves_to_the_configured_mailbox`,
      `test_archive_target_need_not_carry_move_to` (lines 91/103/131/156/170/180 as of the
      baseline; Task 2 edits this file, so trust the names over the numbers)
- [x] update `test_move_reports_when_the_message_cannot_be_relocated`
      (`tests/test_tools_write.py:102-106`) — its two-field payload now trips the new strict
      parse, and its meaning becomes `handle is None` + `note` **and** `verified is True`
- [x] write test: `verified is True` and no `warning` when the source count is 0
- [x] write test: `verified is False` with a `warning` naming the source mailbox when the count
      is non-zero
- [x] write test: `note` and `verified: True` coexist (arrived late, but did leave)
- [x] write test: `note` and `verified: False` + `warning` coexist (the alarming combination)
- [x] write test: the emitted script contains the source-side check and still contains neither
      "delete" nor "deleted status"
- [x] write test: a two-field payload raises `RuntimeError`
- [x] run `uv run pytest` — must pass before task 5

### Task 5: Correct the documentation and error strings that currently teach the bug

**Files:**
- Modify: `src/apple_mail_mcp/mail_service.py`
- Modify: `src/apple_mail_mcp/server.py`
- Modify: `config.example.yml`
- Modify: `README.md`
- Modify: `tests/test_tools_read.py`

- [x] `mail_service.py:275` — the "no archive_mailbox configured" error currently reads
      "Gmail-style accounts archive to '[Gmail]/All Mail'"; after Task 3 that instructs the user
      to configure the exact value that now hard-fails at load. Rewrite it to name a real label
- [x] add a sentence about `verified` to the `move_message` and `archive_message` tool
      docstrings — that string is the only thing a calling agent reads before deciding the
      archive worked
- [x] `config.example.yml`: gmail `archive_mailbox` becomes `Archive`, with a comment saying why
      All Mail is refused. Leave the glob-syntax comment at line 8 alone
- [x] `README.md` config sample (line 92): same change
- [x] `README.md:47-48`: "re-find the message by its RFC Message-ID and return its **new**
      handle" is the arrival-only claim that made this bug invisible — add the departure half
- [x] `README.md` tool table (lines 26-27): note that **both** `move_message` and
      `archive_message` report `verified`
- [x] `README.md` Archiving prose (lines 125-127): "Gmail accounts have none and archive by
      moving to `[Gmail]/All Mail`" states the bug as fact — rewrite to say Gmail archives to a
      real label and that All Mail is refused as source and target
- [x] `README.md:129-131`: "It does need its own entry with `move_from` if you want to move
      messages back *out* of it" — correct for a real label, but anyone who followed it for a
      Gmail account granted `move_from` on `[Gmail]/All Mail`, which Task 1 now refuses at call
      time. Say that All Mail is the one archive target this does not work for
- [x] add a *Notes on Apple Mail* bullet for the All Mail no-op, next to the existing
      "Gmail reports the wrong mailbox" bullet
- [x] extend the docstring test at `tests/test_tools_read.py:79-93` (or add one) asserting
      "verified" appears in both tool docstrings — Task 5 changes source, so it owes tests
- [x] run `uv run pytest` — must pass before task 6

### Task 6: Verify acceptance criteria

- [x] `archive_message` on a Gmail account can no longer target the label-less view — refused at
      config load, in the archive branch, and as a `move_to` target
- [x] All Mail is refused as a `move_from` source, so no move can report a permanent
      `verified: false`
- [x] every move and archive result carries `verified`, and an unverified move carries a
      `warning` instead of looking like success
- [x] no error string or doc line anywhere still instructs the user to configure
      `[Gmail]/All Mail` as an archive. `grep -rn 'All Mail' src/ README.md config.example.yml`
      should return only: the glob-literalness examples (`README.md:121`,
      `config.example.yml:8`, the `matches()` docstring at `config.py:109`), the pre-existing
      "Gmail reports the wrong mailbox" trap note at `README.md:177`, the new no-op note, and the
      new refusal messages
- [x] no code path emits `delete` or `deleted status`; `test_service_never_emits_a_delete_command`
      and the rest of the no-deletion suite still pass
- [x] no *test execution* reaches a real `osascript`, and this work adds no new subprocess call
      site. `applescript.py:45` (`osascript -`) and `applescript.py:64-68` (`open -g -j`) are the
      production runner and must stay exactly as they are
- [x] run full test suite: `uv run pytest` — 101 baseline tests plus the new ones, all green
- [x] no e2e suite exists in this project — nothing to run

### Task 7: [Final] Update documentation

- [ ] README.md updated (covered in Task 5 — confirm nothing was missed)
- [ ] add one or two lines to CLAUDE.md beside "Deletion is out of scope" (lines 37-41) recording
      the All Mail refusal as a project invariant, exactly parallel to how the Trash refusal is
      recorded there: refused as source and target even when allowlisted, asserted by tests.
      An invariant, not a pattern write-up — without it a future reader sees only the Trash rule
      and reads the All Mail refusal as an unexplained oddity to relax
- [ ] move this plan to `docs/plans/completed/`

## Post-Completion

*Items requiring manual intervention — no checkboxes, informational only*

**Manual verification** (user only — the assistant must not touch the live mailbox):

- Confirm a real Gmail label exists to archive into. Apple Mail and Gmail both create `Archive`;
  if the account has none, create it in Gmail's own interface first.
- Point the live config's gmail `archive_mailbox` at that label, and grant it an entry with
  `move_from` if you want to move messages back *out* of it later.
- Archive **one** message and read the response. `verified: true` means the INBOX label was
  genuinely removed. `verified: false` means it was not — re-check with `list_messages` before
  concluding, since a few seconds of IMAP sync lag can produce a false negative.
- Only after one message verifies clean should the 125-message batch be attempted.

**Breaking change to announce:**

- Any existing config with `archive_mailbox: "[Gmail]/All Mail"` will now fail to load, with an
  error naming the fix. That is deliberate; the previous behaviour silently did nothing.
- A config that allowlists an All Mail path with `move_from` or `move_to` still **loads** clean
  and now raises on the first move against it. Following the old `README.md:129-131` advice for a
  Gmail account produces exactly that config, so the call-time refusal needs announcing too — a
  config that loads and then throws is the kind of surprise this plan exists to remove.

**Known gap:**

- Localized Gmail names (`[Gmail]/Alle Nachrichten` and similar) are not matched by the static
  leaf test. They are caught at runtime by `verified`, which is the deliberate trade-off.

**If `verified: false` persists on a real label:**

- The remaining untested theory from the brainstorm is `set mailbox of m to target` instead of
  `move m to target` — the dictionary marks `message.mailbox` writable, and Mail may not
  short-circuit the property setter the way it short-circuits the self-move. Purely speculative;
  it can only be settled against a live mailbox, one message at a time.
