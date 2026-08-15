from jobsearch import config


def test_tools_module_exposes_server():
    from jobsearch.agent import tools
    assert hasattr(tools, "resume_tools")


def test_filter_jobs_tool_is_gone():
    # Filtering now happens in the PostToolUse hook, before the model sees the
    # payload. Re-adding this tool would reintroduce the round-trip that made
    # the agent re-emit the entire scrape as tool arguments.
    from jobsearch.agent import tools
    assert not hasattr(tools, "filter_jobs")
    assert not hasattr(tools, "_filter_jobs_impl")


def test_agent_registers_the_reduction_hook(monkeypatch):
    # build_options() reads os.environ['MONID_API_KEY'] directly; without a
    # .env in the checkout this raises KeyError unless we supply a dummy.
    monkeypatch.setenv("MONID_API_KEY", "test-key")
    from jobsearch.agent import session as agent
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
    from jobsearch import config
    from jobsearch.agent import session as agent
    opts = agent.build_options()
    assert "Bash" in opts.disallowed_tools
    assert "Agent" in opts.disallowed_tools
    assert opts.env["MAX_MCP_OUTPUT_TOKENS"] == config.MAX_MCP_OUTPUT_TOKENS


def test_prepare_resume_tool_replaced_write_resume(monkeypatch):
    monkeypatch.setenv("MONID_API_KEY", "dummy")
    from jobsearch.agent import tools
    assert hasattr(tools, "prepare_resume")
    assert not hasattr(tools, "write_resume")


def test_record_verdict_tool_replaced_write_index():
    # The run's record is now rows in `seen` and `matches`, not an index.md.
    # record_verdict is also what feeds cross-run dedup, so it is not optional.
    from jobsearch.agent import tools
    assert hasattr(tools, "record_verdict")
    assert not hasattr(tools, "write_index")


def test_agent_registers_canva_mcp_and_the_write_guard(monkeypatch):
    monkeypatch.setenv("MONID_API_KEY", "dummy")
    from jobsearch.agent import session as agent
    opts = agent.build_options()

    assert "canva" in opts.mcp_servers
    assert any(t.startswith("mcp__canva__") for t in opts.allowed_tools)
    assert "mcp__resume_tools__prepare_resume" in opts.allowed_tools

    assert "mcp__resume_tools__save_pdf" in opts.allowed_tools
    assert "mcp__resume_tools__record_verdict" in opts.allowed_tools
    assert "mcp__resume_tools__write_index" not in opts.allowed_tools

    pre = opts.hooks["PreToolUse"]
    assert any(m.matcher == "mcp__canva__perform-editing-operations" for m in pre)

    post = opts.hooks["PostToolUse"]
    matchers = [m.matcher for m in post]
    # the Monid reduction hook must survive
    assert "mcp__monid__monid_get_run" in matchers
    # and the Canva element dumps must be reduced too
    assert "mcp__canva__start-editing-transaction" in matchers
    assert "mcp__canva__perform-editing-operations" in matchers

    assert opts.max_buffer_size == 10 * 1024 * 1024
    assert opts.env["MAX_MCP_OUTPUT_TOKENS"] == config.MAX_MCP_OUTPUT_TOKENS


def test_agent_still_denies_the_builtin_tools(monkeypatch):
    monkeypatch.setenv("MONID_API_KEY", "dummy")
    from jobsearch.agent import session as agent
    opts = agent.build_options()
    # save_pdf/record_verdict exist precisely because these stay denied
    for denied in ("Bash", "Read", "Write", "WebFetch", "Agent"):
        assert denied in opts.disallowed_tools


def test_the_cli_stderr_is_captured_into_our_log(monkeypatch, capsys):
    """2026-08-15: the first task-launched run ever to reach the agent failed
    with a bare "Control request timeout: initialize" and nothing else. The CLI
    almost certainly explained itself on stderr, into a console that closed with
    the process."""
    monkeypatch.setenv("MONID_API_KEY", "dummy")
    from jobsearch.agent import session
    opts = session.build_options()
    assert callable(opts.stderr)
    opts.stderr("EACCES: permission denied")
    assert "EACCES" in capsys.readouterr().err


def test_debug_stderr_follows_the_live_stderr(monkeypatch, capsys):
    """ClaudeAgentOptions.debug_stderr defaults to the sys.stderr object
    captured when the dataclass was DEFINED — i.e. at import, before cli.py
    installs the run log's tee. SDK debug output therefore bypassed the log
    completely. This proxy resolves sys.stderr at write time instead."""
    monkeypatch.setenv("MONID_API_KEY", "dummy")
    from jobsearch.agent import session
    opts = session.build_options()
    opts.debug_stderr.write("late-bound line\n")
    assert "late-bound" in capsys.readouterr().err


def test_the_initialize_timeout_is_generous(monkeypatch):
    """The SDK's default is 60s. A scheduled task runs at below-normal priority
    and starts the CLI cold, with MCP servers to connect; 60s was not enough and
    the run died before the agent existed."""
    monkeypatch.setenv("MONID_API_KEY", "dummy")
    from jobsearch.agent import session
    assert session.build_options().load_timeout_ms >= 120_000


def test_the_pinned_search_recipe_carries_the_body_envelope():
    """The endpoint takes the search fields inside `body`. The prompt used to hand
    over the bare body as `input`, so monid_run rejected the first call of every
    run and the agent improvised the wrapper. tooling._window() reads
    run["input"]["body"], which is the same fact from the other end."""
    from jobsearch.agent import session as agent
    from jobsearch.agent import tooling
    assert agent._SEARCH_RECIPE == {"body": agent._SEARCH_RECIPE_BODY}
    assert "'body'" in agent.WORKFLOW
    # The two ends must agree: what we send is what the reducer reads back.
    echoed = {"input": agent._SEARCH_RECIPE}
    assert tooling._window(echoed) == agent._SEARCH_RECIPE_BODY["postedLimit"]
