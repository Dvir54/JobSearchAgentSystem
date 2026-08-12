import db


def test_apply_schema_creates_the_three_tables(pg):
    with pg.cursor() as cur:
        cur.execute("""SELECT table_name FROM information_schema.tables
                       WHERE table_schema = 'public'""")
        tables = {row[0] for row in cur.fetchall()}
    assert {"runs", "seen", "matches"} <= tables


def test_apply_schema_is_idempotent(pg):
    db.apply_schema(pg)          # the conftest fixture already applied it once
    db.apply_schema(pg)          # a third time must not raise


def test_session_is_reused(monkeypatch):
    import config
    monkeypatch.setattr(config, "DATABASE_URL", config.TEST_DATABASE_URL)
    db.close_session()
    first = db.session()
    assert db.session() is first
    db.close_session()
