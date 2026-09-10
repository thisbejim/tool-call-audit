# Product specification: tool-call-audit

## Target developer

An AI platform, agent, evaluation, or inference engineer who records model
responses and needs a deterministic gate before tool arguments reach an
executor. The target works with OpenAI-style APIs, OpenAI-compatible local
servers, or recorded fixtures and does not want to restructure the application
around a framework.

## Problem and job statement

When a model or compatible inference server emits a tool call, the engineer
needs to know whether the response is executable under the declared tool
contract, so malformed JSON, unknown tools, missing call ids, and schema
violations fail in a reproducible place instead of inside an agent loop or
side-effecting executor.

## Evidence

Independent public signals include:

* LangChain issue [#36700](https://github.com/langchain-ai/langchain/issues/36700),
  requesting middleware that validates every model-emitted tool call before
  execution.
* OpenAI Agents issue [#2449](https://github.com/openai/openai-agents-python/issues/2449),
  showing required tool parameters failing at the runtime boundary.
* llama.cpp issue [#22072](https://github.com/ggml-org/llama.cpp/issues/22072),
  showing malformed/incomplete tool arguments from an OpenAI-compatible server.
* vLLM's [tool-calling documentation](https://github.com/vllm-project/vllm/blob/main/docs/features/tool_calling.md)
  distinguishes schema-constrained decoding from the caller's responsibility
  to define tools and handle calls.
* OpenAI's [function-calling guidance](https://help.openai.com/en/articles/8555517-function-calling-in-the-openai-api)
  explicitly recommends validation when strict structured outputs are not in
  use.

These are not claims about any private system. They are public, recurring
reports across framework, SDK, and local inference ecosystems.

## Candidates considered

The selection was made after comparing several evidence-backed jobs rather
than taking the first plausible idea:

* **Evaluation-run diff/regression CLI:** clearly valuable, but projects such
  as [EvalShift](https://github.com/babaliauskas/evalshift-cli),
  [PromptDiff](https://github.com/he-yufeng/PromptDiff), and mature evaluation
  frameworks already cover the central workflow. A new general evaluator would
  need model calls, scoring policy, and a much larger compatibility surface.
* **Prompt/trace secret redaction:** the [Codex DLP request](https://github.com/openai/codex/issues/25585)
  is strong evidence, but [promptcloak](https://github.com/ezequiel0822-netizen/promptcloak),
  [flare-redact](https://github.com/flare-collection/flare-redact), and existing
  secret scanners make a broad redaction product crowded and difficult to keep
  both safe and low-noise.
* **Conversation JSONL deduplication/inspection:** recurring dataset pain is
  real, but [caret](https://github.com/rouapps/caret) already combines local
  JSONL inspection, deduplication, and token analysis. Rebuilding that surface
  would not offer a clear improvement in a one-day standalone project.
* **Response-side tool-call auditing:** public issues show the gap across
  frameworks and local inference servers, while the focused offline interface
  can be implemented and tested without a model, credentials, or hosted state.

The last candidate has the clearest specific reason to exist: it gives a
provider-aware, safe capture boundary that a bare schema library and a live
framework middleware each leave to application glue.

## Existing workflow and alternatives

1. **Framework middleware (LangChain, Agents SDK, Pydantic AI):** useful in a
   live runtime, but tied to a framework and hard to apply to a saved response
   from another provider or a regression fixture.
2. **`jsonschema`/Pydantic directly:** strong instance validation, but the
   caller must hand-write provider extraction, call-id checks, stable paths,
   and CI serialization for every capture format.
3. **Endpoint conformance or smoke-test suites:** useful against a running
   server, but they require a live endpoint and answer “does this server
   respond?” rather than “what exactly was wrong with this captured call?”
4. **Ad hoc logger scripts:** common and easy to start, but usually echo raw
   values, miss Responses-vs-Chat shape differences, and are not shared between
   projects.

## Product thesis

For engineers debugging or gating agent tool calls, `tool-call-audit` audits a
captured response better than framework middleware or a bare schema validator
because it is provider-aware, offline, fixture-first, and produces stable
diagnostics without executing or echoing model output.

## Core workflow

```text
captured response + declared tool schemas
  -> provider-aware extraction
  -> call id / JSON / JSON Schema checks
  -> text or versioned JSON report
  -> CI gate or safe executor boundary
```

## Non-goals

No live API client, endpoint certification, prompt-quality judge, semantic
correctness checker, stream parser, tool executor, auto-repair, or authorization
policy engine.

## Interface and independence

The primary interface is a CLI accepting JSON/JSONL files or stdin. The Python
library exposes the same deterministic engine. Fixtures demonstrate both
OpenAI-style response envelopes without an API key or running server. Optional
connected value comes from the user's own capture pipeline, not a service
controlled by this project.

## Opportunity scoring (pre-build)

| Criterion | Score | Reason |
| --- | ---: | --- |
| Developer pain | 8 | Failures can poison an agent loop or reach side effects. |
| Frequency | 8 | Every tool-calling integration must parse and validate calls. |
| Evidence of demand | 8 | Independent LangChain, OpenAI Agents, and llama.cpp issues. |
| Frontier-AI relevance | 9 | Applies to frontier API and open-weight inference stacks. |
| Improvement over alternatives | 8 | Adds provider-aware offline capture auditing to bare schema checks. |
| Standalone usefulness | 9 | Fixtures and local files provide value without a model. |
| Local-first advantage | 9 | Sensitive prompts and traces never need to leave the machine. |
| Search discoverability | 8 | Maps to “tool call validator”, “function calling schema check”, and “LLM tool-call CI”. |
| Technical feasibility | 9 | Deterministic parsing plus a mature JSON Schema validator. |
| Testability | 9 | Synthetic fixtures cover provider envelopes and failure modes. |
| Maintainability | 8 | Narrow documented subset; no provider client or live compatibility matrix. |

## Skeptical review

The project could be ignored by teams that already validate arguments in a
single framework, and a bare `jsonschema` call is enough for simple payloads.
Provider schemas also change. The counterargument is the deliberately narrow
boundary: saved responses from different stacks can be checked with the same
command, the report is safe for CI, and the tool does not require adopting a
runtime or sending proprietary traces to a SaaS dashboard. The compatibility
document and fixtures keep claims narrower than “fully compatible.”
