from __future__ import annotations

import argparse
import json
import sys
from importlib.metadata import version

import yaml

from apple_mail_mcp.applescript import AppleScriptError
from apple_mail_mcp.config import load_config
from apple_mail_mcp.mail_service import FLAG_COLORS, MailService

EXIT_OK = 0
EXIT_BAD_INPUT = 1
EXIT_NO_ACCESS = 2

_ACCESS_MARKERS = (
    "-600",
    "-1743",
    "-1712",
    "Mail is not running and could not be launched",
)


class _Parser(argparse.ArgumentParser):
    # argparse exits 2 on a usage error, which callers would read as "access denied".
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(EXIT_BAD_INPUT)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="apple-mail",
        description="Apple Mail from the shell. JSON on stdout, errors on stderr.",
        epilog=(
            "Exit codes: 0 ok, 1 not found, bad input or refused by the allowlist config, "
            "2 Mail not reachable, access denied or timed out."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"apple-mail {version('apple-mail-mcp')}"
    )
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    boolean = argparse.BooleanOptionalAction

    sub.add_parser("mailboxes", help="allowed mailboxes with unread counts and permissions")

    messages = sub.add_parser("messages", help="list messages in one mailbox, newest first")
    messages.add_argument("--account", required=True)
    messages.add_argument("--mailbox", required=True)
    messages.add_argument("--limit", type=int, default=25)
    messages.add_argument("--offset", type=int, default=0)
    messages.add_argument("--unread-only", action=boolean, default=False)
    messages.add_argument("--flagged-only", action=boolean, default=False)
    messages.add_argument("--from-contains")
    messages.add_argument("--subject-contains")
    messages.add_argument("--since")
    messages.add_argument("--before")

    read = sub.add_parser("read", help="one message's headers and body")
    read.add_argument("--handle", required=True)
    read.add_argument("--mark-read", action=boolean, default=False)

    for name, text in (
        ("mark-read", "mark a message as read"),
        ("mark-unread", "mark a message as unread"),
        ("archive", "move a message to its account's archive mailbox"),
    ):
        sub.add_parser(name, help=text).add_argument("--handle", required=True)

    flag = sub.add_parser("flag", help="set or clear a message's flag")
    flag.add_argument("--handle", required=True)
    flag.add_argument("--color")

    move = sub.add_parser("move", help="move a message to another mailbox")
    move.add_argument("--handle", required=True)
    move.add_argument("--target-mailbox", required=True)
    move.add_argument("--target-account")

    sub.add_parser("flag-colors", help="the flag colors Apple Mail supports")
    return parser


def _service() -> MailService:
    try:
        config = load_config()
    except (yaml.YAMLError, OSError) as exc:
        raise ValueError(f"cannot load config: {exc}") from exc
    return MailService(config)


def _run(args: argparse.Namespace) -> object:
    if args.command == "flag-colors":
        return list(FLAG_COLORS)
    service = _service()
    if args.command == "mailboxes":
        return service.list_mailboxes()
    if args.command == "messages":
        return service.list_messages(
            account_key=args.account,
            mailbox=args.mailbox,
            limit=args.limit,
            offset=args.offset,
            unread_only=args.unread_only,
            flagged_only=args.flagged_only,
            from_contains=args.from_contains,
            subject_contains=args.subject_contains,
            since=args.since,
            before=args.before,
        )
    if args.command == "read":
        return service.read_message(args.handle, mark_read=args.mark_read)
    if args.command == "mark-read":
        return service.set_read_status(args.handle, read=True)
    if args.command == "mark-unread":
        return service.set_read_status(args.handle, read=False)
    if args.command == "flag":
        return service.set_flag(args.handle, args.color)
    if args.command == "move":
        return service.move_message(args.handle, args.target_mailbox, args.target_account)
    return service.archive_message(args.handle)


def _mail_unreachable(exc: AppleScriptError) -> bool:
    return any(marker in str(exc) for marker in _ACCESS_MARKERS)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        text = json.dumps(_run(args), ensure_ascii=False) + "\n"
    except (PermissionError, TimeoutError) as exc:
        print(f"apple-mail: {exc}", file=sys.stderr)
        return EXIT_NO_ACCESS
    except AppleScriptError as exc:
        print(f"apple-mail: {exc}", file=sys.stderr)
        return EXIT_NO_ACCESS if _mail_unreachable(exc) else EXIT_BAD_INPUT
    except (ValueError, RuntimeError, TypeError) as exc:
        print(f"apple-mail: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    sys.stdout.write(text)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
