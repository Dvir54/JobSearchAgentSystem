def test_tools_module_exposes_server():
    import tools
    assert hasattr(tools, "resume_tools")


def test_filter_jobs_tool_is_gone():
    # Filtering now happens in the PostToolUse hook, before the model sees the
    # payload. Re-adding this tool would reintroduce the round-trip that made
    # the agent re-emit the entire scrape as tool arguments.
    import tools
    assert not hasattr(tools, "filter_jobs")
    assert not hasattr(tools, "_filter_jobs_impl")


def test_agent_registers_the_reduction_hook(monkeypatch):
    # build_options() reads os.environ['MONID_API_KEY'] directly; without a
    # .env in the checkout this raises KeyError unless we supply a dummy.
    monkeypatch.setenv("MONID_API_KEY", "test-key")
    import agent
    opts = agent.build_options()
    matchers = opts.hooks["PostToolUse"]
    assert any(m.matcher == "mcp__monid__monid_get_run" for m in matchers)
    assert "mcp__resume_tools__filter_jobs" not in opts.allowed_tools
    # the raw payload still crosses the CLI<->Python pipe to reach the hook
    assert opts.max_buffer_size == 10 * 1024 * 1024


def test_agent_restricts_built_in_tools_and_raises_mcp_output_cap(monkeypatch):
    # allowed_tools only PRE-APPROVES; it does not restrict built-in tools. Without
    # disallowed_tools the agent keeps Bash/Grep/Agent/etc and can route around a
    # failed reduction by grepping the offloaded payload by hand ($7.19 in one run).
    # Separately, the CLI truncates oversized MCP results to a file BEFORE
    # PostToolUse hooks run, so MAX_MCP_OUTPUT_TOKENS must be raised via env for
    # the hook to ever see real JSON instead of a stub.
    monkeypatch.setenv("MONID_API_KEY", "test-key")
    import config
    import agent
    opts = agent.build_options()
    assert "Bash" in opts.disallowed_tools
    assert "Agent" in opts.disallowed_tools
    assert opts.env["MAX_MCP_OUTPUT_TOKENS"] == config.MAX_MCP_OUTPUT_TOKENS
