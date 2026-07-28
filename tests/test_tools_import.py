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


def test_agent_registers_the_reduction_hook():
    import agent
    opts = agent.build_options()
    matchers = opts.hooks["PostToolUse"]
    assert any(m.matcher == "mcp__monid__monid_get_run" for m in matchers)
    assert "mcp__resume_tools__filter_jobs" not in opts.allowed_tools
    # the raw payload still crosses the CLI<->Python pipe to reach the hook
    assert opts.max_buffer_size == 10 * 1024 * 1024
