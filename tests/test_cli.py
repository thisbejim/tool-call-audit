from __future__ import annotations

import json
from pathlib import Path

from tool_call_audit.cli import main

ROOT = Path(__file__).parents[1]


def test_cli_text_success(capsys) -> None:
    code = main(["audit", str(ROOT / "examples" / "valid-chat.json")])

    captured = capsys.readouterr()
    assert code == 0
    assert "PASS" in captured.out
    assert "provider=chat-completions" in captured.out
    assert "findings: none" in captured.out


def test_cli_text_failure(capsys) -> None:
    code = main(["audit", str(ROOT / "examples" / "broken-responses.json")])

    captured = capsys.readouterr()
    assert code == 1
    assert "FAIL" in captured.out
    assert "CALL_ID_MISSING" in captured.out
    assert "ARGUMENTS_SCHEMA_MISMATCH" in captured.out


def test_cli_jsonl_with_external_tools(capsys) -> None:
    code = main(
        [
            "audit",
            str(ROOT / "fixtures" / "captures.jsonl"),
            "--tools",
            str(ROOT / "examples" / "tools.json"),
            "--format",
            "json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 1
    assert payload["summary"]["records"] == 2
    assert payload["summary"]["errors"] == 2


def test_cli_strict_promotes_warnings(capsys) -> None:
    # A response without calls is clean; an unknown input envelope is still a
    # valid captured response. This assertion primarily locks the flag's API.
    code = main(["audit", str(ROOT / "examples" / "valid-chat.json"), "--strict"])

    captured = capsys.readouterr()
    assert code == 0
    assert "PASS" in captured.out
