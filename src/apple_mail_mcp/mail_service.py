from __future__ import annotations

from datetime import date, datetime

from apple_mail_mcp.applescript import RS, US, OsascriptRunner, quote
from apple_mail_mcp.config import (
    Config,
    advertised_permissions,
    is_all_mail,
    is_trash,
)

FLAG_COLORS = (
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "purple",
    "gray",
)

MAX_BODY_CHARS = 20000

# Mail reports every mailbox with class `container`; an account reports a
# subclass such as `imap account`, so `is account` is false and cannot be used.
_PRELUDE = """
on pad2(n)
	return text -2 thru -1 of ("0" & (n as integer))
end pad2

on isoDate(d)
	if d is missing value then return ""
	return ((year of d) as text) & "-" & my pad2((month of d) as integer) & "-" & ¬
		my pad2(day of d) & "T" & my pad2(hours of d) & ":" & ¬
		my pad2(minutes of d) & ":" & my pad2(seconds of d)
end isoDate

on mbPath(mb)
	tell application "Mail"
		set p to name of mb
		set cur to mb
		repeat
			set par to missing value
			try
				set par to container of cur
			end try
			if par is missing value then exit repeat
			if (class of par) is not container then exit repeat
			set p to (name of par) & "/" & p
			set cur to par
		end repeat
	end tell
	return p
end mbPath
"""


class MailService:
    """Domain operations against Mail.app. Every AppleScript call originates here."""

    def __init__(self, config: Config, runner=None) -> None:
        self._config = config
        self._run = runner or OsascriptRunner(timeout=config.timeout_seconds)

    # -- mailboxes ---------------------------------------------------------

    def list_mailboxes(self) -> list[dict]:
        rows: list[dict] = []
        for account in self._config.accounts:
            script = _PRELUDE + f"""
tell application "Mail"
	set out to ""
	set acc to {account.specifier()}
	repeat with mb in mailboxes of acc
		set out to out & my mbPath(mb) & "{US}" & (unread count of mb) & "{RS}"
	end repeat
	return out
end tell
"""
            for record in _records(self._run(script)):
                path, unread = record[0], record[1]
                permissions = account.permissions_for(path)
                if permissions is None:
                    continue
                rows.append(
                    {
                        "account": account.name,
                        "mailbox": path,
                        "unread_count": int(unread),
                        "is_archive": path == account.archive_mailbox,
                        "permissions": advertised_permissions(
                            path, permissions
                        ).as_dict(),
                    }
                )
        return rows

    # -- reading -----------------------------------------------------------

    def list_messages(
        self,
        account_key: str,
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
        account = self._config.require(account_key, mailbox, "read")
        limit = max(1, min(int(limit), self._config.max_page_size))
        offset = max(0, int(offset))

        setup, clause = _filter_clause(
            unread_only, flagged_only, from_contains, subject_contains, since, before
        )
        target = f"mailbox {quote(mailbox)} of {account.specifier()}"

        if clause:
            select = f"set msgs to (get messages of mb whose {clause})\n\tset total to count of msgs"
            slice_ = "set page to items startIdx thru endIdx of msgs"
        else:
            select = "set total to count of messages of mb"
            slice_ = "set page to (get messages startIdx thru endIdx of mb)"

        script = _PRELUDE + f"""
tell application "Mail"
	set mb to {target}
{setup}	{select}
	set startIdx to {offset + 1}
	set endIdx to {offset + limit}
	if endIdx > total then set endIdx to total
	if startIdx > total then
		return "{US}" & total
	end if
	{slice_}
	set out to ""
	repeat with i from 1 to (count of page)
		set m to item i of page
		set out to out & (id of m) & "{US}" & (read status of m) & "{US}" & ¬
			(flag index of m) & "{US}" & my isoDate(date received of m) & "{US}" & ¬
			(sender of m) & "{US}" & (subject of m) & "{RS}"
	end repeat
	return out & "{US}" & total
end tell
"""
        raw = self._run(script)
        payload, _, total = raw.rpartition(US)
        messages = [
            _summary(account.name, mailbox, record)
            for record in _records(payload)
            if len(record) >= 6
        ]
        return {
            "account": account.name,
            "mailbox": mailbox,
            "total_matching": int(total.strip() or 0),
            "offset": offset,
            "limit": limit,
            "messages": messages,
        }

    def read_message(self, handle: str, mark_read: bool = False) -> dict:
        # The returned expression starts with "" deliberately: AppleScript's `&`
        # yields a *list* when the left operand is not text, and `id of m` is an
        # integer, so `(id of m) & "..."` would return {127614, "..."} instead.
        account_key, mailbox, message_id = parse_handle(handle)
        account = self._config.require(account_key, mailbox, "read")
        if mark_read:
            self._config.require(account_key, mailbox, "mark_read")

        mark = "\n\tset read status of m to true" if mark_read else ""
        script = _PRELUDE + f"""
tell application "Mail"
	set mb to mailbox {quote(mailbox)} of {account.specifier()}
	set m to first message of mb whose id is {message_id}{mark}
	set rcpts to ""
	repeat with r in to recipients of m
		set rcpts to rcpts & (address of r) & " "
	end repeat
	return "" & (id of m) & "{US}" & (message id of m) & "{US}" & (read status of m) & "{US}" & ¬
		(flag index of m) & "{US}" & my isoDate(date received of m) & "{US}" & ¬
		my isoDate(date sent of m) & "{US}" & (sender of m) & "{US}" & rcpts & "{US}" & ¬
		(subject of m) & "{US}" & (content of m)
end tell
"""
        fields = self._run(script).split(US)
        if len(fields) < 10:
            raise RuntimeError(f"Unexpected response reading message {handle}")
        body = fields[9]
        truncated = len(body) > MAX_BODY_CHARS
        return {
            "handle": make_handle(account.name, mailbox, fields[0]),
            "account": account.name,
            "mailbox": mailbox,
            "message_id": fields[1],
            "read": _as_bool(fields[2]),
            "flag_color": _flag_color(fields[3]),
            "date_received": fields[4],
            "date_sent": fields[5],
            "sender": fields[6],
            "to": fields[7].split(),
            "subject": fields[8],
            "body": body[:MAX_BODY_CHARS],
            "body_truncated": truncated,
            "marked_read": mark_read,
        }

    # -- mutations ---------------------------------------------------------

    def set_read_status(self, handle: str, read: bool) -> dict:
        account_key, mailbox, message_id = parse_handle(handle)
        account = self._config.require(account_key, mailbox, "mark_read")
        value = "true" if read else "false"
        self._run(
            _PRELUDE
            + f"""
tell application "Mail"
	set mb to mailbox {quote(mailbox)} of {account.specifier()}
	set m to first message of mb whose id is {message_id}
	set read status of m to {value}
	return "ok"
end tell
"""
        )
        return {
            "handle": make_handle(account.name, mailbox, message_id),
            "read": read,
        }

    def set_flag(self, handle: str, color: str | None) -> dict:
        account_key, mailbox, message_id = parse_handle(handle)
        account = self._config.require(account_key, mailbox, "flag")

        if color is None or color.lower() in ("none", "off", "unflagged"):
            action = "set flagged status of m to false"
            resolved = None
        else:
            key = color.lower()
            if key not in FLAG_COLORS:
                raise ValueError(
                    f"Invalid flag colour '{color}'. "
                    f"Must be one of: {', '.join(FLAG_COLORS)}, none"
                )
            action = (
                "set flagged status of m to true\n"
                f"\tset flag index of m to {FLAG_COLORS.index(key)}"
            )
            resolved = key

        self._run(
            _PRELUDE
            + f"""
tell application "Mail"
	set mb to mailbox {quote(mailbox)} of {account.specifier()}
	set m to first message of mb whose id is {message_id}
	{action}
	return "ok"
end tell
"""
        )
        return {
            "handle": make_handle(account.name, mailbox, message_id),
            "flag_color": resolved,
        }

    def move_message(
        self, handle: str, target_mailbox: str, target_account: str | None = None
    ) -> dict:
        return self._move(handle, target_mailbox, target_account, "move")

    def archive_message(self, handle: str) -> dict:
        account_key, mailbox, _ = parse_handle(handle)
        account = self._config.account(account_key)
        if not account.archive_mailbox:
            raise ValueError(
                f"Account '{account.name}' has no 'archive_mailbox' configured. "
                "Name a real mailbox, such as 'Archive'; on Gmail it must be an "
                "actual label, not the label-less All Mail view."
            )
        if mailbox == account.archive_mailbox:
            raise ValueError(f"Message is already in '{account.archive_mailbox}'")
        return self._move(handle, account.archive_mailbox, account.name, "archive")

    def _move(
        self,
        handle: str,
        target_mailbox: str,
        target_account: str | None,
        operation: str,
    ) -> dict:
        account_key, mailbox, message_id = parse_handle(handle)
        source = self._config.require(account_key, mailbox, "move_from")
        destination_key = target_account or source.name

        if operation == "archive":
            # The archive target is named by the account's own config, so it is
            # trusted for move_to. `parse_config` rejects both of these targets at
            # load; the guards here cover a Config assembled without it.
            destination = self._config.account(destination_key)
            if is_trash(target_mailbox):
                raise ValueError(
                    f"Refusing to archive into '{target_mailbox}'. "
                    "This server does not delete mail."
                )
            if is_all_mail(target_mailbox):
                raise ValueError(
                    f"Refusing to archive into '{target_mailbox}'. "
                    "Every message already appears there, so the move would be a no-op."
                )
        else:
            destination = self._config.require(
                destination_key, target_mailbox, "move_to"
            )

        # Mail reassigns the integer id when a message changes mailbox (observed
        # on Gmail: 127608 became 127638), so the id cannot simply be carried
        # over. Re-find the message by its RFC Message-ID, which does not change,
        # inside the same script rather than paying a second round trip.
        #
        # Nothing after `move m to target` may abort the script: the runner
        # retries the whole thing on -600, which would re-run the move. So the
        # source-side count gets its own try, and `stillThere` is pre-set to the
        # -1 sentinel beforehand so it is defined at the return either way.
        script = _PRELUDE + f"""
tell application "Mail"
	set mb to mailbox {quote(mailbox)} of {source.specifier()}
	set m to first message of mb whose id is {message_id}
	set rfc to message id of m
	set target to mailbox {quote(target_mailbox)} of {destination.specifier()}
	set stillThere to -1
	move m to target
	try
		set stillThere to (count of (messages of mb whose message id is rfc))
	end try
	set newId to ""
	try
		set newId to "" & (id of (first message of target whose message id is rfc))
	end try
	return "" & rfc & "{US}" & newId & "{US}" & stillThere
end tell
"""
        fields = self._run(script).split(US)
        if len(fields) != 3:
            raise RuntimeError(f"Unexpected response moving {handle}")
        rfc, new_id = fields[0], fields[1].strip()
        still_there = _as_count(fields[2])
        verified = still_there == 0

        result = {
            "handle": (
                make_handle(destination.name, target_mailbox, new_id)
                if new_id
                else None
            ),
            "previous_handle": handle,
            "account": destination.name,
            "mailbox": target_mailbox,
            "message_id": rfc.strip(),
            "operation": operation,
            "verified": verified,
        }
        if not new_id:
            result["note"] = (
                "The move command completed, but the message could not be located "
                "in the target yet, most likely because the account is still "
                "syncing. Find it again with list_messages."
            )
        if still_there < 0:
            result["warning"] = (
                f"Mail did not report whether the message left '{mailbox}', so the "
                "move is unconfirmed. Check with list_messages before retrying."
            )
        elif not verified:
            result["warning"] = (
                f"The message is still in '{mailbox}' after the move, so it did not "
                "leave that mailbox. On Gmail this happens when the target is the "
                "label-less All Mail view rather than a real label. It can also mean "
                "the account is still syncing, or that a second copy carrying the "
                "same Message-ID remains there — re-check with list_messages before "
                "retrying."
            )
        return result


# -- handles ---------------------------------------------------------------


def make_handle(account: str, mailbox: str, message_id: str | int) -> str:
    return f"{account}/{mailbox}#{message_id}"


def parse_handle(handle: str) -> tuple[str, str, int]:
    """Split `account/mailbox/path#id`. Mailbox paths may contain '/', ids may not."""
    location, sep, raw_id = handle.rpartition("#")
    if not sep:
        raise ValueError(
            f"Malformed handle '{handle}'. Expected 'account/mailbox#id', "
            "as returned by list_messages."
        )
    account_key, sep, mailbox = location.partition("/")
    if not sep or not mailbox or not account_key:
        raise ValueError(
            f"Malformed handle '{handle}'. Expected 'account/mailbox#id', "
            "as returned by list_messages."
        )
    try:
        message_id = int(raw_id)
    except ValueError:
        raise ValueError(f"Handle '{handle}' has a non-numeric message id") from None
    return account_key, mailbox, message_id


# -- parsing helpers -------------------------------------------------------


def _records(payload: str) -> list[list[str]]:
    return [chunk.split(US) for chunk in payload.split(RS) if chunk.strip()]


def _as_bool(value: str) -> bool:
    return value.strip().lower() == "true"


def _as_count(value: str) -> int:
    """A source-side count, or -1 when Mail reported none.

    Blank or garbled must never read as zero: zero is what marks a move verified,
    and this field exists to keep an unproven move from looking like a proven one.
    """
    try:
        return int(value.strip())
    except ValueError:
        return -1


def _flag_color(value: str) -> str | None:
    index = int(value.strip() or -1)
    if 0 <= index < len(FLAG_COLORS):
        return FLAG_COLORS[index]
    return None


def _summary(account: str, mailbox: str, record: list[str]) -> dict:
    return {
        "handle": make_handle(account, mailbox, record[0]),
        "read": _as_bool(record[1]),
        "flag_color": _flag_color(record[2]),
        "date_received": record[3],
        "sender": record[4],
        "subject": record[5],
    }


def _filter_clause(
    unread_only: bool,
    flagged_only: bool,
    from_contains: str | None,
    subject_contains: str | None,
    since: str | None,
    before: str | None,
) -> tuple[str, str]:
    """Build the AppleScript `whose` clause plus any date setup it needs.

    Filtering runs inside Mail and costs about the same on a 3-message mailbox as
    on a 3400-message one; pulling properties out costs ~0.1s per message. So
    every filter applied here removes work that would otherwise dominate.
    """
    setup = ""
    terms: list[str] = []

    if unread_only:
        terms.append("read status is false")
    if flagged_only:
        terms.append("flagged status is true")
    if from_contains:
        terms.append(f"sender contains {quote(from_contains)}")
    if subject_contains:
        terms.append(f"subject contains {quote(subject_contains)}")
    if since:
        setup += _date_var("sinceDate", since)
        terms.append("date received is greater than or equal to sinceDate")
    if before:
        setup += _date_var("beforeDate", before)
        terms.append("date received is less than beforeDate")

    return setup, " and ".join(terms)


def _date_var(name: str, value: str) -> str:
    """Emit an AppleScript date built from components.

    `date "..."` literals parse against the user's locale, so they are not usable
    from generated script. Day is reset to 1 first so setting the month cannot
    overflow out of a short month.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(value), datetime.min.time())
        except ValueError:
            raise ValueError(
                f"Invalid date '{value}'. Use ISO 8601, e.g. '2026-08-28' "
                "or '2026-08-28T09:00:00'."
            ) from None
    seconds = parsed.hour * 3600 + parsed.minute * 60 + parsed.second
    return (
        f"\tset {name} to current date\n"
        f"\tset day of {name} to 1\n"
        f"\tset year of {name} to {parsed.year}\n"
        f"\tset month of {name} to {parsed.month}\n"
        f"\tset day of {name} to {parsed.day}\n"
        f"\tset time of {name} to {seconds}\n"
    )
