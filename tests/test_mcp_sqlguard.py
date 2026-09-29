import pytest

from engine.mcp.sqlguard import SqlRejected, validate_sql


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select count(*) from documents",
    "  -- leading comment\n  SELECT id FROM documents LIMIT 5",
    "/* block */ SELECT 1",
    "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
    "EXPLAIN SELECT 1",
    "EXPLAIN (ANALYZE, BUFFERS) SELECT count(*) FROM sources",
    "SHOW server_version",
    "TABLE sources",
    "VALUES (1), (2)",
    "SELECT 1;",                                  # single trailing semicolon ok
    "SELECT 'a;b' AS s",                          # semicolon inside string literal ok
    "SELECT * FROM documents WHERE summary LIKE '%delete%'",   # keyword inside literal ok
    "SELECT into_col FROM t",                     # 'into' as part of an identifier ok
])
def test_accepts(sql):
    assert validate_sql(sql)


@pytest.mark.parametrize("sql,needle", [
    ("", "tóm"),
    ("   ", "tóm"),
    ("DELETE FROM documents", "SELECT"),
    ("UPDATE documents SET summary = ''", "SELECT"),
    ("INSERT INTO sources VALUES (1)", "SELECT"),
    ("DROP TABLE documents", "SELECT"),
    ("TRUNCATE passages", "SELECT"),
    ("SELECT 1; DELETE FROM documents", "ein setning"),
    ("SELECT 1; SELECT 2", "ein setning"),
    ("WITH d AS (DELETE FROM documents RETURNING id) SELECT * FROM d", "breyt"),
    ("WITH u AS (UPDATE sources SET x = 1 RETURNING id) SELECT 1", "breyt"),
    ("SELECT * INTO new_table FROM documents", "INTO"),
    ("SELECT pg_sleep(30)", "pg_sleep"),
    ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
    ("SELECT lo_import('/etc/passwd')", "lo_import"),
    ("COPY documents TO '/tmp/x'", "SELECT"),
    ("SELECT * FROM dblink('x', 'y')", "dblink"),
    ("EXPLAIN ANALYZE DELETE FROM documents", "breyt"),
])
def test_rejects(sql, needle):
    with pytest.raises(SqlRejected) as ei:
        validate_sql(sql)
    assert needle.lower() in str(ei.value).lower()


def test_returns_sql_without_trailing_semicolon():
    assert validate_sql("SELECT 1;") == "SELECT 1"
    assert validate_sql("  SELECT 1  ") == "SELECT 1"
