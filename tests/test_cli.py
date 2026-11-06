from __future__ import annotations

import argparse
import inspect
import json
import os
import subprocess
import sys
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from apple_mail_mcp import cli, server
from apple_mail_mcp.applescript import AppleScriptError, OsascriptRunner
from apple_mail_mcp.mail_service import MailService
from tests.conftest import FakeRunner

VERBS = {
    "mailboxes",
    "messages",
    "read",
    "mark-read",
    "mark-unread",
    "flag",
    "move",
    "archive",
    "flag-colors",
}

HELP = {"-h", "--help"}

EXPECTED_FLAGS = {
    "mailboxes": set(),
    "messages": {
        "--account",
        "--mailbox",
        "--limit",
        "--offset",
        "--unread-only",
        "--no-unread-only",
        "--flagged-only",
        "--no-flagged-only",
        "--from-contains",
        "--subject-contains",
        "--since",
        "--before",
    },
    "read": {"--handle", "--mark-read", "--no-mark-read"},
    "mark-read": {"--handle"},
    "mark-unread": {"--handle"},
    "archive": {"--handle"},
    "flag": {"--handle", "--color"},
    "move": {"--handle", "--target-mailbox", "--target-account"},
    "flag-colors": set(),
}

TOOL_FOR_VERB = {
    "mailboxes": server.list_mailboxes,
    "messages": server.list_messages,
    "read": server.read_message,
    "mark-read": server.mark_read,
    "mark-unread": server.mark_unread,
    "flag": server.set_flag,
    "move": server.move_message,
    "archive": server.archive_message,
    "flag-colors": server.flag_colors,
}

HANDLE = "gmail/INBOX#127614"
NOT_RUNNING = "execution error: Application isn't running. (-600)"
LIVE_NOT_FOUND = (
    "816:853: execution error: Mail got an error: Can\u2019t get message 1 of mailbox "
    '"[Gmail]/Starred" of account id "270E4F77-90E5-4BBC-B381-E21613D33A30" '
    "whose id = -1743. Invalid index. (-1719)"
)


@pytest.fixture
def svc():
    mock = MagicMock()
    with (
        patch.object(cli, "_service", return_value=mock),
        patch.object(server, "_get_service", return_value=mock),
    ):
        yield mock


def _run(capsys, *argv):
    rc = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    parser = cli._build_parser()
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return dict(action.choices)


def _options(parser: argparse.ArgumentParser) -> set[str]:
    return {s for a in parser._actions for s in a.option_strings}


def _file_flags(parser: argparse.ArgumentParser) -> list[str]:
    found = []
    for action in parser._actions:
        names = [*action.option_strings, action.dest, str(action.metavar or "")]
        if any(w in n.lower() for n in names for w in ("file", "path", "input", "output")):
            found.append(action.dest)
        elif isinstance(action.type, argparse.FileType):
            found.append(action.dest)
    return found


class RaisingRunner:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def __call__(self, script: str) -> str:
        raise self.error


def _with_runner(config, runner):
    return patch.object(cli, "_service", lambda: MailService(config, runner=runner))


class TestVerbSurface:
    def test_verb_set_is_exactly_c5(self):
        assert set(_subparsers()) == VERBS

    def test_no_ping_and_no_delete_verb(self):
        for verb in _subparsers():
            assert verb != "ping"
            assert "delete" not in verb
            assert "trash" not in verb

    def test_every_verb_maps_to_a_tool(self):
        assert set(TOOL_FOR_VERB) == VERBS

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--version"])

        assert exc.value.code == 0
        assert capsys.readouterr().out == f"apple-mail {version('apple-mail-mcp')}\n"


class TestNoFileFlag:
    def test_flag_sets_are_exact(self):
        parsers = _subparsers()

        assert {verb: _options(p) - HELP for verb, p in parsers.items()} == EXPECTED_FLAGS
        assert all(HELP <= _options(p) for p in parsers.values())

    def test_flags_are_the_tool_parameters(self):
        for verb, tool in TOOL_FOR_VERB.items():
            expected = set()
            for name, param in inspect.signature(tool).parameters.items():
                flag = "--" + name.replace("_", "-")
                expected.add(flag)
                if param.annotation == "bool":
                    expected.add("--no-" + flag[2:])
            assert _options(_subparsers()[verb]) - HELP == expected, verb

    def test_no_flag_names_a_file(self):
        for verb, parser in _subparsers().items():
            assert _file_flags(parser) == [], verb

    def test_no_parser_reads_arguments_from_a_file(self):
        assert cli._build_parser().fromfile_prefix_chars is None
        for verb, parser in _subparsers().items():
            assert parser.fromfile_prefix_chars is None, verb

    def test_at_sign_value_is_taken_literally(self, svc, capsys, tmp_path):
        target = tmp_path / "handle.txt"
        target.write_text("gmail/INBOX#1")
        svc.read_message.return_value = {}

        rc, _, _ = _run(capsys, "read", "--handle", f"@{target}")

        assert rc == 0
        svc.read_message.assert_called_once_with(f"@{target}", mark_read=False)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"args": ("--config-file",)},
            {"args": ("--out",), "kwargs": {"dest": "output_path"}},
            {"args": ("--source",), "kwargs": {"metavar": "FILE"}},
            {"args": ("--source",), "kwargs": {"type": argparse.FileType}},
        ],
    )
    @pytest.mark.filterwarnings("ignore::PendingDeprecationWarning")
    def test_detector_catches_a_file_flag(self, kwargs):
        parser = argparse.ArgumentParser(prog="x")
        options = dict(kwargs.get("kwargs", {}))
        if options.get("type") is argparse.FileType:
            options["type"] = argparse.FileType("r")
        parser.add_argument(*kwargs["args"], **options)

        assert _file_flags(parser)


_MESSAGES_ARGV = [
    "messages",
    "--account",
    "gmail",
    "--mailbox",
    "INBOX",
    "--limit",
    "10",
    "--offset",
    "5",
    "--unread-only",
    "--flagged-only",
    "--from-contains",
    "boss",
    "--subject-contains",
    "invoice",
    "--since",
    "2026-08-01",
    "--before",
    "2026-09-01",
]
_MESSAGES_KWARGS = {
    "account": "gmail",
    "mailbox": "INBOX",
    "limit": 10,
    "offset": 5,
    "unread_only": True,
    "flagged_only": True,
    "from_contains": "boss",
    "subject_contains": "invoice",
    "since": "2026-08-01",
    "before": "2026-09-01",
}

JSON_CASES = [
    pytest.param(["mailboxes"], {}, "list_mailboxes", [{"mailbox": "INBOX"}], id="mailboxes"),
    pytest.param(
        _MESSAGES_ARGV, _MESSAGES_KWARGS, "list_messages", {"messages": [], "total": 0}, id="messages"
    ),
    pytest.param(
        ["messages", "--account", "gmail", "--mailbox", "INBOX"],
        {"account": "gmail", "mailbox": "INBOX"},
        "list_messages",
        {"messages": [{"handle": HANDLE}], "total": 1},
        id="messages-defaults",
    ),
    pytest.param(
        ["read", "--handle", HANDLE, "--mark-read"],
        {"handle": HANDLE, "mark_read": True},
        "read_message",
        {"handle": HANDLE, "body": "héllo"},
        id="read",
    ),
    pytest.param(
        ["read", "--handle", HANDLE],
        {"handle": HANDLE},
        "read_message",
        {"handle": HANDLE, "body": "x"},
        id="read-defaults",
    ),
    pytest.param(
        ["mark-read", "--handle", HANDLE], {"handle": HANDLE}, "set_read_status", {"read": True}, id="mark-read"
    ),
    pytest.param(
        ["mark-unread", "--handle", HANDLE],
        {"handle": HANDLE},
        "set_read_status",
        {"read": False},
        id="mark-unread",
    ),
    pytest.param(
        ["flag", "--handle", HANDLE, "--color", "red"],
        {"handle": HANDLE, "color": "red"},
        "set_flag",
        {"flag": "red"},
        id="flag",
    ),
    pytest.param(
        ["flag", "--handle", HANDLE], {"handle": HANDLE}, "set_flag", {"flag": None}, id="unflag"
    ),
    pytest.param(
        ["move", "--handle", HANDLE, "--target-mailbox", "Filed/Finance", "--target-account", "iCloud"],
        {"handle": HANDLE, "target_mailbox": "Filed/Finance", "target_account": "iCloud"},
        "move_message",
        {"handle": "iCloud/Filed/Finance#9", "verified": True},
        id="move",
    ),
    pytest.param(
        ["move", "--handle", HANDLE, "--target-mailbox", "Filed/Finance"],
        {"handle": HANDLE, "target_mailbox": "Filed/Finance"},
        "move_message",
        {"handle": "gmail/Filed/Finance#9", "verified": False, "warning": "w"},
        id="move-defaults",
    ),
    pytest.param(
        ["archive", "--handle", HANDLE],
        {"handle": HANDLE},
        "archive_message",
        {"handle": "gmail/Archive#3", "verified": True},
        id="archive",
    ),
]


class TestJsonMatchesTools:
    @pytest.mark.parametrize(("argv", "tool_kwargs", "method", "result"), JSON_CASES)
    def test_cli_equals_tool(self, svc, capsys, argv, tool_kwargs, method, result):
        getattr(svc, method).return_value = result
        tool = TOOL_FOR_VERB[argv[0]]

        rc, out, err = _run(capsys, *argv)
        cli_calls = list(svc.mock_calls)
        svc.reset_mock()
        tool_result = tool(**tool_kwargs)

        assert (rc, err) == (0, "")
        assert out.endswith("\n") and out.count("\n") == 1
        assert json.loads(out) == tool_result == result
        assert cli_calls == list(svc.mock_calls)
        assert cli_calls

    def test_flag_colors_needs_no_service(self, svc, capsys):
        rc, out, err = _run(capsys, "flag-colors")

        assert (rc, err) == (0, "")
        assert json.loads(out) == server.flag_colors()
        cli._service.assert_not_called()

    def test_non_ascii_is_not_escaped(self, svc, capsys):
        svc.read_message.return_value = {"body": "héllo"}

        _, out, _ = _run(capsys, "read", "--handle", HANDLE)

        assert "héllo" in out

    def test_empty_list_is_an_empty_array(self, svc, capsys):
        svc.list_mailboxes.return_value = []

        rc, out, _ = _run(capsys, "mailboxes")

        assert (rc, out) == (0, "[]\n")


class TestFlagMapping:
    def test_defaults(self, svc, capsys):
        svc.list_messages.return_value = {}

        _run(capsys, "messages", "--account", "gmail", "--mailbox", "INBOX")

        svc.list_messages.assert_called_once_with(
            account_key="gmail",
            mailbox="INBOX",
            limit=25,
            offset=0,
            unread_only=False,
            flagged_only=False,
            from_contains=None,
            subject_contains=None,
            since=None,
            before=None,
        )

    def test_every_flag(self, svc, capsys):
        svc.list_messages.return_value = {}

        _run(capsys, *_MESSAGES_ARGV)

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
            before="2026-09-01",
        )

    def test_negated_boolean(self, svc, capsys):
        svc.list_messages.return_value = {}

        _run(capsys, "messages", "--account", "a", "--mailbox", "m", "--no-unread-only")

        assert svc.list_messages.call_args.kwargs["unread_only"] is False

    @pytest.mark.parametrize(("extra", "expected"), [([], False), (["--mark-read"], True)])
    def test_read_mark_read(self, svc, capsys, extra, expected):
        svc.read_message.return_value = {}

        _run(capsys, "read", "--handle", HANDLE, *extra)

        svc.read_message.assert_called_once_with(HANDLE, mark_read=expected)

    def test_flag_without_color_passes_none(self, svc, capsys):
        svc.set_flag.return_value = {}

        _run(capsys, "flag", "--handle", HANDLE)

        svc.set_flag.assert_called_once_with(HANDLE, None)

    def test_move_without_target_account_passes_none(self, svc, capsys):
        svc.move_message.return_value = {}

        _run(capsys, "move", "--handle", HANDLE, "--target-mailbox", "Archive")

        svc.move_message.assert_called_once_with(HANDLE, "Archive", None)


class TestExitOne:
    @pytest.mark.parametrize(
        "argv",
        [
            ["read"],
            ["messages", "--account", "gmail"],
            ["messages", "--account", "a", "--mailbox", "m", "--limit", "many"],
            ["nonsense"],
            [],
        ],
    )
    def test_usage_error_is_1_not_2(self, svc, argv):
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)

        assert exc.value.code == 1

    def test_value_error_from_service(self, svc, capsys):
        svc.read_message.side_effect = ValueError("Bad handle 'x'")

        rc, out, err = _run(capsys, "read", "--handle", "x")

        assert (rc, out) == (1, "")
        assert err == "apple-mail: Bad handle 'x'\n"

    def test_runtime_error_from_service(self, svc, capsys):
        svc.read_message.side_effect = RuntimeError("Unexpected response from Mail")

        rc, out, err = _run(capsys, "read", "--handle", HANDLE)

        assert (rc, out) == (1, "")
        assert "Unexpected response" in err

    def test_missing_config_file(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("APPLE_MAIL_MCP_CONFIG", str(tmp_path / "absent.yml"))

        rc, out, err = _run(capsys, "mailboxes")

        assert (rc, out) == (1, "")
        assert err.startswith("apple-mail: No config at")

    def test_allowlist_refusal_end_to_end(self, config, capsys):
        with _with_runner(config, FakeRunner()):
            rc, out, err = _run(
                capsys, "messages", "--account", "gmail", "--mailbox", "Secret"
            )

        assert (rc, out) == (1, "")
        assert "not in the allowlist" in err

    def test_non_serializable_result_propagates_with_stdout_empty(self, svc, capsys):
        svc.list_mailboxes.return_value = {1, 2}

        with pytest.raises(TypeError):
            cli.main(["mailboxes"])

        assert capsys.readouterr().out == ""

    def test_type_error_from_the_service_is_not_swallowed(self, svc):
        svc.read_message.side_effect = TypeError("bug")

        with pytest.raises(TypeError, match="bug"):
            cli.main(["read", "--handle", HANDLE])

    def test_not_found_with_caller_data_in_it_is_1_not_2(self, config, capsys):
        error = AppleScriptError(LIVE_NOT_FOUND)
        with _with_runner(config, RaisingRunner(error)):
            rc, out, err = _run(capsys, "read", "--handle", HANDLE)

        assert (rc, out) == (1, "")
        assert "Invalid index" in err

    def test_mailbox_name_holding_an_access_code_is_1_not_2(self, config, capsys):
        error = AppleScriptError('Can\'t get mailbox "X-1743 (-1743)" of account "a". (-1728)')
        with _with_runner(config, RaisingRunner(error)):
            rc, out, err = _run(capsys, "messages", "--account", "gmail", "--mailbox", "INBOX")

        assert (rc, out) == (1, "")
        assert "(-1728)" in err

    @pytest.mark.parametrize("flag", ["--since", "--before"])
    def test_empty_date_flag_is_a_usage_error(self, config, capsys, flag):
        runner = FakeRunner()
        with _with_runner(config, runner):
            rc, out, err = _run(
                capsys, "messages", "--account", "gmail", "--mailbox", "INBOX", flag, ""
            )

        assert (rc, out) == (1, "")
        assert "Invalid date" in err
        assert runner.scripts == []

    def test_negative_message_id_is_bad_input(self, config, capsys):
        runner = FakeRunner()
        with _with_runner(config, runner):
            rc, out, err = _run(capsys, "read", "--handle", "gmail/INBOX#-1743")

        assert (rc, out) == (1, "")
        assert "negative message id" in err
        assert runner.scripts == []

    def test_other_applescript_error(self, config, capsys):
        with _with_runner(config, RaisingRunner(AppleScriptError("syntax error (-2740)"))):
            rc, out, err = _run(capsys, "mailboxes")

        assert (rc, out) == (1, "")
        assert "-2740" in err


class TestConfigLoading:
    @pytest.fixture
    def config_file(self, monkeypatch, tmp_path):
        path = tmp_path / "config.yml"
        monkeypatch.setenv("APPLE_MAIL_MCP_CONFIG", str(path))
        return path

    def _assert_exit_one(self, capsys):
        rc, out, err = _run(capsys, "mailboxes")

        assert (rc, out) == (1, "")
        assert err.startswith("apple-mail: ")

    def test_malformed_yaml(self, config_file, capsys):
        config_file.write_text("accounts: [unclosed\n")

        self._assert_exit_one(capsys)

    def test_path_is_a_directory(self, config_file, capsys):
        config_file.mkdir()

        self._assert_exit_one(capsys)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
    def test_unreadable_file_is_1_not_2(self, config_file, capsys):
        config_file.write_text("accounts: []\n")
        config_file.chmod(0o000)
        try:
            self._assert_exit_one(capsys)
        finally:
            config_file.chmod(0o600)

    @pytest.mark.parametrize("error", [yaml.YAMLError("bad"), OSError("disk")])
    def test_load_config_failures(self, capsys, error):
        with patch.object(cli, "load_config", side_effect=error):
            self._assert_exit_one(capsys)

    def test_permission_error_outside_load_config_stays_2(self, svc, capsys):
        svc.list_mailboxes.side_effect = PermissionError("denied")

        rc, out, _ = _run(capsys, "mailboxes")

        assert (rc, out) == (2, "")


class TestExitTwo:
    @pytest.mark.parametrize("error", [TimeoutError("Mail did not respond"), PermissionError("denied")])
    def test_service_access_failure(self, svc, capsys, error):
        svc.list_mailboxes.side_effect = error

        rc, out, err = _run(capsys, "mailboxes")

        assert (rc, out) == (2, "")
        assert str(error) in err

    def test_mail_not_running_and_launch_fails(self, config, capsys):
        failed = SimpleNamespace(returncode=1, stdout="", stderr=NOT_RUNNING)
        runner = OsascriptRunner(launch_wait=0)

        with (
            _with_runner(config, runner),
            patch("apple_mail_mcp.applescript.subprocess.run", return_value=failed),
        ):
            rc, out, err = _run(capsys, "mailboxes")

        assert (rc, out) == (2, "")
        assert "could not be launched" in err

    @pytest.mark.parametrize(
        "message",
        [
            NOT_RUNNING,
            "Not authorized to send Apple events to Mail. (-1743)",
            "Mail got an error: AppleEvent timed out. (-1712)",
        ],
    )
    def test_access_flavoured_applescript_error(self, config, capsys, message):
        with _with_runner(config, RaisingRunner(AppleScriptError(message))):
            rc, out, err = _run(capsys, "mailboxes")

        assert (rc, out) == (2, "")
        assert message in err


_ISOLATION = (
    "import sys, apple_mail_mcp.cli{extra}; "
    "assert 'apple_mail_mcp.server' not in sys.modules"
)


def test_cli_does_not_import_server():
    result = subprocess.run([sys.executable, "-c", _ISOLATION.format(extra="")])

    assert result.returncode == 0


def test_isolation_check_detects_server_import():
    result = subprocess.run(
        [sys.executable, "-c", _ISOLATION.format(extra=", apple_mail_mcp.server")],
        capture_output=True,
    )

    assert result.returncode != 0
