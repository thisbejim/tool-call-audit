"""Command-line interface for :mod:`tool_call_audit`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .audit import AuditReport, audit_records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tool-call-audit",
        description="Audit captured LLM tool calls against local JSON Schemas.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit_parser = subparsers.add_parser(
        "audit",
        help="audit a JSON or JSONL capture",
        description=(
            "Read a captured Chat Completions, Responses, or generic tool-call "
            "payload and report schema/lifecycle findings."
        ),
    )
    audit_parser.add_argument("input", help="capture JSON/JSONL path, or - for stdin")
    audit_parser.add_argument(
        "--tools",
        metavar="PATH",
        help="JSON file containing tool definitions (the capture may contain them instead)",
    )
    audit_parser.add_argument(
        "--input-format",
        choices=("auto", "json", "jsonl"),
        default="auto",
        help="input encoding (default: auto)",
    )
    audit_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        dest="output_format",
        help="report format (default: text)",
    )
    audit_parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    audit_parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as failures (exit status 1)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "audit":
        try:
            report = _run_audit(args)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"tool-call-audit: {exc}", file=sys.stderr)
            return 2
        if args.output_format == "json":
            payload = report.as_dict()
            if args.strict and report.warnings:
                payload["status"] = "fail"
            print(json.dumps(payload, indent=2 if args.pretty else None, ensure_ascii=False))
        else:
            _print_text(report, strict=args.strict)
        return 1 if report.errors or (args.strict and report.warnings) else 0
    parser.error(f"unknown command: {args.command}")
    return 2


def _run_audit(args: argparse.Namespace) -> AuditReport:
    raw_text = _read_text(args.input)
    input_format = args.input_format
    if input_format == "auto":
        input_format = _detect_input_format(raw_text)
    tools = _read_json_file(args.tools) if args.tools else None
    if input_format == "jsonl":
        records = _parse_jsonl(raw_text)
    else:
        records = _records_from_json(json.loads(raw_text))
    return audit_records(records, tools, source=args.input)


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def _read_json_file(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _detect_input_format(raw_text: str) -> str:
    for line in raw_text.splitlines():
        if line.strip():
            try:
                json.loads(raw_text)
            except json.JSONDecodeError:
                return "jsonl"
            return "json"
    return "json"


def _parse_jsonl(raw_text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL line {line_number} must contain an object")
        records.append(value)
    return records


def _records_from_json(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        records = value
    elif isinstance(value, dict) and isinstance(value.get("records"), list):
        inherited_tools = value.get("tools")
        records = [
            _inherit_tools(record, inherited_tools) if isinstance(record, dict) else record
            for record in value["records"]
        ]
    elif isinstance(value, dict) and isinstance(value.get("captures"), list):
        inherited_tools = value.get("tools")
        records = [
            _inherit_tools(record, inherited_tools) if isinstance(record, dict) else record
            for record in value["captures"]
        ]
    else:
        records = [value]
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("JSON input must be an object, or an array of objects")
    return records


def _inherit_tools(record: dict[str, Any], inherited_tools: Any) -> dict[str, Any]:
    if inherited_tools is None or "tools" in record:
        return record
    return dict(record, tools=inherited_tools)


def _print_text(report: AuditReport, *, strict: bool) -> None:
    status = "PASS" if report.errors == 0 and not (strict and report.warnings) else "FAIL"
    source = report.source or "stdin"
    print(f"{status} {source}")
    for record in report.records:
        print(f"record {record.record}: provider={record.provider} calls={record.calls}")
    if report.findings:
        print("findings:")
        for finding in report.findings:
            location = finding.path
            if finding.record is not None:
                location = f"record {finding.record} {location}"
            if finding.call is not None:
                location += f" (call {finding.call})"
            print(f"  {finding.severity.upper():7} {finding.code} {location} — {finding.message}")
    else:
        print("findings: none")
    print(
        "summary: "
        f"records={len(report.records)} calls={report.calls} "
        f"errors={report.errors} warnings={report.warnings}"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
