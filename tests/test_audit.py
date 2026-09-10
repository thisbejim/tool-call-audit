from __future__ import annotations

import json
from pathlib import Path

from tool_call_audit import audit_record, audit_records, load_tool_schemas

ROOT = Path(__file__).parents[1]


def read_fixture(name: str) -> dict:
    return json.loads((ROOT / "examples" / name).read_text())


def test_valid_chat_fixture_passes() -> None:
    report = audit_records([read_fixture("valid-chat.json")])

    assert report.valid
    assert report.calls == 1
    assert report.records[0].provider == "chat-completions"
    assert report.findings == []


def test_broken_chat_reports_schema_and_json_findings_without_echoing_values() -> None:
    report = audit_records([read_fixture("broken-chat.json")])

    assert not report.valid
    codes = [finding.code for finding in report.findings]
    assert "ARGUMENTS_SCHEMA_MISMATCH" in codes
    assert "ARGUMENTS_INVALID_JSON" in codes
    assert "kelvin" not in " ".join(finding.message for finding in report.findings)
    assert "42" not in " ".join(finding.message for finding in report.findings)


def test_valid_responses_fixture_passes() -> None:
    report = audit_records([read_fixture("valid-responses.json")])

    assert report.valid
    assert report.records[0].provider == "responses"
    assert report.calls == 1


def test_responses_missing_call_id_is_an_error() -> None:
    report = audit_records([read_fixture("broken-responses.json")])

    assert not report.valid
    assert {finding.code for finding in report.findings} >= {
        "CALL_ID_MISSING",
        "ARGUMENTS_SCHEMA_MISMATCH",
    }


def test_responses_paths_use_the_actual_envelope() -> None:
    record = {
        "tools": {"lookup": {"type": "object", "required": ["query"]}},
        "response": {
            "output": [
                {
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "lookup",
                    "arguments": "{}",
                }
            ]
        },
    }

    report = audit_records([record])
    finding = next(f for f in report.findings if f.code == "ARGUMENTS_SCHEMA_MISMATCH")
    assert finding.path == "$.output[0].arguments"


def test_malformed_provider_envelope_is_not_a_clean_pass() -> None:
    choices_report = audit_records([{"response": {"choices": {}}}])
    unknown_report = audit_records([{"response": {"metadata": {"trace": "x"}}}])

    assert choices_report.findings[0].code == "CHOICES_NOT_ARRAY"
    assert not choices_report.valid
    assert unknown_report.findings[0].code == "NO_SUPPORTED_CALLS"
    assert unknown_report.warnings == 1


def test_unknown_tool_and_duplicate_call_id_are_reported() -> None:
    record = {
        "tools": {"known": {"type": "object"}},
        "response": {
            "tool_calls": [
                {"id": "same", "name": "unknown", "arguments": "{}"},
                {"id": "same", "name": "known", "arguments": "{}"},
            ]
        },
    }

    report = audit_records([record])
    assert {finding.code for finding in report.findings} >= {
        "TOOL_UNKNOWN",
        "CALL_ID_DUPLICATE",
    }


def test_json_schema_nested_paths_are_stable() -> None:
    record = {
        "tools": [
            {
                "name": "lookup",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filters": {
                            "type": "object",
                            "properties": {"limit": {"type": "integer"}},
                            "required": ["limit"],
                        }
                    },
                    "required": ["filters"],
                },
            }
        ],
        "response": {
            "type": "function_call",
            "call_id": "c1",
            "name": "lookup",
            "arguments": '{"filters":{"limit":"ten"}}',
        },
    }

    report = audit_records([record])
    finding = next(f for f in report.findings if f.code == "ARGUMENTS_SCHEMA_MISMATCH")
    assert finding.path == '$.arguments["filters"]["limit"]'


def test_jsonl_style_records_can_share_external_tool_map() -> None:
    tools = {"get_weather": {"type": "object", "required": ["city"]}}
    records = [
        {"response": {"tool_calls": [{"name": "get_weather", "arguments": '{"city":"Perth"}'}]}},
        {"response": {"tool_calls": [{"name": "get_weather", "arguments": "{}"}]}},
    ]

    report = audit_records(records, tools)
    assert report.records[0].findings == []
    assert report.records[1].findings[0].code == "ARGUMENTS_SCHEMA_MISMATCH"
    assert report.records[1].findings[0].record == 2


def test_tool_definition_shapes_are_normalized() -> None:
    tools = load_tool_schemas(
        [
            {"type": "function", "name": "responses", "parameters": {"type": "object"}},
            {"name": "mcp", "inputSchema": {"type": "object"}},
            {"type": "function", "function": {"name": "chat", "parameters": {"type": "object"}}},
        ]
    )

    assert set(tools) == {"responses", "mcp", "chat"}


def test_malformed_record_does_not_raise() -> None:
    result = audit_record("not an object")  # type: ignore[arg-type]

    assert result.errors == 1
    assert result.findings[0].code == "RECORD_NOT_OBJECT"


def test_report_json_shape_is_stable() -> None:
    report = audit_records([read_fixture("valid-chat.json")], source="fixture.json")
    payload = report.as_dict()

    assert payload["schema_version"] == 1
    assert payload["status"] == "pass"
    assert payload["source"] == "fixture.json"
    assert payload["summary"] == {"records": 1, "calls": 1, "errors": 0, "warnings": 0}
