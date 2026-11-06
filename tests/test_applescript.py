from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from apple_mail_mcp.applescript import AppleScriptError, OsascriptRunner, error_code, quote


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["osascript", "-"], returncode=returncode, stdout=stdout, stderr=stderr
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("INBOX", '"INBOX"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("[Gmail]/All Mail", '"[Gmail]/All Mail"'),
    ],
)
def test_quote_escapes_applescript_literals(value, expected):
    assert quote(value) == expected


def test_successful_run_returns_stdout():
    with patch("subprocess.run", return_value=completed(stdout="hello")):
        assert OsascriptRunner()("script") == "hello"


def test_stderr_on_success_is_ignored():
    """osascript logs XPC warnings to stderr even when the script succeeds."""
    noisy = completed(stdout="hello", stderr="Connection Invalid error for service")
    with patch("subprocess.run", return_value=noisy):
        assert OsascriptRunner()("script") == "hello"


def test_failure_raises_with_mail_message():
    failed = completed(returncode=1, stderr="execution error: no such mailbox (-1728)")
    with patch("subprocess.run", return_value=failed):
        with pytest.raises(AppleScriptError, match="-1728"):
            OsascriptRunner()("script")


def test_timeout_becomes_timeout_error():
    with patch(
        "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=5)
    ):
        with pytest.raises(TimeoutError, match="did not respond"):
            OsascriptRunner(timeout=5)("script")


def test_not_running_triggers_launch_and_one_retry():
    """Mail quits itself when idle, so -600 is expected mid-session, not fatal."""
    not_running = completed(returncode=1, stderr="Application isn't running. (-600)")
    # 1: first attempt fails, 2: the `open` call, 3: the launch probe, 4: retry
    calls = [
        not_running,
        completed(stdout=""),
        completed(stdout="ok"),
        completed(stdout="payload"),
    ]

    with patch("subprocess.run", side_effect=calls) as run:
        assert OsascriptRunner()("script") == "payload"

    launch = run.call_args_list[1].args[0]
    assert launch[:4] == ["open", "-g", "-j"] or launch[0] == "open"
    assert "-g" in launch and "-j" in launch, "must not steal focus"
    assert "com.apple.mail" in launch


def test_other_errors_do_not_retry():
    failed = completed(returncode=1, stderr="execution error: nope (-1728)")
    with patch("subprocess.run", return_value=failed) as run:
        with pytest.raises(AppleScriptError):
            OsascriptRunner()("script")
    assert run.call_count == 1


def test_launch_failure_is_reported():
    not_running = completed(returncode=1, stderr="(-600)")
    with patch("subprocess.run", return_value=not_running):
        with pytest.raises(AppleScriptError, match=r"could not be launched \(-600\)"):
            OsascriptRunner(launch_wait=0)("script")


def test_caller_data_holding_not_running_code_does_not_relaunch():
    failed = completed(
        returncode=1,
        stderr='execution error: Can\'t get mailbox "X (-600)" of account "a". (-1728)',
    )
    with patch("subprocess.run", return_value=failed) as run:
        with pytest.raises(AppleScriptError):
            OsascriptRunner()("script")
    assert run.call_count == 1


LIVE_NOT_FOUND = (
    "816:853: execution error: Mail got an error: Can\u2019t get message 1 of mailbox "
    '"[Gmail]/Starred" of account id "270E4F77-90E5-4BBC-B381-E21613D33A30" '
    "whose id = -1743. Invalid index. (-1719)"
)


@pytest.mark.parametrize(
    "message,expected",
    [
        (LIVE_NOT_FOUND, "-1719"),
        ('Can\'t get mailbox "X-1743 (-1743)" of account "a". (-1728)', "-1728"),
        ("Connection Invalid (-609)\nexecution error: nope (-1728)", "-1728"),
        ("execution error: no code here", None),
        ("(-600) trailing text", None),
    ],
)
def test_error_code_reads_only_the_trailing_code(message, expected):
    assert error_code(AppleScriptError(message)) == expected
