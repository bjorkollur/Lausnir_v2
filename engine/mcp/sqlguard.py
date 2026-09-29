"""SELECT-only guard for the `sql_query` MCP tool.

This is the third of three layers (spec §5): the `lausnir_ro` role cannot
write, every query runs in a READ ONLY transaction, and this validator gives
the LLM an immediate, understandable refusal for obvious mistakes. It is
deliberately conservative — rejecting a legitimate query costs one retry,
letting a write through would cost the corpus.
"""
from __future__ import annotations

import re

_ALLOWED_FIRST = ("SELECT", "WITH", "EXPLAIN", "SHOW", "TABLE", "VALUES")
_MODIFYING = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|VACUUM|REINDEX|CLUSTER|LOCK|CALL|DO)\b", re.I)
_FORBIDDEN_FUNCS = re.compile(r"\b(pg_sleep|pg_sleep_for|pg_sleep_until|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|lo_unlink|dblink|dblink_exec|pg_terminate_backend|pg_cancel_backend|set_config|pg_reload_conf)\b", re.I)
_SELECT_INTO = re.compile(r"\bSELECT\b(?:(?!\bFROM\b).)*?\bINTO\b", re.I | re.S)


class SqlRejected(ValueError):
    """Raised with an Icelandic reason the LLM can act on."""


def _strip_comments_and_literals(sql: str) -> str:
    """Remove -- and /* */ comments and blank out string literals so that
    keywords inside literals ('%delete%') and semicolons inside strings
    don't trigger the structural checks."""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
            out.append(" ")
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
        elif ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    j += 2
                    continue
                if sql[j] == "'":
                    break
                j += 1
            out.append("''")
            i = j + 1
        elif ch == '"':
            j = sql.find('"', i + 1)
            j = n - 1 if j == -1 else j
            out.append(sql[i:j + 1])   # keep quoted identifiers (they may contain 'into')
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def validate_sql(sql: str) -> str:
    if sql is None or not sql.strip():
        raise SqlRejected("Fyrirspurnin er tóm.")
    cleaned = _strip_comments_and_literals(sql).strip()
    # Allow exactly one optional trailing semicolon.
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if ";" in cleaned:
        raise SqlRejected("Aðeins ein setning í einu (semikomma fannst inni í fyrirspurninni).")
    if not cleaned:
        raise SqlRejected("Fyrirspurnin er tóm.")
    first = re.match(r"[A-Za-z]+", cleaned)
    if not first or first.group(0).upper() not in _ALLOWED_FIRST:
        raise SqlRejected("Aðeins lesfyrirspurnir: setningin verður að byrja á SELECT, WITH, EXPLAIN, SHOW, TABLE eða VALUES.")
    if _MODIFYING.search(cleaned):
        raise SqlRejected("Fyrirspurnin inniheldur breytingaskipun (INSERT/UPDATE/DELETE/DDL). Þjónninn er read-only.")
    m = _FORBIDDEN_FUNCS.search(cleaned)
    if m:
        raise SqlRejected(f"Fallið {m.group(1)} er ekki leyft.")
    if _SELECT_INTO.search(cleaned):
        raise SqlRejected("SELECT … INTO (ný tafla) er ekki leyft.")
    # Return the original text minus comments? No — return the user's SQL with
    # only outer whitespace and one trailing ';' removed, so Postgres sees the
    # exact query (comments are harmless to the executor).
    original = sql.strip()
    if original.endswith(";"):
        original = original[:-1].rstrip()
    return original
