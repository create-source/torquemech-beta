from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = Path("/data") if Path("/data").exists() else BASE_DIR / ".localstate"
DB_PATH = str((STATE_DIR / "app.db").resolve())
LOCAL_FALLBACK_DB_PATH = str((STATE_DIR / "dev_runtime_app.db").resolve())
USE_LOCAL_SQLITE_COMPAT = not Path("/data").exists()
LOCAL_DB_MARKER_PATH = STATE_DIR / "active_app_db_path.txt"


def database_url() -> str:
    return (os.getenv("DATABASE_URL") or "").strip()


def using_postgres() -> bool:
    raw = database_url().lower()
    return raw.startswith("postgres://") or raw.startswith("postgresql://")


def normalize_database_url(raw_url: str | None = None) -> str:
    url = (raw_url or database_url()).strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if not url.startswith("postgresql://"):
        return url

    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("sslmode", os.getenv("PGSSLMODE", "require"))
    return urlunparse(parsed._replace(query=urlencode(query)))


def mark_local_fallback_db_active() -> None:
    if not USE_LOCAL_SQLITE_COMPAT:
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        LOCAL_DB_MARKER_PATH.write_text(LOCAL_FALLBACK_DB_PATH, encoding="utf-8")
    except OSError:
        pass


def active_app_db_path() -> str:
    if USE_LOCAL_SQLITE_COMPAT:
        try:
            marked_path = LOCAL_DB_MARKER_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            marked_path = ""
        if marked_path == LOCAL_FALLBACK_DB_PATH:
            return LOCAL_FALLBACK_DB_PATH
    return DB_PATH


def runtime_schema_changes_allowed() -> bool:
    return (os.getenv("TORQUEMECH_ALLOW_RUNTIME_SCHEMA") or "").strip().lower() in {"1", "true", "yes"}


POSTGRES_RUNTIME_SCHEMA_TABLE_ALLOWLIST = {"staff_notifications", "customer_decision_follow_ups"}

_POSTGRES_POOL = None
_POSTGRES_POOL_DSN = ""
_POSTGRES_POOL_LOCK = threading.Lock()


def postgres_connection_pool(dsn: str):
    global _POSTGRES_POOL, _POSTGRES_POOL_DSN
    if _POSTGRES_POOL is not None and _POSTGRES_POOL_DSN == dsn:
        return _POSTGRES_POOL
    with _POSTGRES_POOL_LOCK:
        if _POSTGRES_POOL is None or _POSTGRES_POOL_DSN != dsn:
            from psycopg2.pool import ThreadedConnectionPool

            _POSTGRES_POOL = ThreadedConnectionPool(
                minconn=1,
                maxconn=max(2, int(os.getenv("POSTGRES_POOL_MAX", "10"))),
                dsn=dsn,
            )
            _POSTGRES_POOL_DSN = dsn
    return _POSTGRES_POOL


def connect_app_db(*, row_factory: bool = False):
    if using_postgres():
        return PostgresCompatConnection(normalize_database_url(), row_factory=row_factory)

    conn = sqlite3.connect(active_app_db_path())
    if USE_LOCAL_SQLITE_COMPAT:
        try:
            conn.execute("PRAGMA journal_mode=MEMORY")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            try:
                conn.execute("PRAGMA journal_mode=TRUNCATE")
                conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.OperationalError:
                conn.close()
                mark_local_fallback_db_active()
                conn = sqlite3.connect(LOCAL_FALLBACK_DB_PATH)
                conn.execute("PRAGMA journal_mode=TRUNCATE")
                conn.execute("PRAGMA synchronous=NORMAL")
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn


class PortableRow:
    def __init__(self, columns: Iterable[str], values: Iterable[Any]):
        self._columns = list(columns)
        self._values = tuple(values)
        self._mapping = {column: self._values[index] for index, column in enumerate(self._columns)}

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self) -> list[str]:
        return list(self._columns)

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping.get(key, default)

    def __contains__(self, key: object) -> bool:
        return key in self._mapping


class PostgresCompatCursor:
    def __init__(self, conn: "PostgresCompatConnection", cursor: Any | None = None, rows: list[Any] | None = None):
        self.conn = conn
        self.cursor = cursor
        self._rows = rows
        self.lastrowid: int | None = None

    def execute(self, sql: str, params: Iterable[Any] | None = None):
        return self.conn._execute(sql, params, cursor=self)

    @property
    def rowcount(self) -> int:
        if self.cursor is None:
            return -1
        return int(self.cursor.rowcount)

    def fetchone(self):
        if self._rows is not None:
            return self._rows.pop(0) if self._rows else None
        row = self.cursor.fetchone()
        return self._wrap_row(row)

    def fetchall(self):
        if self._rows is not None:
            rows, self._rows = self._rows, []
            return rows
        return [self._wrap_row(row) for row in self.cursor.fetchall()]

    def _wrap_row(self, row: Any):
        if row is None:
            return None
        columns = [desc[0] for desc in self.cursor.description or []]
        return PortableRow(columns, row)


class PostgresCompatConnection:
    def __init__(self, dsn: str, *, row_factory: bool = False):
        try:
            import psycopg2
        except ImportError as exc:
            raise RuntimeError("DATABASE_URL requires psycopg2-binary to be installed") from exc

        self._psycopg2 = psycopg2
        self._pool = postgres_connection_pool(dsn)
        self._conn = self._pool.getconn()
        self.row_factory = row_factory

    def cursor(self) -> PostgresCompatCursor:
        return PostgresCompatCursor(self, self._conn.cursor())

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> PostgresCompatCursor:
        return self._execute(sql, params)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        if self._conn is None:
            return
        conn, self._conn = self._conn, None
        try:
            conn.rollback()
        except Exception:
            self._pool.putconn(conn, close=True)
        else:
            self._pool.putconn(conn)

    def _execute(
        self,
        sql: str,
        params: Iterable[Any] | None = None,
        *,
        cursor: PostgresCompatCursor | None = None,
    ) -> PostgresCompatCursor:
        sql = sql.strip()
        sql, params_tuple = self._normalize_params(sql, params)

        pragma_rows = self._pragma_table_info(sql)
        if pragma_rows is not None:
            return PostgresCompatCursor(self, rows=pragma_rows)

        sqlite_master_sql_rows = self._sqlite_master_table_sql(sql, params_tuple)
        if sqlite_master_sql_rows is not None:
            return PostgresCompatCursor(self, rows=sqlite_master_sql_rows)

        translated_sql, translated_params = self._translate_sql(sql, params_tuple)
        translated_sql, returns_id = self._add_returning_id_if_needed(sql, translated_sql)
        pg_cursor = cursor.cursor if cursor and cursor.cursor is not None else self._conn.cursor()
        compat_cursor = cursor or PostgresCompatCursor(self, pg_cursor)
        compat_cursor.cursor = pg_cursor

        try:
            pg_cursor.execute(translated_sql, translated_params)
            if returns_id:
                returned = pg_cursor.fetchone()
                compat_cursor.lastrowid = int(returned[0]) if returned else None
            return compat_cursor
        except self._psycopg2.IntegrityError as exc:
            raise sqlite3.IntegrityError(str(exc)) from exc
        except self._psycopg2.OperationalError as exc:
            raise sqlite3.OperationalError(str(exc)) from exc

    def _normalize_params(self, sql: str, params: Iterable[Any] | None = None) -> tuple[str, tuple[Any, ...]]:
        if isinstance(params, dict):
            values: list[Any] = []

            def replace(match: re.Match[str]) -> str:
                name = match.group(1)
                values.append(params[name])
                return "?"

            translated_sql = re.sub(r"(?<!:):([A-Za-z_]\w*)", replace, sql)
            return translated_sql, tuple(values)
        return sql, tuple(params or ())

    def _pragma_table_info(self, sql: str) -> list[PortableRow] | None:
        match = re.fullmatch(r"PRAGMA\s+table_info\(([\w_]+)\)", sql, re.IGNORECASE)
        if not match:
            return None
        table_name = match.group(1)
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT ordinal_position - 1 AS cid,
                   column_name AS name,
                   data_type AS type,
                   CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
                   column_default AS dflt_value,
                   CASE WHEN column_name IN (
                       SELECT a.attname
                       FROM pg_index i
                       JOIN pg_attribute a
                         ON a.attrelid = i.indrelid
                        AND a.attnum = ANY(i.indkey)
                       WHERE i.indrelid = %s::regclass
                         AND i.indisprimary
                   ) THEN 1 ELSE 0 END AS pk
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name, table_name),
        )
        return [PortableRow(["cid", "name", "type", "notnull", "dflt_value", "pk"], row) for row in cur.fetchall()]

    def _sqlite_master_table_sql(self, sql: str, params: tuple[Any, ...]) -> list[PortableRow] | None:
        normalized = re.sub(r"\s+", " ", sql.strip())
        table_name = ""
        if re.fullmatch(r"SELECT sql FROM sqlite_master WHERE type = 'table' AND name = \?", normalized, re.IGNORECASE):
            table_name = str(params[0]) if params else ""
        else:
            literal_match = re.fullmatch(
                r"SELECT sql FROM sqlite_master WHERE type = 'table' AND name = '([\w_]+)'",
                normalized,
                re.IGNORECASE,
            )
            if literal_match:
                table_name = literal_match.group(1)
        if not table_name:
            return None

        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table_name,),
        )
        columns = [row[0] for row in cur.fetchall()]
        if not columns:
            return []
        cur.execute(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = %s::regclass
              AND contype = 'c'
            ORDER BY conname
            """,
            (table_name,),
        )
        checks = [row[0] for row in cur.fetchall()]
        synthetic_sql = f"CREATE TABLE {table_name} ({', '.join(columns + checks)})"
        return [PortableRow(["sql"], [synthetic_sql])]

    def _translate_sql(self, sql: str, params: tuple[Any, ...]) -> tuple[str, tuple[Any, ...]]:
        normalized = re.sub(r"\s+", " ", sql.strip())

        if re.fullmatch(r"SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = \?", normalized, re.IGNORECASE):
            return (
                "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s",
                params,
            )

        literal_table_match = re.fullmatch(
            r"SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = '([\w_]+)'",
            normalized,
            re.IGNORECASE,
        )
        if literal_table_match:
            return (
                "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s",
                (literal_table_match.group(1),),
            )

        if re.fullmatch(r"SELECT name FROM sqlite_master WHERE type = 'table'", normalized, re.IGNORECASE):
            return (
                "SELECT table_name AS name FROM information_schema.tables WHERE table_schema = 'public'",
                (),
            )

        create_match = re.match(r"CREATE TABLE IF NOT EXISTS\s+([\w_]+)", normalized, re.IGNORECASE)
        if create_match and not runtime_schema_changes_allowed():
            table_name = create_match.group(1)
            if table_name in POSTGRES_RUNTIME_SCHEMA_TABLE_ALLOWLIST:
                sql = re.sub(
                    r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
                    "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
                    sql,
                    flags=re.IGNORECASE,
                )
                sql = sql.replace("?", "%s")
                return sql, params
            cur = self._conn.cursor()
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s",
                (table_name,),
            )
            if cur.fetchone() is None:
                raise RuntimeError(
                    f"PostgreSQL schema is missing table '{table_name}'. "
                    "Run the explicit migration before starting the application."
                )
            return "SELECT 1", ()

        if re.match(r"ALTER TABLE\s+[\w_]+\s+ADD COLUMN", normalized, re.IGNORECASE) and not runtime_schema_changes_allowed():
            raise RuntimeError(
                "PostgreSQL runtime schema changes are disabled. Run the explicit migration instead."
            )

        sql = re.sub(
            r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
            "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
            sql,
            flags=re.IGNORECASE,
        )
        original = normalized.upper()
        sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, flags=re.IGNORECASE)
        if "INSERT OR IGNORE INTO" in original and "ON CONFLICT" not in sql.upper():
            sql = f"{sql} ON CONFLICT DO NOTHING"
        sql = self._translate_group_concat(sql)
        sql = sql.replace("?", "%s")
        sql = self._qualify_upsert_self_references(sql)
        return sql, params

    def _translate_group_concat(self, sql: str) -> str:
        def replace_with_separator(match: re.Match[str]) -> str:
            expression = match.group(1).strip()
            separator = match.group(2).strip()
            return f"STRING_AGG(({expression})::text, {separator})"

        sql = re.sub(
            r"GROUP_CONCAT\(\s*([^)]+?)\s*,\s*('[^']*')\s*\)",
            replace_with_separator,
            sql,
            flags=re.IGNORECASE,
        )

        def replace_default(match: re.Match[str]) -> str:
            expression = match.group(1).strip()
            return f"STRING_AGG(({expression})::text, ',')"

        return re.sub(
            r"GROUP_CONCAT\(\s*([^)]+?)\s*\)",
            replace_default,
            sql,
            flags=re.IGNORECASE,
        )

    def _qualify_upsert_self_references(self, sql: str) -> str:
        if "ON CONFLICT" not in sql.upper() or "DO UPDATE SET" not in sql.upper():
            return sql
        table_match = re.match(r"\s*INSERT\s+INTO\s+([\w_]+)", sql, re.IGNORECASE)
        if not table_match:
            return sql
        table_name = table_match.group(1)
        return re.sub(
            r"(?im)^(\s*)([\w_]+)(\s*=\s*)\2(\s*[+\-*/]\s*%s\b)",
            rf"\1\2\3{table_name}.\2\4",
            sql,
        )

    def _is_insert(self, sql: str) -> bool:
        return bool(re.match(r"\s*INSERT\b", sql, re.IGNORECASE))

    def _add_returning_id_if_needed(self, original_sql: str, translated_sql: str) -> tuple[str, bool]:
        if not self._is_insert(original_sql) or re.search(r"\bRETURNING\b", translated_sql, re.IGNORECASE):
            return translated_sql, False
        match = re.match(r"\s*INSERT\s+INTO\s+([\w_]+)", translated_sql, re.IGNORECASE)
        if not match:
            return translated_sql, False
        table_name = match.group(1)
        cur = self._conn.cursor()
        cur.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = 'id'
              AND (is_identity = 'YES' OR column_default LIKE 'nextval%%')
            """,
            (table_name,),
        )
        if cur.fetchone() is None:
            return translated_sql, False
        return f"{translated_sql} RETURNING id", True
