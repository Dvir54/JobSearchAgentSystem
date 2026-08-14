"""The repo-root anchor, pinned.

config.PROJECT_ROOT is computed by walking up from config.py's own location, so
it breaks the moment config.py changes depth in the tree -- silently, because a
wrong root still produces perfectly valid Path objects. Everything the agent
reads from disk hangs off it: base_cv.md, schema.sql, logs/, output/.

These pass today. That is the point: they are a characterisation test guarding
the R4 package move, not a red-green cycle.
"""
import config


def test_project_root_is_the_real_repo_root():
    assert (config.PROJECT_ROOT / "pyproject.toml").is_file()
    assert (config.PROJECT_ROOT / "docker-compose.yml").is_file()


def test_the_paths_hanging_off_project_root_exist():
    # base_cv.md is gitignored but required at runtime; schema.sql is committed.
    assert config.SCHEMA_PATH.is_file()
    assert config.BASE_CV_PATH.is_file(), (
        "base_cv.md is missing -- either PROJECT_ROOT is wrong or the file was "
        "never created. See the README.")


def test_derived_directories_sit_under_the_repo_root():
    # Not that they exist -- they are created on demand -- but that they are
    # anchored where a human would look for them.
    assert config.LOG_DIR.parent == config.PROJECT_ROOT
