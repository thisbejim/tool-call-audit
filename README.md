# tool-call-audit

Offline CI audits for captured LLM tool calls against JSON Schema.

When an agent model emits a function call, there are two contracts to check:
the provider envelope and the tool's argument schema. A response can be valid
JSON and still name an undeclared tool, omit a required property, use the wrong
type, duplicate a call id, or arrive without the `call_id` needed to continue a
Responses conversation. `tool-call-audit` makes those failures visible from a
saved response, before an executor sees the arguments.

It is a small local CLI/library: no model, API key, server, account, telemetry,
or network access is needed.

## Quick start

```console
git clone https://github.com/thisbejim/tool-call-audit.git
cd tool-call-audit
python3 -m venv .venv
.venv/bin/python -m pip install .
```

Audit a checked-in fixture:

```console
$ .venv/bin/tool-call-audit audit examples/valid-chat.json
PASS examples/valid-chat.json
record 1: provider=chat-completions calls=1
findings: none
summary: records=1 calls=1 errors=0 warnings=0
```

The same command gives actionable, stable diagnostics for a broken capture:

```console
$ .venv/bin/tool-call-audit audit examples/broken-responses.json
FAIL examples/broken-responses.json
record 1: provider=responses calls=1
findings:
  ERROR   CALL_ID_MISSING record 1 $.output[0] (call 1) — Responses function calls must include call_id
  ERROR   ARGUMENTS_SCHEMA_MISMATCH record 1 $.output[0].arguments["city"] (call 1) — value must have JSON type 'string'
summary: records=1 calls=1 errors=2 warnings=0
```

## The useful workflow

```text
captured provider response + tool schemas
                 │
                 ▼
        tool-call-audit audit
                 │
                 ▼
stable text/JSON findings → CI, fixtures, or a safe executor boundary
```

Capture responses in whatever way your application already uses, save them as
JSON or JSONL, and audit them without replaying the request. A tool-call
argument is parsed as data only; it is never imported, evaluated, or run.

## Inputs

The shortest fixture is one manifest containing `tools` and `response`:

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "parameters": {
          "type": "object",
          "properties": {"city": {"type": "string"}},
          "required": ["city"],
          "additionalProperties": false
        }
      }
    }
  ],
  "response": {
    "choices": [{
      "message": {
        "tool_calls": [{
          "id": "call_1",
          "type": "function",
          "function": {
            "name": "get_weather",
            "arguments": "{\"city\":\"Melbourne\"}"
          }
        }]
      }
    }]
  }
}
```

Keep schemas in a separate file when captures are produced by a logger:

```console
.venv/bin/tool-call-audit audit response.json --tools examples/tools.json
cat captures.jsonl | .venv/bin/tool-call-audit audit - \
  --input-format jsonl --tools examples/tools.json --format json --pretty
```

For JSONL, every non-empty line is one independent capture record. A JSON
manifest may also contain `records` or `captures` arrays. Use `--format json`
for a versioned machine-readable report and `--strict` when warnings should
fail CI.

Exit codes are script-friendly:

* `0` — no errors (warnings are reported but do not fail by default);
* `1` — an audit error, or a warning with `--strict`;
* `2` — invalid arguments, unreadable input, or malformed JSON.

## What it checks

### Chat Completions responses

* `choices[].message.tool_calls[]` function names and JSON-encoded arguments;
* legacy `message.function_call` captures;
* unknown tools, missing names, malformed argument JSON, and duplicate call ids;
* argument instances against the declared JSON Schema.

### Responses API captures

* `output[]` items whose `type` is `function_call`;
* required `name`, `arguments`, and `call_id` fields;
* malformed arguments, duplicate call ids, unknown tools, and schema failures.

### Generic captures

The same checks work with a small provider-neutral envelope containing a
top-level `tool_calls` or `calls` array. Tool definitions may be Chat
Completions (`function.parameters`), Responses (`parameters`), MCP
(`inputSchema`), bare `{name, parameters}` definitions, or a name-to-schema
mapping.

Schemas are checked with the standard `jsonschema` Draft 2020-12 validator,
including standard format checks. The report uses schema version `1`; finding
codes and paths are intended to remain stable for CI annotations.

## Why this exists

Provider-side structured output is useful, but it is not a substitute for a
local audit boundary. OpenAI recommends validating function arguments when
strict structured outputs are unavailable, and local/open-weight servers can
expose different parser behavior. Public issue reports show the recurring
failure modes:

* [LangChain #36700](https://github.com/langchain-ai/langchain/issues/36700)
  requests middleware that validates every model-emitted tool call against its
  tool schema before execution.
* [OpenAI Agents SDK #2449](https://github.com/openai/openai-agents-python/issues/2449)
  documents missing required parameters surfacing at the tool boundary.
* [llama.cpp #22072](https://github.com/ggml-org/llama.cpp/issues/22072)
  tracks malformed or incomplete JSON arguments from an OpenAI-compatible
  local server.
* The [OpenAI function-calling guidance](https://help.openai.com/en/articles/8555517-function-calling-in-the-openai-api)
  recommends a validation library when generated arguments are not guaranteed
  to match a schema.

The strongest existing pieces solve adjacent problems: `jsonschema` validates
an instance but knows nothing about provider envelopes or call ids; SDKs and
agent frameworks validate while a live application is running and usually
return framework-specific errors; endpoint conformance suites exercise a
server but require a running endpoint. `tool-call-audit` is the missing small
boundary between a captured response and execution: provider-aware extraction,
schema validation, safe diagnostics, and a fixture-first CI report in one
dependency-light command.

It complements request-side contract checks and stream parsers; it does not
claim to certify an endpoint or decide whether a semantically valid call is a
good decision.

## Library API

```python
from tool_call_audit import audit_records

report = audit_records(
    records=[{
        "response": {
            "tool_calls": [
                {"name": "get_weather", "arguments": '{"city":"Melbourne"}'}
            ]
        }
    }],
    tools={
        "get_weather": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        }
    },
)

if not report.valid:
    for finding in report.findings:
        print(finding.code, finding.path, finding.message)
```

The returned `AuditReport.as_dict()` is the same schema used by
`--format json`.

## Offline and privacy boundary

* Core auditing works with checked-in fixtures, stdin, or local files.
* The CLI makes no network requests and does not call a model or an endpoint.
* There is no account, telemetry, hosted database, dashboard, or mandatory
  proprietary integration.
* Prompt content and raw argument values are not copied into findings. The
  validator reports paths, types, schema keywords, and remediation-oriented
  messages.
* Tool arguments are treated as untrusted JSON. No command, URL, import, file,
  archive, or tool call is executed.

The optional provider integration is intentionally just your existing capture
pipeline: save a response locally and pass it to the CLI. OpenAI-compatible
servers, vLLM, Ollama, and other providers can be audited when their captured
payloads match one of the documented envelopes; no provider SDK is required.

## Compatibility and non-goals

This project audits a documented subset of response shapes; it does not claim
full compatibility with OpenAI, Anthropic, Meta, xAI, vLLM, Ollama, MCP, or any
other provider. Unknown envelope fields are ignored unless they prevent the
documented call shape from being checked.

It intentionally does not:

* send requests, replay traffic, run tools, or repair model output;
* evaluate whether a chosen tool was semantically appropriate;
* emulate provider-specific streaming state machines;
* validate an entire conversation's request-side message ordering;
* replace a runtime authorization policy or sandbox.

## Development

```console
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/python -m compileall -q src tests
```

The tests use only synthetic local fixtures. No API key, paid model, running
server, or network access is required.

## License

MIT. See [LICENSE](LICENSE).
