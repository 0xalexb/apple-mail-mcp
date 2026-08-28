from __future__ import annotations

import threading
from importlib.metadata import version

from mcp.server import MCPServer

from apple_mail_mcp.config import load_config
from apple_mail_mcp.mail_service import FLAG_COLORS, MailService

mcp = MCPServer("apple-mail", version=version("apple-mail-mcp"))

_service: MailService | None = None
_service_lock = threading.Lock()


def _get_service() -> MailService:
    global _service
    with _service_lock:
        if _service is None:
            _service = MailService(load_config())
        return _service


@mcp.tool()
def ping() -> str:
    """Health check - returns pong."""
    return "pong"


@mcp.tool()
def list_mailboxes() -> list[dict]:
    """Returns the mailboxes this server is allowed to touch, with unread counts and the permissions usable on each. Mailbox paths are full paths from the account root, e.g. 'Filed/Finance'."""
    return _get_service().list_mailboxes()


@mcp.tool()
def list_messages(
    account: str,
    mailbox: str,
    limit: int = 25,
    offset: int = 0,
    unread_only: bool = False,
    flagged_only: bool = False,
    from_contains: str | None = None,
    subject_contains: str | None = None,
    since: str | None = None,
    before: str | None = None,
) -> dict:
    """Lists messages in one mailbox, newest first. Filters are cheap and paging is not: prefer narrowing with unread_only/flagged_only/from_contains/subject_contains/since/before over paging through a large mailbox. Dates are ISO 8601 ('2026-08-28' or '2026-08-28T09:00:00'). Returns handles to pass to the other tools."""
    return _get_service().list_messages(
        account_key=account,
        mailbox=mailbox,
        limit=limit,
        offset=offset,
        unread_only=unread_only,
        flagged_only=flagged_only,
        from_contains=from_contains,
        subject_contains=subject_contains,
        since=since,
        before=before,
    )


@mcp.tool()
def read_message(handle: str, mark_read: bool = False) -> dict:
    """Returns one message's headers and body. Takes a handle from list_messages. Pass mark_read=true to mark it read at the same time; by default reading leaves the unread state untouched."""
    return _get_service().read_message(handle, mark_read=mark_read)


@mcp.tool()
def mark_read(handle: str) -> dict:
    """Marks a message as read. Takes a handle from list_messages."""
    return _get_service().set_read_status(handle, read=True)


@mcp.tool()
def mark_unread(handle: str) -> dict:
    """Marks a message as unread. Takes a handle from list_messages."""
    return _get_service().set_read_status(handle, read=False)


@mcp.tool()
def set_flag(handle: str, color: str | None = None) -> dict:
    """Sets or clears a message's flag. Colors: red, orange, yellow, green, blue, purple, gray. Pass 'none' or omit color to unflag."""
    return _get_service().set_flag(handle, color)


@mcp.tool()
def move_message(
    handle: str, target_mailbox: str, target_account: str | None = None
) -> dict:
    """Moves a message to another mailbox. Requires move_from on the source and move_to on the target. target_account defaults to the message's own account. Returns the message's new handle. Trash and Junk mailboxes are refused: this server never deletes mail. The result carries verified: true only when the message is confirmed gone from the source mailbox; verified: false means it is still there and comes with a warning explaining why."""
    return _get_service().move_message(handle, target_mailbox, target_account)


@mcp.tool()
def archive_message(handle: str) -> dict:
    """Moves a message to its account's configured archive mailbox. Returns the message's new handle. The result carries verified: true only when the message is confirmed gone from the source mailbox; verified: false means the archive did not take effect and comes with a warning explaining why. Do not report an unverified result as archived."""
    return _get_service().archive_message(handle)


@mcp.tool()
def flag_colors() -> list[str]:
    """Returns the flag colors Apple Mail supports, in flag-index order."""
    return list(FLAG_COLORS)


def main():
    import sys

    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        print(f"apple-mail-mcp {version('apple-mail-mcp')}")
        sys.exit(0)
    mcp.run()


if __name__ == "__main__":
    main()
