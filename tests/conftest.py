from __future__ import annotations

import pytest

from apple_mail_mcp.config import parse_config


class FakeRunner:
    """Stands in for OsascriptRunner: records scripts, replays canned output."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.scripts: list[str] = []

    def __call__(self, script: str) -> str:
        self.scripts.append(script)
        return self.responses.pop(0) if self.responses else "ok"

    @property
    def script(self) -> str:
        return self.scripts[-1]


@pytest.fixture
def fake_runner():
    return FakeRunner


@pytest.fixture
def config():
    return parse_config(
        {
            "defaults": {"read": True, "mark_read": True, "flag": True},
            "accounts": [
                {
                    "name": "gmail",
                    "id": "A1B2C3D4-0000-0000-0000-000000000000",
                    "archive_mailbox": "Archive",
                    "mailboxes": [
                        {"path": "INBOX", "move_from": True},
                        {"path": "Filed/*", "move_to": True},
                        {"path": "[Gmail]/Trash", "move_to": True},
                    ],
                },
                {
                    "name": "iCloud",
                    "archive_mailbox": "Archive",
                    "mailboxes": [
                        {"path": "INBOX", "move_from": True},
                        {"path": "Archive", "move_to": True},
                        {"path": "readonly/*"},
                    ],
                },
            ],
        }
    )
