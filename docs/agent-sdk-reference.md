# `claude-agent-sdk` (Python) reference — verified API

**Verified against:** `claude-agent-sdk==0.2.128`, Python 3.11, Windows.
**How this was verified:** installed the package into `.venv`, inspected the real
public API with `dir()`/`inspect.signature()`/`inspect.getsource()` (not docs, not
memory), then ran a real one-shot session (`spike_agent.py`, deleted after use)
that called an in-process tool and a remote MCP tool over HTTP. Every snippet
below is copy-pasted from what actually ran — nothing here is guessed.

Tasks 4–6 of the Agent SDK refactor should copy patterns from this file rather
than re-deriving the API.

---

## 0. Environment prerequisite (important gotcha)

`claude-agent-sdk` is a **thin Python wrapper that spawns the Claude Code CLI as
a subprocess** (`_internal/transport/subprocess_cli.py`). It is *not* a pure
HTTP client to the Anthropic API.

- Requires the `claude` CLI on `PATH` (installed here via
  `npm install -g @anthropic-ai/claude-code`; found at
  `C:\Users\dvir5\AppData\Roaming\npm\claude`, version `2.1.218`).
- Minimum required CLI version, per SDK source: `2.0.0`.
- If the CLI is missing, the SDK raises `CLINotFoundError` with an install hint
  pointing at that same npm package — so this is a hard runtime dependency for
  any host machine running the pipeline, not just a dev-box nicety.
- Auth: the subprocess inherits the parent process's environment (everything in
  `os.environ` except `CLAUDECODE`, plus SDK-internal vars like
  `CLAUDE_CODE_ENTRYPOINT`). Setting `ANTHROPIC_API_KEY` in `.env` (loaded via
  `python-dotenv`'s `load_dotenv()` *before* importing/using the SDK) is
  sufficient — no separate `claude login` / OAuth step needed. Confirmed
  working in the spike.
- Node.js must be present for the CLI itself (confirmed `node v22.14.0`
  installed alongside).

## 1. Imports

```python
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)
```

Other names that exist but weren't needed for the spike: `ClaudeSDKClient`
(stateful/bidirectional alternative to `query()`), `McpSdkServerConfig`,
`SdkMcpTool`, `PermissionMode`, hook types (`HookMatcher`, `PreToolUseHookInput`,
...), `AgentDefinition` (for subagents), `SandboxSettings`, `ThinkingConfig*`.

## 2. In-process custom tool — `@tool` decorator

```python
@tool("ping", "Trivial local tool that echoes 'pong' plus any message given.", {"message": str})
async def ping(args: dict) -> dict:
    return {
        "content": [
            {"type": "text", "text": f"pong: {args.get('message', '')}"}
        ]
    }
```

Confirmed signature (from `inspect.signature`):

```python
def tool(
    name: str,
    description: str,
    input_schema: type | dict[str, Any],   # dict of {param: type}, a TypedDict, or a raw JSON Schema
    annotations: mcp.types.ToolAnnotations | None = None,
) -> Callable[[Callable[[Any], Awaitable[dict[str, Any]]]], SdkMcpTool[Any]]
```

- The handler **must be `async def`** and takes a single `dict` of args.
- Return shape: `{"content": [{"type": "text", "text": "..."}], "is_error": <bool, optional>}`.
  `is_error: True` signals a tool-level error back to the model.
- The decorator returns an `SdkMcpTool` instance (fields: `name`, `description`,
  `input_schema`, `handler`, `annotations`) — pass a list of these into
  `create_sdk_mcp_server`, not into `mcp_servers` directly.

## 3. Bundle tools into an SDK MCP server — `create_sdk_mcp_server`

```python
local_server = create_sdk_mcp_server(name="local", version="1.0.0", tools=[ping])
```

Confirmed signature:

```python
def create_sdk_mcp_server(
    name: str,
    version: str = "1.0.0",
    tools: list[SdkMcpTool[Any]] | None = None,
) -> McpSdkServerConfig
```

This runs the tool **in-process** (no subprocess, no IPC) — it returns a config
object you put directly into `ClaudeAgentOptions.mcp_servers[<key>]`.

## 4. Remote MCP server over HTTP with a bearer auth header (Monid)

`ClaudeAgentOptions.mcp_servers` accepts a dict whose values can be any of:
`McpStdioServerConfig`, `McpSSEServerConfig`, `McpHttpServerConfig`, or
`McpSdkServerConfig` (the in-process kind above). These are `TypedDict`s, so
plain dict literals work directly — no wrapper class needed.

`McpHttpServerConfig` shape (confirmed via `typing.get_type_hints`):

```python
{
    "type": "http",          # Literal["http"], required
    "url": str,               # required
    "headers": dict[str, str] # optional (NotRequired)
}
```

Verified working config for Monid:

```python
"monid": {
    "type": "http",
    "url": "https://mcp.monid.ai/v1",
    "headers": {"Authorization": f"Bearer {MONID_API_KEY}"},
},
```

No OAuth flow, no session negotiation — matches the known fact that Monid is
stateless. This ran successfully in the spike and returned a real balance.

(For completeness, `McpSSEServerConfig` has the identical `{type: "sse", url,
headers}` shape for SSE-based remote servers; `McpStdioServerConfig` is
`{command, args?, env?}` for spawning a local MCP server process — neither was
needed here.)

## 5. `ClaudeAgentOptions` — fields used in the spike

Full dataclass has ~40 fields (hooks, sandboxing, thinking config, session
resume/fork, subagents, etc. — see `claude_agent_sdk.types.ClaudeAgentOptions`
for the complete list if a later task needs them). The ones exercised here:

```python
options = ClaudeAgentOptions(
    system_prompt=(
        "You are a spike-test agent. Call the requested tools exactly as "
        "instructed, then summarize their results. Do nothing else."
    ),
    mcp_servers={
        "local": local_server,          # McpSdkServerConfig (in-process)
        "monid": {                       # McpHttpServerConfig (remote HTTP)
            "type": "http",
            "url": "https://mcp.monid.ai/v1",
            "headers": {"Authorization": f"Bearer {MONID_API_KEY}"},
        },
    },
    allowed_tools=["mcp__local__ping", "mcp__monid__monid_balance"],
    permission_mode="bypassPermissions",
    max_turns=6,
)
```

Notes:
- **Tool name namespacing**: tools exposed via `mcp_servers` are addressed as
  `mcp__<server_key>__<tool_name>` in `allowed_tools` (and in the model's tool
  calls) — e.g. server key `"local"` + tool `"ping"` → `mcp__local__ping`.
- `permission_mode` is `Literal['default', 'acceptEdits', 'plan',
  'bypassPermissions', 'dontAsk', 'auto']`. The spike used `"bypassPermissions"`
  to allow tool calls without interactive confirmation (there is no human to
  prompt in a headless run). `'dontAsk'` (deny anything not pre-approved by
  `allowed_tools`) is likely the safer choice for the production pipeline —
  worth a deliberate decision in the task that builds the real session, not an
  assumption carried from this spike.
- `max_turns` caps agent turns (used `6`; the real run needed 5 assistant turns
  including tool-search retries — see gotcha below).
- `system_prompt` takes a plain `str` (there's also a `SystemPromptPreset` /
  `SystemPromptFile` option, unused here).

## 6. Running a one-shot session — `query()`

```python
async for message in query(prompt=prompt, options=options):
    if isinstance(message, AssistantMessage):
        for block in message.content:
            if isinstance(block, ToolUseBlock):
                print(block.name, block.input)
            elif isinstance(block, ToolResultBlock):
                print(block.tool_use_id, block.content)
            elif isinstance(block, TextBlock):
                print(block.text)
    elif isinstance(message, ResultMessage):
        print(message.subtype, message.is_error, message.result, message.total_cost_usd)
```

Confirmed signature:

```python
def query(
    *,
    prompt: str | AsyncIterable[dict[str, Any]],
    options: ClaudeAgentOptions | None = None,
    transport: Transport | None = None,
) -> AsyncIterator[UserMessage | AssistantMessage | SystemMessage | ResultMessage | StreamEvent | RateLimitEvent]
```

- `query()` is stateless/fire-and-forget — right tool for a one-shot pipeline
  run. `ClaudeSDKClient` exists for stateful/interactive multi-turn use
  (`connect()`, `.query()`, `.receive_response()`, `.disconnect()`) — not
  needed for a single pipeline invocation but available if a later task needs
  follow-up turns.
- Message stream includes `AssistantMessage` (has `.content: list[TextBlock |
  ThinkingBlock | ToolUseBlock | ToolResultBlock | ...]`), and a terminal
  `ResultMessage` with `.subtype` (`"success"` on completion), `.is_error`,
  `.result` (the final text), `.total_cost_usd`, `.num_turns`, `.usage`.
- `ToolUseBlock` fields: `id`, `name`, `input` (dict). `ToolResultBlock`
  fields: `tool_use_id`, `content`, `is_error`.

## 7. Spike run result (real, single run — not simulated)

Command: `.venv/Scripts/python.exe spike_agent.py`

Observed tool-call sequence:
```
ToolSearch (query='select:mcp__local__ping', ...)
ToolSearch (query='+monid balance', ...)
mcp__local__ping (message='hello-from-spike')  →  "pong: hello-from-spike"
ToolSearch (query='+monid balance credits account', ...)
mcp__monid__monid_balance (workspaceId='')      →  balance $0.67 USD, $0.00 held
```

Final `ResultMessage`: `subtype="success"`, `is_error=False`,
`total_cost_usd=0.23168925`.

**Both target tool calls succeeded**: the local in-process tool returned its
echo, and the remote Monid MCP tool returned a real balance ($0.67).

### Gotcha: MCP tools can be deferred behind the CLI's own `ToolSearch`

Even though both tools were explicitly listed in `allowed_tools`, the CLI did
**not** hand their full schemas to the model up front — it exposed a
`ToolSearch` meta-tool and made the model search for/load
`mcp__local__ping` and `mcp__monid__monid_balance` before it could call them
(this happened automatically inside the CLI subprocess; the spike script did
not request it). This appears to be the CLI's deferred-tool-loading mechanism,
presumably to keep context small when MCP servers (like Monid, which exposes
many tools) are attached. **Practical implication for later tasks**: budget
extra turns in `max_turns` for tool-discovery overhead, and don't assume a
tool call happens on turn 1 just because it's in `allowed_tools`. This added
one extra `ToolSearch` retry in the spike (Monid's server was still connecting
on the first attempt).

### Cost note

The single spike run cost **$0.23** (`total_cost_usd`), higher than a trivial
two-tool-call session might suggest — likely driven by the `ToolSearch`
retries and CLI/session overhead, not by Monid (whose `monid_balance` call
itself is free). Budget accordingly for any later task that runs multiple
sessions.

### Environment note (cosmetic, non-blocking)

The CLI printed a startup warning:
```
claude.ai connectors are disabled because ANTHROPIC_API_KEY or another auth
source is set and takes precedence over your claude.ai login
```
This is expected/harmless when driving the SDK via `ANTHROPIC_API_KEY` — it
does not affect MCP server connectivity or tool execution (both Monid and the
local server worked). No action needed.

## 8. What was *not* exercised (out of scope for this spike)

- `ClaudeSDKClient` (stateful multi-turn) — only `query()` was verified.
- Hooks, subagents (`AgentDefinition`), sandboxing (`SandboxSettings`),
  thinking config, session resume/fork — all present in `ClaudeAgentOptions`
  per the installed version but unused/unverified here. Confirm against this
  same installed version (`0.2.128`) if a later task needs them; do not assume
  their shape from this doc.
