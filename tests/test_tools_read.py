from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apple_mail_mcp import server


@pytest.fixture
def svc():
    mock = MagicMock()
    with patch.object(server, "_get_service", return_value=mock):
        yield mock


def test_list_mailboxes_delegates(svc):
    svc.list_mailboxes.return_value = [{"mailbox": "INBOX"}]
    assert server.list_mailboxes() == [{"mailbox": "INBOX"}]


def test_list_messages_passes_every_filter_through(svc):
    server.list_messages(
        account="gmail",
        mailbox="INBOX",
        limit=10,
        offset=5,
        unread_only=True,
        flagged_only=True,
        from_contains="boss",
        subject_contains="invoice",
        since="2026-08-01",
        before="2026-08-28",
    )
    svc.list_messages.assert_called_once_with(
        account_key="gmail",
        mailbox="INBOX",
        limit=10,
        offset=5,
        unread_only=True,
        flagged_only=True,
        from_contains="boss",
        subject_contains="invoice",
        since="2026-08-01",
        before="2026-08-28",
    )


def test_list_messages_defaults_are_conservative(svc):
    server.list_messages(account="gmail", mailbox="INBOX")
    kwargs = svc.list_messages.call_args.kwargs
    assert kwargs["limit"] == 25
    assert kwargs["offset"] == 0
    assert kwargs["unread_only"] is False
    assert kwargs["since"] is None


def test_read_message_does_not_mark_read_by_default(svc):
    server.read_message("gmail/INBOX#1")
    svc.read_message.assert_called_once_with("gmail/INBOX#1", mark_read=False)


def test_read_message_can_opt_into_marking_read(svc):
    server.read_message("gmail/INBOX#1", mark_read=True)
    svc.read_message.assert_called_once_with("gmail/INBOX#1", mark_read=True)


def test_service_is_created_once():
    server._service = None
    with patch.object(server, "MailService") as factory, patch.object(
        server, "load_config"
    ):
        server._get_service()
        server._get_service()
    assert factory.call_count == 1
    server._service = None


def test_every_tool_has_a_docstring():
    tools = [
        server.ping,
        server.list_mailboxes,
        server.list_messages,
        server.read_message,
        server.mark_read,
        server.mark_unread,
        server.set_flag,
        server.move_message,
        server.archive_message,
        server.flag_colors,
    ]
    for tool in tools:
        assert tool.__doc__, f"{tool.__name__} needs a docstring for the tool schema"


def test_move_tools_document_verified():
    """The docstring is all a calling agent reads before deciding the move worked."""
    for tool in (server.move_message, server.archive_message):
        assert "verified: true" in tool.__doc__
        assert "verified: false" in tool.__doc__
        assert "warning" in tool.__doc__
        # verified: false is two states, and the agent must not read it as failure
        assert "did not report a count" in tool.__doc__
        assert "list_messages" in tool.__doc__
        # verified: true is Mail's local view, not a server-side commitment
        assert "at the time of the move" in tool.__doc__
    assert "unverified" in server.archive_message.__doc__
    assert "archived" in server.archive_message.__doc__
