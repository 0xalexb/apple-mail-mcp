from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

CONFIG_ENV_VAR = "APPLE_MAIL_MCP_CONFIG"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "apple-mail-mcp" / "config.yml"

_PERMISSION_FIELDS = ("read", "mark_read", "flag", "move_from", "move_to")

# Never a move target, even when a rule grants move_to. Deletion is out of scope
# for this server, and moving to Trash is deletion by another name.
_TRASH_LEAVES = frozenset(
    {"trash", "deleted messages", "deleted items", "bin", "junk"}
)


@dataclass(frozen=True)
class Permissions:
    read: bool = True
    mark_read: bool = True
    flag: bool = True
    move_from: bool = False
    move_to: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {name: getattr(self, name) for name in _PERMISSION_FIELDS}


@dataclass(frozen=True)
class MailboxRule:
    pattern: str
    permissions: Permissions


@dataclass(frozen=True)
class AccountConfig:
    name: str
    account_id: str | None
    archive_mailbox: str | None
    mailboxes: tuple[MailboxRule, ...]

    def matches(self, key: str) -> bool:
        return key == self.name or (
            self.account_id is not None and key == self.account_id
        )

    def specifier(self) -> str:
        """The AppleScript account reference, preferring the stable id."""
        if self.account_id:
            return f"account id {_as_literal(self.account_id)}"
        return f"account {_as_literal(self.name)}"

    def permissions_for(self, path: str) -> Permissions | None:
        """First matching rule wins; None means the mailbox is not allowed at all."""
        for rule in self.mailboxes:
            if matches(path, rule.pattern):
                return rule.permissions
        return None


@dataclass(frozen=True)
class Config:
    accounts: tuple[AccountConfig, ...]
    timeout_seconds: float = 120.0
    max_page_size: int = 100

    def account(self, key: str) -> AccountConfig:
        for account in self.accounts:
            if account.matches(key):
                return account
        allowed = ", ".join(a.name for a in self.accounts) or "<none>"
        raise ValueError(
            f"Account '{key}' is not in the allowlist. Configured accounts: {allowed}"
        )

    def resolve(self, account_key: str, path: str) -> tuple[AccountConfig, Permissions]:
        account = self.account(account_key)
        permissions = account.permissions_for(path)
        if permissions is None:
            raise ValueError(
                f"Mailbox '{path}' of account '{account.name}' is not in the allowlist"
            )
        return account, permissions

    def require(self, account_key: str, path: str, capability: str) -> AccountConfig:
        account, permissions = self.resolve(account_key, path)
        if not getattr(permissions, capability):
            raise ValueError(
                f"'{capability}' is not permitted on mailbox '{path}' "
                f"of account '{account.name}'"
            )
        refusal = _refusal(path, capability)
        if refusal:
            raise ValueError(refusal)
        return account


def matches(path: str, pattern: str) -> bool:
    """Glob a mailbox path, treating only '*' and '?' as wildcards.

    '*' spans '/', so 'work/*' covers the whole subtree beneath it.

    Not fnmatch: Gmail names its system mailboxes '[Gmail]/All Mail' and
    '[Gmail]/Trash', and fnmatch would read '[Gmail]' as a character class, so
    such a pattern would silently match nothing.
    """
    escaped = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
    return re.fullmatch(escaped, path) is not None


def is_trash(path: str) -> bool:
    return path.rsplit("/", 1)[-1].strip().lower() in _TRASH_LEAVES


def _refusal(path: str, capability: str) -> str | None:
    """The reason this mailbox may not be used this way, or None when it may.

    Single-sourced so that `require` and `advertised_permissions` cannot drift:
    a capability that is refused here is never advertised as available.
    """
    if capability == "move_to" and is_trash(path):
        return (
            f"Refusing to move messages into '{path}'. This server does not delete mail."
        )
    return None


def advertised_permissions(path: str, permissions: Permissions) -> Permissions:
    """Capabilities a caller can actually use, for reporting rather than enforcing.

    A rule may grant move_to on Trash; `require` refuses it regardless. Reporting
    the raw grant hands an agent a capability that throws on first use.
    """
    masked = {field: False for field in _PERMISSION_FIELDS if _refusal(path, field)}
    return replace(permissions, **masked) if masked else permissions


def _as_literal(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV_VAR)
    return Path(override).expanduser() if override else DEFAULT_CONFIG_PATH


def load_config(path: Path | None = None) -> Config:
    target = path or config_path()
    if not target.exists():
        raise ValueError(
            f"No config at {target}. This server denies every mailbox by default; "
            f"create the file or set {CONFIG_ENV_VAR}. See config.example.yml."
        )
    raw = yaml.safe_load(target.read_text()) or {}
    return parse_config(raw)


def parse_config(raw: dict) -> Config:
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a mapping")

    defaults = _permissions(raw.get("defaults") or {}, Permissions())
    accounts_raw = raw.get("accounts")
    if not accounts_raw:
        raise ValueError("Config must list at least one account under 'accounts'")

    accounts = tuple(_account(entry, defaults) for entry in accounts_raw)
    return Config(
        accounts=accounts,
        timeout_seconds=float(raw.get("timeout_seconds", 120)),
        max_page_size=int(raw.get("max_page_size", 100)),
    )


def _account(entry: dict, defaults: Permissions) -> AccountConfig:
    if not isinstance(entry, dict):
        raise ValueError("Each account entry must be a mapping")
    name = entry.get("name")
    if not name:
        raise ValueError("Each account entry needs a 'name'")

    account_defaults = _permissions(entry.get("defaults") or {}, defaults)
    rules = []
    for mailbox in entry.get("mailboxes") or []:
        if isinstance(mailbox, str):
            rules.append(MailboxRule(mailbox, account_defaults))
            continue
        if not isinstance(mailbox, dict):
            raise ValueError("Each mailbox entry must be a string or a mapping")
        pattern = mailbox.get("path")
        if not pattern:
            raise ValueError(f"Mailbox entry for account '{name}' needs a 'path'")
        rules.append(MailboxRule(pattern, _permissions(mailbox, account_defaults)))

    if not rules:
        raise ValueError(f"Account '{name}' lists no mailboxes, so nothing is allowed")

    archive_mailbox = entry.get("archive_mailbox")
    if archive_mailbox:
        archive_mailbox = str(archive_mailbox)
        if is_trash(archive_mailbox):
            raise ValueError(
                f"Account '{name}' sets archive_mailbox to '{archive_mailbox}'. "
                f"Archiving is not deletion; name a real mailbox such as 'Archive'."
            )

    return AccountConfig(
        name=str(name),
        account_id=entry.get("id"),
        archive_mailbox=archive_mailbox,
        mailboxes=tuple(rules),
    )


def _permissions(entry: dict, base: Permissions) -> Permissions:
    overrides = {
        field: bool(entry[field]) for field in _PERMISSION_FIELDS if field in entry
    }
    return replace(base, **overrides)
