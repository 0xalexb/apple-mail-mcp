from __future__ import annotations

import pytest

from apple_mail_mcp.applescript import US
from apple_mail_mcp.mail_service import MailService


def service(config, runner_cls, *responses):
    runner = runner_cls(*responses)
    return MailService(config, runner=runner), runner


# -- read status -----------------------------------------------------------


def test_mark_read_and_unread(config, fake_runner):
    svc, runner = service(config, fake_runner, "ok", "ok")

    assert svc.set_read_status("gmail/INBOX#1", read=True) == {
        "handle": "gmail/INBOX#1",
        "read": True,
    }
    assert "set read status of m to true" in runner.script

    svc.set_read_status("gmail/INBOX#1", read=False)
    assert "set read status of m to false" in runner.script


def test_mark_read_denied_when_capability_off(fake_runner):
    from apple_mail_mcp.config import parse_config

    cfg = parse_config(
        {"accounts": [{"name": "a", "mailboxes": [{"path": "INBOX", "mark_read": False}]}]}
    )
    svc, runner = service(cfg, fake_runner)
    with pytest.raises(ValueError, match="'mark_read' is not permitted"):
        svc.set_read_status("a/INBOX#1", read=True)
    assert runner.scripts == []


# -- flags -----------------------------------------------------------------


@pytest.mark.parametrize(
    "color,index",
    [
        ("red", 0),
        ("orange", 1),
        ("yellow", 2),
        ("green", 3),
        ("blue", 4),
        ("purple", 5),
        ("gray", 6),
    ],
)
def test_set_flag_maps_colors_to_flag_index(config, fake_runner, color, index):
    svc, runner = service(config, fake_runner, "ok")
    result = svc.set_flag("gmail/INBOX#1", color)
    assert result["flag_color"] == color
    assert f"set flag index of m to {index}" in runner.script
    assert "set flagged status of m to true" in runner.script


def test_set_flag_is_case_insensitive(config, fake_runner):
    svc, _ = service(config, fake_runner, "ok")
    assert svc.set_flag("gmail/INBOX#1", "RED")["flag_color"] == "red"


@pytest.mark.parametrize("value", [None, "none", "off"])
def test_clearing_a_flag_unflags(config, fake_runner, value):
    svc, runner = service(config, fake_runner, "ok")
    result = svc.set_flag("gmail/INBOX#1", value)
    assert result["flag_color"] is None
    assert "set flagged status of m to false" in runner.script
    assert "flag index" not in runner.script


def test_invalid_flag_color_is_rejected(config, fake_runner):
    svc, runner = service(config, fake_runner)
    with pytest.raises(ValueError, match="Invalid flag colour"):
        svc.set_flag("gmail/INBOX#1", "chartreuse")
    assert runner.scripts == []


# -- moving ----------------------------------------------------------------


def test_move_returns_the_reassigned_handle(config, fake_runner):
    """Mail gives the message a new integer id in its new mailbox."""
    svc, runner = service(config, fake_runner, f"<abc@example.com>{US}127638")
    result = svc.move_message("gmail/INBOX#127614", "Filed/Insurance")

    assert result["handle"] == "gmail/Filed/Insurance#127638"
    assert result["previous_handle"] == "gmail/INBOX#127614"
    assert result["message_id"] == "<abc@example.com>"
    assert result["operation"] == "move"
    assert "move m to target" in runner.script
    assert "whose message id is rfc" in runner.script


def test_move_reports_when_the_message_cannot_be_relocated(config, fake_runner):
    svc, _ = service(config, fake_runner, f"<abc@example.com>{US}")
    result = svc.move_message("gmail/INBOX#127614", "Filed/x")
    assert result["handle"] is None
    assert "still syncing" in result["note"]


def test_move_requires_move_from_on_the_source(config, fake_runner):
    svc, runner = service(config, fake_runner)
    with pytest.raises(ValueError, match="'move_from' is not permitted"):
        svc.move_message("iCloud/readonly/notes#1", "Archive")
    assert runner.scripts == []


def test_move_requires_move_to_on_the_target(config, fake_runner):
    svc, runner = service(config, fake_runner)
    with pytest.raises(ValueError, match="'move_to' is not permitted"):
        svc.move_message("gmail/INBOX#1", "INBOX")
    assert runner.scripts == []


def test_move_to_unlisted_target_is_denied(config, fake_runner):
    svc, runner = service(config, fake_runner)
    with pytest.raises(ValueError, match="not in the allowlist"):
        svc.move_message("gmail/INBOX#1", "Spam")
    assert runner.scripts == []


def test_move_across_accounts(config, fake_runner):
    svc, runner = service(config, fake_runner, f"<a@b>{US}9")
    result = svc.move_message("gmail/INBOX#5", "Archive", target_account="iCloud")
    assert result["handle"] == "iCloud/Archive#9"
    assert 'account "iCloud"' in runner.script


# -- the no-deletion guarantee --------------------------------------------


def test_move_into_trash_is_refused_even_when_allowlisted(config, fake_runner):
    """[Gmail]/Trash carries move_to in the fixture; the guard must still refuse."""
    svc, runner = service(config, fake_runner)
    with pytest.raises(ValueError, match="does not delete mail"):
        svc.move_message("gmail/INBOX#1", "[Gmail]/Trash")
    assert runner.scripts == []


def test_no_delete_tool_is_exposed():
    from apple_mail_mcp import server

    exposed = {name for name in dir(server) if not name.startswith("_")}
    assert not {n for n in exposed if "delete" in n or "trash" in n}


def test_service_never_emits_a_delete_command(config, fake_runner):
    svc, runner = service(config, fake_runner, "ok", "ok", f"<a@b>{US}2")
    svc.set_read_status("gmail/INBOX#1", read=True)
    svc.set_flag("gmail/INBOX#1", "red")
    svc.move_message("gmail/INBOX#1", "Filed/x")

    for script in runner.scripts:
        assert "delete" not in script.lower()
        assert "deleted status" not in script.lower()


# -- archiving -------------------------------------------------------------


def test_archive_moves_to_the_configured_mailbox(config, fake_runner):
    svc, runner = service(config, fake_runner, f"<a@b>{US}200")
    result = svc.archive_message("gmail/INBOX#127614")

    assert result["handle"] == "gmail/Archive#200"
    assert result["operation"] == "archive"
    assert 'mailbox "Archive"' in runner.script


def test_archive_target_need_not_carry_move_to(config, fake_runner):
    """iCloud's Archive is the account's declared archive; move_to is implied."""
    svc, _ = service(config, fake_runner, f"<a@b>{US}3")
    assert svc.archive_message("iCloud/INBOX#1")["handle"] == "iCloud/Archive#3"


def test_archive_requires_configuration(fake_runner):
    from apple_mail_mcp.config import parse_config

    cfg = parse_config(
        {"accounts": [{"name": "a", "mailboxes": [{"path": "INBOX", "move_from": True}]}]}
    )
    svc, runner = service(cfg, fake_runner)
    with pytest.raises(ValueError, match="no 'archive_mailbox' configured"):
        svc.archive_message("a/INBOX#1")
    assert runner.scripts == []


def test_archiving_an_archived_message_is_rejected(config, fake_runner):
    from apple_mail_mcp.config import parse_config

    cfg = parse_config(
        {
            "accounts": [
                {
                    "name": "a",
                    "archive_mailbox": "Archive",
                    "mailboxes": [{"path": "Archive", "move_from": True}],
                }
            ]
        }
    )
    svc, _ = service(cfg, fake_runner)
    with pytest.raises(ValueError, match="already in 'Archive'"):
        svc.archive_message("a/Archive#1")


def test_archive_into_trash_is_refused(fake_runner):
    from apple_mail_mcp.config import parse_config

    cfg = parse_config(
        {
            "accounts": [
                {
                    "name": "a",
                    "archive_mailbox": "Trash",
                    "mailboxes": [{"path": "INBOX", "move_from": True}],
                }
            ]
        }
    )
    svc, runner = service(cfg, fake_runner)
    with pytest.raises(ValueError, match="does not delete mail"):
        svc.archive_message("a/INBOX#1")
    assert runner.scripts == []


# -- tool layer ------------------------------------------------------------


def test_write_tools_delegate_to_the_service():
    from unittest.mock import MagicMock, patch

    from apple_mail_mcp import server

    svc = MagicMock()
    with patch.object(server, "_get_service", return_value=svc):
        server.mark_read("a/INBOX#1")
        server.mark_unread("a/INBOX#1")
        server.set_flag("a/INBOX#1", "red")
        server.move_message("a/INBOX#1", "Done")
        server.archive_message("a/INBOX#1")

    svc.set_read_status.assert_any_call("a/INBOX#1", read=True)
    svc.set_read_status.assert_any_call("a/INBOX#1", read=False)
    svc.set_flag.assert_called_once_with("a/INBOX#1", "red")
    svc.move_message.assert_called_once_with("a/INBOX#1", "Done", None)
    svc.archive_message.assert_called_once_with("a/INBOX#1")
