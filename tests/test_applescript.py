from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from apple_mail_mcp.applescript import AppleScriptError, OsascriptRunner, quote


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
        with pytest.raises(AppleScriptError, match="could not be launched"):
            OsascriptRunner(launch_wait=0)("script")
