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
_FORBIDDEN_FUNCS = re.compile(r"\b(pg_sleep|pg_sleep_for|pg_sleep_until|pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|lo_unlink|dblink|dblink_exec|pg_terminate_backend|pg_cancel_backend|set_config|pg_reload_conf|pg_advisory_lock|pg_advisory_lock_shared|pg_advisory_xact_lock|pg_try_advisory_lock)\b", re.I)
_SELECT_INTO = re.compile(r"\bSELECT\b(?:(?!\bFROM\b).)*?\bINTO\b", re.I | re.S)
# A dollar-quote tag: `$$` (empty tag) or `$tag$` (tag starts with a letter/underscore).
_DOLLAR_TAG = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")
# Leading keyword, allowing a run of opening parens/whitespace first so that
# `(SELECT 1) UNION ALL (SELECT 2)` and `  (SELECT ...)` are recognised.
_LEADING_KEYWORD = re.compile(r"[(\s]*([A-Za-z]+)")
# Non-space filler for literal/identifier *content*. Comments are blanked to
# spaces (so a trailing comment reads as trailing whitespace and gets
# trimmed); literal bodies must NOT be spaces, or a query that ends in a
# literal (`... WHERE x = 'foo'`) would look like it ends in whitespace and
# get truncated. Delimiters (quotes, dollar-tags) are always kept as-is.
_FILL = "_"


class SqlRejected(ValueError):
    """Raised with an Icelandic reason the LLM can act on."""


def _strip_comments_and_literals(sql: str) -> str:
    """Blank out comments, string/dollar-quoted literals and quoted-identifier
    bodies so keywords or semicolons inside them ('%delete%', 'a;b', "update")
    don't trigger the structural checks. Every blanked span is replaced with
    same-length filler so the result stays index-aligned with `sql` — this
    lets callers slice the *original* text using boundaries computed here
    without corrupting literal content. Comments become spaces (trailing
    comments then look like trailing whitespace); literal/identifier bodies
    become `_FILL` with their delimiters kept, so a literal at the very end
    of the query is never mistaken for trimmable trailing whitespace.
    """
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            end = n if j == -1 else j
            out.append(" " * (end - i))
            i = end
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            end = n if j == -1 else j + 2
            out.append(" " * (end - i))
            i = end
        elif ch == "$" and _DOLLAR_TAG.match(sql, i):
            tag = _DOLLAR_TAG.match(sql, i).group(0)
            close = sql.find(tag, i + len(tag))
            if close == -1:
                out.append(tag + _FILL * (n - i - len(tag)))
                i = n
            else:
                content_len = close - (i + len(tag))
                out.append(tag + _FILL * content_len + tag)
                i = close + len(tag)
        elif ch == "'":
            # A literal immediately preceded by E/e (e.g. E'\'') is a
            # Postgres escape-string: backslash escapes the next character.
            is_e_string = i > 0 and sql[i - 1] in "Ee"
            j = i + 1
            closed = False
            while j < n:
                if is_e_string and sql[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if sql[j] == "'" and j + 1 < n and sql[j + 1] == "'":
                    j += 2
                    continue
                if sql[j] == "'":
                    j += 1
                    closed = True
                    break
                j += 1
            span_len = j - i
            if closed and span_len >= 2:
                out.append("'" + _FILL * (span_len - 2) + "'")
            else:
                # Unterminated literal: keep the opening quote, fill the rest.
                out.append("'" + _FILL * (span_len - 1))
            i = j
        elif ch == '"':
            j = sql.find('"', i + 1)
            if j == -1:
                out.append('"' + " " * (n - i - 1))
                i = n
            else:
                out.append('"' + " " * (j - i - 1) + '"')
                i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def validate_sql(sql: str) -> str:
    if sql is None or not sql.strip():
        raise SqlRejected("Fyrirspurnin er tóm.")

    masked = _strip_comments_and_literals(sql)

    # `start`/`end` bound the "real" query inside `sql`, trimming outer
    # whitespace and (via `masked`, where comments are already blank) any
    # trailing comment, plus at most one trailing semicolon.
    start = 0
    while start < len(sql) and sql[start].isspace():
        start += 1
    end = len(sql)
    while end > start and masked[end - 1].isspace():
        end -= 1
    if end > start and masked[end - 1] == ";":
        end -= 1
        while end > start and masked[end - 1].isspace():
            end -= 1

    cleaned = masked[start:end].strip()
    if ";" in cleaned:
        raise SqlRejected("Aðeins ein setning í einu (semikomma fannst inni í fyrirspurninni).")
    if not cleaned:
        raise SqlRejected("Fyrirspurnin er tóm.")
    first = _LEADING_KEYWORD.match(cleaned)
    if not first or first.group(1).upper() not in _ALLOWED_FIRST:
        raise SqlRejected("Aðeins lesfyrirspurnir: setningin verður að byrja á SELECT, WITH, EXPLAIN, SHOW, TABLE eða VALUES.")
    if _MODIFYING.search(cleaned):
        raise SqlRejected("Fyrirspurnin inniheldur breytingaskipun (INSERT/UPDATE/DELETE/DDL). Þjónninn er read-only.")
    m = _FORBIDDEN_FUNCS.search(cleaned)
    if m:
        raise SqlRejected(f"Fallið {m.group(1)} er ekki leyft.")
    if _SELECT_INTO.search(cleaned):
        raise SqlRejected("SELECT … INTO (ný tafla) er ekki leyft.")

    # Return the user's SQL with only outer whitespace, a trailing comment
    # and one trailing ';' removed (comments elsewhere are harmless to the
    # executor). Belt-and-braces: re-mask the result and confirm it is truly
    # semicolon-free before handing it back.
    result = sql[start:end]
    if ";" in _strip_comments_and_literals(result):
        raise SqlRejected("Aðeins ein setning í einu (semikomma fannst inni í fyrirspurninni).")
    return result
