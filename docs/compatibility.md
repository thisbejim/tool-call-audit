# Compatibility

`tool-call-audit` validates response shapes it can identify; it is not a
provider certification suite.

| Shape | Supported fields | Status |
| --- | --- | --- |
| Chat Completions | `choices[].message.tool_calls[].function.name`, `.arguments`, and call `id` | Supported |
| Legacy Chat function call | `choices[].message.function_call.name` and `.arguments` | Supported; no call-id check |
| Responses | `output[].type=function_call`, `name`, `arguments`, `call_id` | Supported |
| Generic | top-level `tool_calls` or `calls` arrays with `name`/`arguments` | Supported |

Tool definitions may be:

* Chat Completions `{type: "function", function: {name, parameters}}`;
* Responses `{type: "function", name, parameters}`;
* MCP `{name, inputSchema}`;
* bare `{name, parameters}`;
* a mapping from names to JSON Schemas.

Arguments may be the provider's JSON-encoded string or an already-decoded JSON
value in a generic capture. The argument instance is checked with
`jsonschema`'s Draft 2020-12 validator and standard format checker.

Unknown fields are preserved in the input but are not interpreted. Unsupported
provider envelopes are not guessed at; they result in zero extracted calls and
a `NO_SUPPORTED_CALLS` warning, so call the project out as unsupported in your
own integration rather than interpreting a clean report as certification.

The project does not validate request-side message ordering, stream framing,
tool execution results, semantic tool choice, authorization, or provider
availability.
