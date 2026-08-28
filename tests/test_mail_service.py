from __future__ import annotations

import pytest

from apple_mail_mcp.applescript import RS, US
from apple_mail_mcp.mail_service import MailService, make_handle, parse_handle


def service(config, runner_cls, *responses):
    runner = runner_cls(*responses)
    return MailService(config, runner=runner), runner


# -- handles ---------------------------------------------------------------


def test_handle_round_trip():
    handle = make_handle("iCloud", "Filed/Finance", 42)
    assert handle == "iCloud/Filed/Finance#42"
    assert parse_handle(handle) == ("iCloud", "Filed/Finance", 42)


def test_handle_keeps_bracketed_mailbox_paths():
    handle = make_handle("gmail", "[Gmail]/All Mail", 7)
    assert parse_handle(handle) == ("gmail", "[Gmail]/All Mail", 7)


@pytest.mark.parametrize("bad", ["no-hash", "acct#12", "acct/mbox#abc", "/mbox#1"])
def test_malformed_handles_are_rejected(bad):
    with pytest.raises(ValueError):
        parse_handle(bad)


# -- listing ---------------------------------------------------------------


def test_list_mailboxes_hides_mailboxes_outside_the_allowlist(config, fake_runner):
    gmail = f"INBOX{US}3{RS}Spam{US}99{RS}Filed/Insurance{US}0{RS}"
    icloud = f"INBOX{US}1{RS}private/diary{US}5{RS}"
    svc, _ = service(config, fake_runner, gmail, icloud)

    paths = [(row["account"], row["mailbox"]) for row in svc.list_mailboxes()]
    assert ("gmail", "INBOX") in paths
    assert ("gmail", "Filed/Insurance") in paths
    assert ("gmail", "Spam") not in paths
    assert ("iCloud", "private/diary") not in paths


def test_list_mailboxes_reports_permissions_and_archive(config, fake_runner):
    svc, _ = service(config, fake_runner, f"[Gmail]/All Mail{US}0{RS}", "")
    # Not allowlisted, so it is filtered out despite being the archive target.
    assert svc.list_mailboxes() == []


def test_list_messages_parses_records(config, fake_runner):
    payload = (
        f"127614{US}false{US}-1{US}2026-08-28T09:15:00{US}a@example.com{US}Hi{RS}"
        f"127608{US}true{US}2{US}2026-08-27T10:00:00{US}b@example.com{US}Re: Hi{RS}"
        f"{US}2"
    )
    svc, _ = service(config, fake_runner, payload)
    result = svc.list_messages("gmail", "INBOX")

    assert result["total_matching"] == 2
    first, second = result["messages"]
    assert first["handle"] == "gmail/INBOX#127614"
    assert first["read"] is False
    assert first["flag_color"] is None
    assert second["read"] is True
    assert second["flag_color"] == "yellow"


def test_list_messages_empty_page(config, fake_runner):
    svc, _ = service(config, fake_runner, f"{US}0")
    result = svc.list_messages("gmail", "INBOX")
    assert result["messages"] == []
    assert result["total_matching"] == 0


def test_list_messages_clamps_limit_to_max_page_size(config, fake_runner):
    svc, runner = service(config, fake_runner, f"{US}0")
    result = svc.list_messages("gmail", "INBOX", limit=5000)
    assert result["limit"] == config.max_page_size
    assert f"set endIdx to {config.max_page_size}" in runner.script


def test_list_messages_builds_filter_clause(config, fake_runner):
    svc, runner = service(config, fake_runner, f"{US}0")
    svc.list_messages(
        "gmail",
        "INBOX",
        unread_only=True,
        flagged_only=True,
        from_contains="boss@example.com",
        subject_contains="invoice",
    )
    script = runner.script
    assert "read status is false" in script
    assert "flagged status is true" in script
    assert 'sender contains "boss@example.com"' in script
    assert 'subject contains "invoice"' in script
    assert " and " in script


def test_list_messages_without_filters_slices_in_mail(config, fake_runner):
    svc, runner = service(config, fake_runner, f"{US}0")
    svc.list_messages("gmail", "INBOX", limit=10, offset=20)
    script = runner.script
    assert "whose" not in script
    assert "set startIdx to 21" in script
    assert "set endIdx to 30" in script


def test_list_messages_forces_list_resolution(config, fake_runner):
    """`repeat with m in (messages ... of mb)` re-resolves the list on every
    property access; measured 64s vs 2.6s for 20 messages."""
    svc, runner = service(config, fake_runner, f"{US}0")
    svc.list_messages("gmail", "INBOX")
    assert "get messages startIdx thru endIdx of mb" in runner.script
    assert "repeat with i from 1 to (count of page)" in runner.script


def test_since_filter_builds_a_locale_safe_date(config, fake_runner):
    svc, runner = service(config, fake_runner, f"{US}0")
    svc.list_messages("gmail", "INBOX", since="2026-08-28T09:30:00")
    script = runner.script
    assert "set day of sinceDate to 1" in script
    assert "set year of sinceDate to 2026" in script
    assert "set month of sinceDate to 8" in script
    assert "set day of sinceDate to 28" in script
    assert "set time of sinceDate to 34200" in script
    assert 'date "' not in script


def test_bare_date_is_accepted(config, fake_runner):
    svc, runner = service(config, fake_runner, f"{US}0")
    svc.list_messages("gmail", "INBOX", before="2026-08-28")
    assert "set time of beforeDate to 0" in runner.script


def test_invalid_date_is_rejected(config, fake_runner):
    svc, _ = service(config, fake_runner, f"{US}0")
    with pytest.raises(ValueError, match="Invalid date"):
        svc.list_messages("gmail", "INBOX", since="last tuesday")


def test_list_messages_denied_on_unlisted_mailbox(config, fake_runner):
    svc, runner = service(config, fake_runner)
    with pytest.raises(ValueError, match="not in the allowlist"):
        svc.list_messages("gmail", "Spam")
    assert runner.scripts == []


def test_account_id_specifier_used_when_configured(config, fake_runner):
    svc, runner = service(config, fake_runner, f"{US}0")
    svc.list_messages("gmail", "INBOX")
    assert 'account id "A1B2C3D4-0000-0000-0000-000000000000"' in runner.script


# -- reading ---------------------------------------------------------------


def _read_payload(body="Hello there", flag="-1", read="false"):
    return US.join(
        [
            "127614",
            "<abc@example.com>",
            read,
            flag,
            "2026-08-28T09:15:00",
            "2026-08-28T09:14:00",
            "a@example.com",
            "me@example.com you@example.com",
            "Subject line",
            body,
        ]
    )


def test_read_message_parses_all_fields(config, fake_runner):
    svc, runner = service(config, fake_runner, _read_payload())
    result = svc.read_message("gmail/INBOX#127614")

    assert result["message_id"] == "<abc@example.com>"
    assert result["subject"] == "Subject line"
    assert result["to"] == ["me@example.com", "you@example.com"]
    assert result["body"] == "Hello there"
    assert result["body_truncated"] is False
    assert result["marked_read"] is False
    assert "set read status of m to true" not in runner.script


def test_read_message_can_mark_read(config, fake_runner):
    svc, runner = service(config, fake_runner, _read_payload(read="true"))
    result = svc.read_message("gmail/INBOX#127614", mark_read=True)
    assert result["marked_read"] is True
    assert "set read status of m to true" in runner.script


def test_read_message_truncates_long_bodies(config, fake_runner):
    from apple_mail_mcp.mail_service import MAX_BODY_CHARS

    svc, _ = service(config, fake_runner, _read_payload(body="x" * (MAX_BODY_CHARS + 5)))
    result = svc.read_message("gmail/INBOX#127614")
    assert result["body_truncated"] is True
    assert len(result["body"]) == MAX_BODY_CHARS


def test_read_message_resolves_by_id(config, fake_runner):
    svc, runner = service(config, fake_runner, _read_payload())
    svc.read_message("gmail/INBOX#127614")
    assert "first message of mb whose id is 127614" in runner.script


def test_read_script_forces_text_concatenation(config, fake_runner):
    """AppleScript '&' builds a list unless the left operand is text, and
    `id of m` is an integer."""
    svc, runner = service(config, fake_runner, _read_payload())
    svc.read_message("gmail/INBOX#127614")
    assert '\treturn "" & (id of m)' in runner.script
