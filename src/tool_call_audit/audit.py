"""Core, deterministic tool-call auditing logic.

The module deliberately treats model output as data. It parses JSON arguments but
never imports, evaluates, or executes them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError

JSON = Any


@dataclass(frozen=True)
class Finding:
    """A stable, machine-readable audit finding."""

    code: str
    severity: str
    message: str
    path: str = "$"
    record: int | None = None
    call: int | None = None

    def as_dict(self) -> dict[str, JSON]:
        result: dict[str, JSON] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
        }
        if self.record is not None:
            result["record"] = self.record
        if self.call is not None:
            result["call"] = self.call
        return result


@dataclass(frozen=True)
class CallResult:
    """The normalized shape of one provider-emitted function call."""

    name: str | None
    raw_arguments: JSON
    call_id: str | None
    path: str
    provider: str


@dataclass
class RecordResult:
    """Audit results for one captured response."""

    record: int
    provider: str
    calls: int
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(f.severity == "error" for f in self.findings)

    @property
    def warnings(self) -> int:
        return sum(f.severity == "warning" for f in self.findings)

    def as_dict(self) -> dict[str, JSON]:
        return {
            "record": self.record,
            "provider": self.provider,
            "calls": self.calls,
            "findings": [finding.as_dict() for finding in self.findings],
        }


@dataclass
class AuditReport:
    """The complete report returned by :func:`audit_records`."""

    source: str | None
    records: list[RecordResult]

    @property
    def findings(self) -> list[Finding]:
        return [finding for result in self.records for finding in result.findings]

    @property
    def errors(self) -> int:
        return sum(f.severity == "error" for f in self.findings)

    @property
    def warnings(self) -> int:
        return sum(f.severity == "warning" for f in self.findings)

    @property
    def calls(self) -> int:
        return sum(result.calls for result in self.records)

    @property
    def valid(self) -> bool:
        return self.errors == 0

    def as_dict(self) -> dict[str, JSON]:
        return {
            "schema_version": 1,
            "status": "pass" if self.valid else "fail",
            "source": self.source,
            "summary": {
                "records": len(self.records),
                "calls": self.calls,
                "errors": self.errors,
                "warnings": self.warnings,
            },
            "records": [record.as_dict() for record in self.records],
        }


def load_tool_schemas(tools: JSON) -> dict[str, Mapping[str, JSON]]:
    """Normalize common tool-definition envelopes to ``name -> JSON Schema``.

    Supported definitions are OpenAI Chat Completions tools, OpenAI Responses
    function tools, bare ``{"name", "parameters"}`` definitions, and MCP-style
    ``{"name", "inputSchema"}`` definitions. A mapping of names to schemas is
    also accepted for small local fixtures.

    ``ValueError`` is raised for malformed definitions. The CLI converts these
    errors into a stable finding instead of exposing a traceback.
    """

    if tools is None:
        return {}
    if isinstance(tools, Mapping):
        if "tools" in tools:
            tools = tools["tools"]
        elif _looks_like_tool_definition(tools):
            tools = [tools]
        else:
            normalized: dict[str, Mapping[str, JSON]] = {}
            for name, schema in tools.items():
                if not isinstance(name, str) or not isinstance(schema, Mapping):
                    raise ValueError("tool map keys must be names and values must be schemas")
                normalized[name] = schema
            return normalized
    if not isinstance(tools, Sequence) or isinstance(tools, (str, bytes, bytearray)):
        raise ValueError("tools must be an array or a name-to-schema object")

    normalized = {}
    for index, definition in enumerate(tools):
        if not isinstance(definition, Mapping):
            raise ValueError(f"tool definition {index + 1} must be an object")
        name, schema = _definition_name_and_schema(definition)
        if not name:
            raise ValueError(f"tool definition {index + 1} has no name")
        if not isinstance(schema, Mapping):
            raise ValueError(f"tool {name!r} parameters must be a JSON Schema object")
        if name in normalized:
            raise ValueError(f"duplicate tool name {name!r}")
        normalized[name] = schema
    return normalized


def audit_record(
    record: Mapping[str, JSON],
    tools: JSON = None,
    *,
    record_index: int = 1,
) -> RecordResult:
    """Audit one fixture record or provider response.

    A record may be a manifest with ``tools`` and ``response``/``payload`` or a
    raw provider response when ``tools`` is supplied separately.
    """

    findings: list[Finding] = []
    if not isinstance(record, Mapping):
        return RecordResult(
            record=record_index,
            provider="unknown",
            calls=0,
            findings=[
                Finding(
                    "RECORD_NOT_OBJECT",
                    "error",
                    "capture record must be a JSON object",
                    record=record_index,
                )
            ],
        )

    record_tools = tools if tools is not None else record.get("tools")
    try:
        schemas = load_tool_schemas(record_tools)
    except ValueError as exc:
        schemas = {}
        findings.append(
            Finding(
                "TOOL_DEFINITIONS_INVALID",
                "error",
                str(exc),
                path="$.tools",
                record=record_index,
            )
        )

    payload = _payload_from_record(record)
    provider, calls, extraction_findings = _extract_calls(payload)
    findings.extend(
        _with_location(finding, record_index=record_index) for finding in extraction_findings
    )

    # Validate each declared schema once. A broken schema should be reported even
    # when its tool is not selected in this particular capture.
    valid_schemas: dict[str, Mapping[str, JSON]] = {}
    for name, schema in schemas.items():
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            findings.append(
                Finding(
                    "TOOL_SCHEMA_INVALID",
                    "error",
                    f"tool {name!r} has an invalid JSON Schema",
                    path=f"$.tools[{_json_key(name)}]",
                    record=record_index,
                )
            )
        else:
            valid_schemas[name] = schema

    seen_ids: dict[str, int] = {}
    for call_index, call in enumerate(calls, start=1):
        call_findings = _audit_call(call, valid_schemas, seen_ids, call_index)
        findings.extend(
            _with_location(
                finding,
                record_index=record_index,
                call_index=call_index,
            )
            for finding in call_findings
        )

    return RecordResult(record=record_index, provider=provider, calls=len(calls), findings=findings)


def audit_records(
    records: Iterable[Mapping[str, JSON]],
    tools: JSON = None,
    *,
    source: str | None = None,
) -> AuditReport:
    """Audit an iterable of records and return one stable aggregate report."""

    results = [
        audit_record(record, tools, record_index=index)
        for index, record in enumerate(records, start=1)
    ]
    return AuditReport(source=source, records=results)


def _payload_from_record(record: Mapping[str, JSON]) -> JSON:
    for key in ("response", "payload", "capture"):
        if key in record:
            return record[key]
    return record


def _definition_name_and_schema(definition: Mapping[str, JSON]) -> tuple[str | None, JSON]:
    function = definition.get("function")
    if isinstance(function, Mapping):
        return function.get("name"), function.get("parameters")
    if "inputSchema" in definition:
        return definition.get("name"), definition.get("inputSchema")
    return definition.get("name"), definition.get("parameters")


def _looks_like_tool_definition(value: Mapping[str, JSON]) -> bool:
    return any(key in value for key in ("name", "function", "inputSchema", "parameters"))


def _extract_calls(payload: JSON) -> tuple[str, list[CallResult], list[Finding]]:
    findings: list[Finding] = []
    if not isinstance(payload, Mapping):
        return (
            "unknown",
            [],
            [Finding("RESPONSE_NOT_OBJECT", "error", "response payload must be a JSON object")],
        )

    calls: list[CallResult] = []
    if "choices" in payload:
        if not isinstance(payload.get("choices"), Sequence) or isinstance(
            payload.get("choices"), (str, bytes, bytearray)
        ):
            return (
                "chat-completions",
                [],
                [
                    Finding(
                        "CHOICES_NOT_ARRAY",
                        "error",
                        "choices must be an array",
                        path="$.choices",
                    )
                ],
            )
        provider = "chat-completions"
        for choice_index, choice in enumerate(payload["choices"]):
            if not isinstance(choice, Mapping):
                findings.append(
                    Finding(
                        "CHOICE_NOT_OBJECT",
                        "error",
                        "choice must be an object",
                        path=f"$.choices[{choice_index}]",
                    )
                )
                continue
            message = choice.get("message", choice)
            if not isinstance(message, Mapping):
                findings.append(
                    Finding(
                        "MESSAGE_NOT_OBJECT",
                        "error",
                        "choice message must be an object",
                        path=f"$.choices[{choice_index}].message",
                    )
                )
                continue
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, Sequence) and not isinstance(
                tool_calls, (str, bytes, bytearray)
            ):
                for call_index, raw_call in enumerate(tool_calls):
                    path = f"$.choices[{choice_index}].message.tool_calls[{call_index}]"
                    calls.append(_chat_call(raw_call, path))
            elif "tool_calls" in message:
                findings.append(
                    Finding(
                        "TOOL_CALLS_NOT_ARRAY",
                        "error",
                        "message.tool_calls must be an array",
                        path=f"$.choices[{choice_index}].message.tool_calls",
                    )
                )
            if "function_call" in message:
                path = f"$.choices[{choice_index}].message.function_call"
                calls.append(_legacy_call(message["function_call"], path))
        return provider, calls, findings

    if "output" in payload:
        if not isinstance(payload.get("output"), Sequence) or isinstance(
            payload.get("output"), (str, bytes, bytearray)
        ):
            return (
                "responses",
                [],
                [
                    Finding(
                        "OUTPUT_NOT_ARRAY",
                        "error",
                        "output must be an array",
                        path="$.output",
                    )
                ],
            )
        provider = "responses"
        for output_index, item in enumerate(payload["output"]):
            if not isinstance(item, Mapping):
                continue
            if item.get("type") == "function_call":
                path = f"$.output[{output_index}]"
                calls.append(
                    CallResult(
                        name=_string_or_none(item.get("name")),
                        raw_arguments=item.get("arguments"),
                        call_id=_string_or_none(item.get("call_id")),
                        path=path,
                        provider=provider,
                    )
                )
        return provider, calls, findings

    if "tool_calls" in payload:
        provider = "generic"
        tool_calls = payload["tool_calls"]
        if not isinstance(tool_calls, Sequence) or isinstance(tool_calls, (str, bytes, bytearray)):
            findings.append(
                Finding(
                    "TOOL_CALLS_NOT_ARRAY",
                    "error",
                    "tool_calls must be an array",
                    path="$.tool_calls",
                )
            )
            return provider, calls, findings
        for call_index, raw_call in enumerate(tool_calls):
            calls.append(_chat_call(raw_call, f"$.tool_calls[{call_index}]"))
        return provider, calls, findings

    if "calls" in payload:
        provider = "generic"
        raw_calls = payload["calls"]
        if not isinstance(raw_calls, Sequence) or isinstance(raw_calls, (str, bytes, bytearray)):
            findings.append(
                Finding(
                    "CALLS_NOT_ARRAY",
                    "error",
                    "calls must be an array",
                    path="$.calls",
                )
            )
            return provider, calls, findings
        for call_index, raw_call in enumerate(raw_calls):
            calls.append(_chat_call(raw_call, f"$.calls[{call_index}]"))
        return provider, calls, findings

    if payload.get("type") == "function_call":
        return (
            "responses",
            [
                CallResult(
                    name=_string_or_none(payload.get("name")),
                    raw_arguments=payload.get("arguments"),
                    call_id=_string_or_none(payload.get("call_id")),
                    path="$",
                    provider="responses",
                )
            ],
            findings,
        )

    if not findings:
        findings.append(
            Finding(
                "NO_SUPPORTED_CALLS",
                "warning",
                "response has no recognized tool-call envelope",
            )
        )
    return "unknown", calls, findings


def _chat_call(raw_call: JSON, path: str) -> CallResult:
    if not isinstance(raw_call, Mapping):
        return CallResult(name=None, raw_arguments=None, call_id=None, path=path, provider="chat")
    function = raw_call.get("function")
    if isinstance(function, Mapping):
        return CallResult(
            name=_string_or_none(function.get("name")),
            raw_arguments=function.get("arguments"),
            call_id=_string_or_none(raw_call.get("id")),
            path=path,
            provider="chat",
        )
    return CallResult(
        name=_string_or_none(raw_call.get("name")),
        raw_arguments=raw_call.get("arguments"),
        call_id=_string_or_none(raw_call.get("id", raw_call.get("call_id"))),
        path=path,
        provider="generic",
    )


def _legacy_call(raw_call: JSON, path: str) -> CallResult:
    if not isinstance(raw_call, Mapping):
        return CallResult(name=None, raw_arguments=None, call_id=None, path=path, provider="chat")
    return CallResult(
        name=_string_or_none(raw_call.get("name")),
        raw_arguments=raw_call.get("arguments"),
        call_id=None,
        path=path,
        provider="chat",
    )


def _audit_call(
    call: CallResult,
    schemas: Mapping[str, Mapping[str, JSON]],
    seen_ids: dict[str, int],
    call_index: int,
) -> list[Finding]:
    findings: list[Finding] = []
    if call.name is None:
        findings.append(
            Finding("TOOL_NAME_MISSING", "error", "tool call has no function name", path=call.path)
        )
        return findings

    if call.call_id is not None:
        if call.call_id in seen_ids:
            findings.append(
                Finding(
                    "CALL_ID_DUPLICATE",
                    "error",
                    f"call id is duplicated from call {seen_ids[call.call_id]}",
                    path=_call_id_path(call),
                )
            )
        else:
            seen_ids[call.call_id] = call_index
    elif call.provider == "responses":
        findings.append(
            Finding(
                "CALL_ID_MISSING",
                "error",
                "Responses function calls must include call_id",
                path=call.path,
            )
        )

    schema = schemas.get(call.name)
    if schema is None:
        findings.append(
            Finding(
                "TOOL_UNKNOWN",
                "error",
                f"tool {call.name!r} is not declared in the tool schemas",
                path=_name_path(call),
            )
        )

    arguments, argument_findings = _decode_arguments(call)
    findings.extend(argument_findings)
    if arguments is None or schema is None or argument_findings:
        return findings

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(arguments), key=lambda error: list(error.path))
    for error in errors:
        findings.append(
            Finding(
                "ARGUMENTS_SCHEMA_MISMATCH",
                "error",
                _safe_schema_message(error),
                path=_join_path(_argument_path(call), error.path),
            )
        )
    return findings


def _decode_arguments(call: CallResult) -> tuple[JSON, list[Finding]]:
    path = _argument_path(call)
    if call.raw_arguments is None:
        return None, [
            Finding("ARGUMENTS_MISSING", "error", "tool call has no arguments", path=path)
        ]
    if isinstance(call.raw_arguments, str):
        try:
            return json.loads(call.raw_arguments), []
        except json.JSONDecodeError as exc:
            return None, [
                Finding(
                    "ARGUMENTS_INVALID_JSON",
                    "error",
                    f"tool arguments are not valid JSON ({exc.msg})",
                    path=path,
                )
            ]
    return call.raw_arguments, []


def _argument_path(call: CallResult) -> str:
    if call.provider == "chat":
        if call.path.endswith(".function_call"):
            return f"{call.path}.arguments"
        return f"{call.path}.function.arguments"
    return f"{call.path}.arguments"


def _name_path(call: CallResult) -> str:
    if call.provider == "chat" and not call.path.endswith(".function_call"):
        return f"{call.path}.function.name"
    return f"{call.path}.name"


def _call_id_path(call: CallResult) -> str:
    return f"{call.path}.call_id" if call.provider == "responses" else f"{call.path}.id"


def _safe_schema_message(error: Any) -> str:
    validator = error.validator
    if validator == "required":
        missing = error.validator_value
        if isinstance(missing, list):
            present = error.instance if isinstance(error.instance, Mapping) else {}
            missing_names = [name for name in missing if name not in present]
            if missing_names:
                return "required property is missing: " + ", ".join(map(str, missing_names))
        return "required property is missing"
    if validator == "additionalProperties":
        extras = error.message.partition("'")[2].split("'", 1)[0]
        return (
            f"additional property is not allowed: {extras}"
            if extras
            else "additional property is not allowed"
        )
    if validator == "type":
        expected = error.validator_value
        return f"value must have JSON type {expected!r}"
    if validator == "enum":
        return "value is not one of the allowed enum values"
    if validator == "const":
        return "value does not match the required constant"
    if validator in {"minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems"}:
        return f"value violates the schema {validator} constraint"
    return f"value violates the schema {validator!r} constraint"


def _with_location(
    finding: Finding,
    *,
    record_index: int,
    call_index: int | None = None,
) -> Finding:
    return Finding(
        finding.code,
        finding.severity,
        finding.message,
        finding.path,
        record=record_index,
        call=call_index,
    )


def _join_path(base: str, path: Iterable[Any]) -> str:
    result = base
    for part in path:
        result += f"[{part}]" if isinstance(part, int) else f"[{_json_key(str(part))}]"
    return result


def _json_key(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _string_or_none(value: JSON) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = [
    "AuditReport",
    "CallResult",
    "Finding",
    "RecordResult",
    "audit_record",
    "audit_records",
    "load_tool_schemas",
]
