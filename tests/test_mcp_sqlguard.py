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
    "(SELECT 1) UNION ALL (SELECT 2)",            # parenthesised top-level query ok
    "  (SELECT id FROM documents LIMIT 1)",       # leading whitespace + paren ok
    "SELECT $$a;b$$ AS s",                        # semicolon inside dollar-quoted string ok
    "SELECT E'it\\'s' AS s",                      # backslash-escaped quote in E-string ok
    'SELECT "update" FROM t',                     # quoted identifier matching a keyword ok
    'SELECT t."copy" FROM t',                     # quoted identifier matching a keyword ok
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
    ("SELECT $$--$$; DROP TABLE documents", "ein setning"),
    ("SELECT $$it's$$; DROP TABLE documents; SELECT $$'$$", "ein setning"),
    ("SELECT E'\\''; DROP TABLE documents; SELECT '1'", "ein setning"),
    ("SELECT pg_advisory_lock(42)", "pg_advisory_lock"),
])
def test_rejects(sql, needle):
    with pytest.raises(SqlRejected) as ei:
        validate_sql(sql)
    assert needle.lower() in str(ei.value).lower()


def test_returns_sql_without_trailing_semicolon():
    assert validate_sql("SELECT 1;") == "SELECT 1"
    assert validate_sql("  SELECT 1  ") == "SELECT 1"


def test_trailing_comment_after_semicolon_is_stripped():
    result = validate_sql("SELECT 1; -- note")
    assert ";" not in result


def test_literal_at_end_is_preserved():
    # Regression: round-1's masking blanked literal delimiters to spaces too,
    # so a query ending in a literal looked like it ended in trailing
    # whitespace and got silently truncated.
    assert validate_sql("SELECT * FROM t WHERE x = 'foo'") == "SELECT * FROM t WHERE x = 'foo'"
    assert validate_sql("SELECT 'a' || $$b$$") == "SELECT 'a' || $$b$$"
    assert validate_sql("SELECT * FROM t WHERE name = $tag$bar$tag$") == "SELECT * FROM t WHERE name = $tag$bar$tag$"
    assert validate_sql("SELECT E'x\\'y'") == "SELECT E'x\\'y'"


def test_trailing_comment_after_literal_is_stripped():
    assert validate_sql("SELECT 'a' -- trailing comment") == "SELECT 'a'"
    assert validate_sql("SELECT 'a'; /* c */") == "SELECT 'a'"


# (input, expected return value) pairs mirroring every entry in the
# `test_accepts` list above: validate_sql(sql) must equal sql.strip() with at
# most one trailing ';' removed and any trailing comment removed. Spelled out
# by hand (no regex) so this is an independent check on the return value,
# not just on truthiness.
_ACCEPT_RETURN_VALUES = [
    ("SELECT 1", "SELECT 1"),
    ("select count(*) from documents", "select count(*) from documents"),
    (
        "  -- leading comment\n  SELECT id FROM documents LIMIT 5",
        "-- leading comment\n  SELECT id FROM documents LIMIT 5",
    ),
    ("/* block */ SELECT 1", "/* block */ SELECT 1"),
    ("WITH x AS (SELECT 1 AS a) SELECT a FROM x", "WITH x AS (SELECT 1 AS a) SELECT a FROM x"),
    ("EXPLAIN SELECT 1", "EXPLAIN SELECT 1"),
    (
        "EXPLAIN (ANALYZE, BUFFERS) SELECT count(*) FROM sources",
        "EXPLAIN (ANALYZE, BUFFERS) SELECT count(*) FROM sources",
    ),
    ("SHOW server_version", "SHOW server_version"),
    ("TABLE sources", "TABLE sources"),
    ("VALUES (1), (2)", "VALUES (1), (2)"),
    ("SELECT 1;", "SELECT 1"),
    ("SELECT 'a;b' AS s", "SELECT 'a;b' AS s"),
    (
        "SELECT * FROM documents WHERE summary LIKE '%delete%'",
        "SELECT * FROM documents WHERE summary LIKE '%delete%'",
    ),
    ("SELECT into_col FROM t", "SELECT into_col FROM t"),
    ("(SELECT 1) UNION ALL (SELECT 2)", "(SELECT 1) UNION ALL (SELECT 2)"),
    ("  (SELECT id FROM documents LIMIT 1)", "(SELECT id FROM documents LIMIT 1)"),
    ("SELECT $$a;b$$ AS s", "SELECT $$a;b$$ AS s"),
    ("SELECT E'it\\'s' AS s", "SELECT E'it\\'s' AS s"),
    ('SELECT "update" FROM t', 'SELECT "update" FROM t'),
    ('SELECT t."copy" FROM t', 'SELECT t."copy" FROM t'),
]


def test_accept_list_return_values_match_expected():
    for sql, expected in _ACCEPT_RETURN_VALUES:
        assert validate_sql(sql) == expected, sql


@pytest.mark.parametrize("sql", [
    # `""` is an escaped quote inside a quoted identifier under Postgres rules,
    # so the scanner's quote pairing ends up one quote short. Before the fix the
    # blanked tail made validate_sql return a *truncated* query; now it refuses.
    r'SELECT "a""b" FROM (SELECT 1 AS "a\"b") q',
    "SELECT 'abc",
    "SELECT $$abc",
    'SELECT * FROM "t',
    "SELECT $tag$abc",
])
def test_unterminated_literal_or_identifier_is_rejected(sql):
    with pytest.raises(SqlRejected) as ei:
        validate_sql(sql)
    assert "Ólokaður" in str(ei.value)


@pytest.mark.parametrize("sql", [
    'SELECT "ok" FROM t',
    "SELECT 'it''s'",
    "SELECT $$ok$$ AS s",
    "SELECT $tag$ok$tag$ AS s",
])
def test_properly_closed_literals_are_still_accepted(sql):
    assert validate_sql(sql) == sql


@pytest.mark.parametrize("sql", [
    "SELECT pg_logical_emit_message(true, 'a', 'b')",
    "SELECT pg_notify('chan', 'payload')",
])
def test_wal_writing_functions_are_rejected(sql):
    """Both succeed as lausnir_ro inside a READ ONLY transaction and write WAL."""
    with pytest.raises(SqlRejected) as ei:
        validate_sql(sql)
    assert "er ekki leyft" in str(ei.value)


def test_nested_block_comment_cannot_hide_a_second_statement():
    # Postgres nests block comments: `/* /* */ ' */` is ONE comment. A scanner
    # that closes at the first `*/` reads the `'` as the start of a literal and
    # masks `; DELETE …` as string content — the statement Postgres then runs.
    with pytest.raises(SqlRejected) as ei:
        validate_sql("SELECT 1 /* /* */ ' */; DELETE FROM documents; -- '")
    assert "ein setning" in str(ei.value)


def test_nested_block_comment_is_one_comment():
    sql = "/* outer /* inner; DELETE */ still comment; */ SELECT 1"
    assert validate_sql(sql) == sql


def test_unterminated_block_comment_is_rejected():
    # Postgres refuses it ("unterminated /* comment"); returning the text up to
    # the comment would hand back a query the user never wrote.
    with pytest.raises(SqlRejected) as ei:
        validate_sql("SELECT 1 /* /* */ never closed")
    assert "Ólokað" in str(ei.value)


def test_nested_block_comment_cannot_hide_a_forbidden_function():
    # One statement to Postgres — `SELECT 1, pg_notify(...)` — so it does not
    # fail on a multi-statement parse error; before nesting was honoured the
    # guard read the call as string content and let it through.
    with pytest.raises(SqlRejected) as ei:
        validate_sql("SELECT 1 /* /* */ ' */, pg_notify('chan', 'x') -- '")
    assert "pg_notify" in str(ei.value)
