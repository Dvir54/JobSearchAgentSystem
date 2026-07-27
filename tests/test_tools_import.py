def test_tools_module_exposes_server():
    import tools
    assert hasattr(tools, "resume_tools")
