from __future__ import annotations

import pytest

from apple_mail_mcp.config import is_trash, load_config, parse_config


def test_account_lookup_by_name_and_id(config):
    assert config.account("gmail").name == "gmail"
    assert config.account("A1B2C3D4-0000-0000-0000-000000000000").name == "gmail"


def test_unknown_account_is_denied(config):
    with pytest.raises(ValueError, match="not in the allowlist"):
        config.account("not-configured")


def test_unlisted_mailbox_is_denied(config):
    with pytest.raises(ValueError, match="not in the allowlist"):
        config.resolve("gmail", "Spam")


def test_glob_patterns_match(config):
    _, permissions = config.resolve("gmail", "Filed/Insurance")
    assert permissions.move_to is True


def test_first_matching_rule_wins():
    cfg = parse_config(
        {
            "accounts": [
                {
                    "name": "a",
                    "mailboxes": [
                        {"path": "work/*", "move_to": True},
                        {"path": "*", "move_to": False},
                    ],
                }
            ]
        }
    )
    assert cfg.resolve("a", "work/x")[1].move_to is True
    assert cfg.resolve("a", "other")[1].move_to is False


def test_defaults_apply_and_are_overridden(config):
    _, permissions = config.resolve("iCloud", "readonly/notes")
    assert permissions.read is True
    assert permissions.move_from is False

    _, inbox = config.resolve("iCloud", "INBOX")
    assert inbox.move_from is True


def test_account_defaults_override_global_defaults():
    cfg = parse_config(
        {
            "defaults": {"flag": True},
            "accounts": [
                {"name": "a", "defaults": {"flag": False}, "mailboxes": ["INBOX"]}
            ],
        }
    )
    assert cfg.resolve("a", "INBOX")[1].flag is False


def test_require_rejects_missing_capability(config):
    with pytest.raises(ValueError, match="'move_from' is not permitted"):
        config.require("iCloud", "readonly/notes", "move_from")


def test_require_grants_permitted_capability(config):
    assert config.require("gmail", "INBOX", "move_from").name == "gmail"


def test_trash_is_never_a_move_target(config):
    with pytest.raises(ValueError, match="does not delete mail"):
        config.require("gmail", "[Gmail]/Trash", "move_to")


@pytest.mark.parametrize(
    "path",
    ["Trash", "[Gmail]/Trash", "a/b/Deleted Messages", "JUNK", "Deleted Items", "Bin"],
)
def test_is_trash_matches_known_names(path):
    assert is_trash(path)


@pytest.mark.parametrize("path", ["INBOX", "Archive", "work/trashcan ideas"])
def test_is_trash_ignores_other_names(path):
    assert not is_trash(path)


def test_account_specifier_prefers_id(config):
    assert 'account id "A1B2C3D4' in config.account("gmail").specifier()
    assert config.account("iCloud").specifier() == 'account "iCloud"'


def test_string_mailbox_entry_uses_defaults():
    cfg = parse_config({"accounts": [{"name": "a", "mailboxes": ["INBOX"]}]})
    assert cfg.resolve("a", "INBOX")[1].read is True


def test_account_without_mailboxes_is_rejected():
    with pytest.raises(ValueError, match="lists no mailboxes"):
        parse_config({"accounts": [{"name": "a", "mailboxes": []}]})


def test_config_without_accounts_is_rejected():
    with pytest.raises(ValueError, match="at least one account"):
        parse_config({})


def test_missing_config_file_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="denies every mailbox by default"):
        load_config(tmp_path / "nope.yml")


def test_load_config_reads_yaml(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        "accounts:\n"
        "  - name: iCloud\n"
        "    archive_mailbox: Archive\n"
        "    mailboxes:\n"
        "      - path: INBOX\n"
        "        move_from: true\n"
    )
    cfg = load_config(path)
    assert cfg.account("iCloud").archive_mailbox == "Archive"
    assert cfg.resolve("iCloud", "INBOX")[1].move_from is True


def test_bracketed_gmail_names_are_literal():
    cfg = parse_config(
        {"accounts": [{"name": "g", "mailboxes": [{"path": "[Gmail]/All Mail"}]}]}
    )
    assert cfg.resolve("g", "[Gmail]/All Mail")[1].read is True
    with pytest.raises(ValueError, match="not in the allowlist"):
        cfg.resolve("g", "G/All Mail")


def test_wildcard_spans_nested_paths():
    cfg = parse_config(
        {"accounts": [{"name": "a", "mailboxes": [{"path": "Filed/*"}]}]}
    )
    assert cfg.resolve("a", "Filed/Finance")[1].read is True
    assert cfg.resolve("a", "Filed/Finance/Statements")[1].read is True
    with pytest.raises(ValueError, match="not in the allowlist"):
        cfg.resolve("a", "other/Finance")
