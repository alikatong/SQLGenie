from __future__ import annotations

import sqlite3
import json
import time
from datetime import datetime, timedelta, timezone

from .auth import get_password_hash, verify_password
from .config import (
    EXAMPLE_ADMIN_PASSWORD,
    INSECURE_DEFAULT_ADMIN_PASSWORD,
    LOOPBACK_HOSTS,
    default_model_config,
    normalize_prompt_max_chars,
    normalize_reasoning_effort,
    validate_qwen_embedding_model_path,
    settings,
)
from .utils import utc_now
from .his_semantics import normalize_and_validate_bindings, row_to_term
from .rag import (
    delete_schema_rag_index,
    delete_schema_rag_collection,
    delete_sql_feedback_rag_index,
    sync_schema_rag_index,
    validate_feedback_for_rag,
)
from .schemas import (
    ConfigUpdate,
    DbDefinitionCreate,
    DbDefinitionUpdate,
    HisSemanticTermCreate,
    HisSemanticTermQuery,
    HisSemanticTermUpdate,
    SingleTableUploadRequest,
    SqlHistoryQuery,
    TableUploadRequest,
    UserCreateRequest,
    UserPasswordResetRequest,
)


_LAST_RETENTION_PURGE = 0.0
_RETENTION_PURGE_INTERVAL_SECONDS = 300.0


class FeedbackValidationError(ValueError):
    def __init__(self, issues: list[dict[str, str]]) -> None:
        self.issues = issues
        super().__init__(issues[0]["message"] if issues else "反馈未通过本地策略校验。")


def _parse_bool_config(raw_value: str | bool | None, default: bool) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is None:
        return default
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int_config(raw_value: str | int | None, default: int) -> int:
    try:
        parsed = int(raw_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _sync_rag_and_return_schema(connection: sqlite3.Connection, db_id: int) -> dict:
    schema = get_table_schema(connection, db_id)
    schema_bundle = get_schema_bundle(connection, db_id)
    if schema_bundle is not None:
        runtime = get_model_runtime_config(connection)
        sync_schema_rag_index(
            connection,
            schema_bundle=schema_bundle,
            force=True,
            embedding_model_path=str(runtime["embedding_model_path"]),
        )
    else:
        delete_schema_rag_index(connection, db_id)
    return schema


def ensure_default_admin(connection: sqlite3.Connection) -> None:
    existing_user = get_user_by_username(connection, settings.admin_username)
    if existing_user is not None:
        return

    connection.execute(
        """
        INSERT INTO users (username, password, role, created_at)
        VALUES (?, ?, 'admin', ?)
        """,
        (
            settings.admin_username,
            get_password_hash(settings.admin_password),
            utc_now(),
        ),
    )


def validate_persisted_admin_password(connection: sqlite3.Connection) -> None:
    """Reject a network launch when the configured admin still uses the known default."""
    if settings.app_host.strip().lower() in LOOPBACK_HOSTS:
        return

    admin = get_user_by_username(connection, settings.admin_username)
    if admin is not None and any(
        verify_password(candidate, admin["password"])
        for candidate in (INSECURE_DEFAULT_ADMIN_PASSWORD, EXAMPLE_ADMIN_PASSWORD)
    ):
        raise RuntimeError(
            "Refusing to expose SQLGenie on the network while the configured admin "
            "still uses the known default password. Reset that account's password first."
        )


def get_user_by_username(connection: sqlite3.Connection, username: str) -> dict | None:
    cursor = connection.execute(
        """
        SELECT id, username, password, role, token_version, created_at
        FROM users
        WHERE username = ?
        """,
        (username,),
    )
    return _row_to_dict(cursor.fetchone())


def list_users(connection: sqlite3.Connection) -> list[dict]:
    cursor = connection.execute(
        """
        SELECT id, username, role, created_at
        FROM users
        ORDER BY id ASC
        """
    )
    return [dict(row) for row in cursor.fetchall()]


def get_user_by_id(connection: sqlite3.Connection, user_id: int) -> dict | None:
    cursor = connection.execute(
        """
        SELECT id, username, role, token_version, created_at
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    )
    return _row_to_dict(cursor.fetchone())


def is_default_admin(user: dict | None) -> bool:
    return bool(user and user["role"] == "admin" and user["username"] == settings.admin_username)


def create_user(connection: sqlite3.Connection, payload: UserCreateRequest) -> dict:
    cursor = connection.execute(
        """
        INSERT INTO users (username, password, role, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            payload.username.strip(),
            get_password_hash(payload.password),
            payload.role,
            utc_now(),
        ),
    )
    created_id = cursor.lastrowid
    user_cursor = connection.execute(
        """
        SELECT id, username, role, token_version, created_at
        FROM users
        WHERE id = ?
        """,
        (created_id,),
    )
    return dict(user_cursor.fetchone())


def delete_user(connection: sqlite3.Connection, user_id: int) -> bool:
    user = get_user_by_id(connection, user_id)
    if user is None or is_default_admin(user):
        return False
    cursor = connection.execute(
        "DELETE FROM users WHERE id = ?",
        (user_id,),
    )
    return cursor.rowcount > 0


def reset_user_password(
    connection: sqlite3.Connection,
    user_id: int,
    payload: UserPasswordResetRequest,
) -> dict | None:
    user = get_user_by_id(connection, user_id)
    if user is None or is_default_admin(user):
        return None
    cursor = connection.execute(
        """
        UPDATE users
        SET password = ?, token_version = token_version + 1
        WHERE id = ?
        """,
        (get_password_hash(payload.password), user_id),
    )
    if cursor.rowcount == 0:
        return None
    return get_user_by_id(connection, user_id)


def update_user_role(connection: sqlite3.Connection, user_id: int, role: str) -> dict | None:
    user = get_user_by_id(connection, user_id)
    if user is None or is_default_admin(user):
        return None

    cursor = connection.execute(
        """
        UPDATE users
        SET role = ?
        WHERE id = ?
        """,
        (role, user_id),
    )
    if cursor.rowcount == 0:
        return None
    return get_user_by_id(connection, user_id)


def authenticate_user(connection: sqlite3.Connection, username: str, password: str) -> dict | None:
    user = get_user_by_username(connection, username)
    if user is None:
        return None

    if not verify_password(password, user["password"]):
        return None

    return user


def list_db_definitions(connection: sqlite3.Connection) -> list[dict]:
    cursor = connection.execute(
        """
        SELECT id, name, db_type, created_by
        FROM db_definitions
        ORDER BY id DESC
        """
    )
    return [dict(row) for row in cursor.fetchall()]


def get_db_definition(connection: sqlite3.Connection, db_id: int) -> dict | None:
    cursor = connection.execute(
        """
        SELECT id, name, db_type, created_by
        FROM db_definitions
        WHERE id = ?
        """,
        (db_id,),
    )
    return _row_to_dict(cursor.fetchone())


def create_db_definition(
    connection: sqlite3.Connection,
    payload: DbDefinitionCreate,
    created_by: int,
) -> dict:
    cursor = connection.execute(
        """
        INSERT INTO db_definitions (name, db_type, created_by)
        VALUES (?, ?, ?)
        """,
        (payload.name.strip(), payload.db_type, created_by),
    )
    return get_db_definition(connection, cursor.lastrowid)


def update_db_definition(
    connection: sqlite3.Connection,
    db_id: int,
    payload: DbDefinitionUpdate,
) -> dict | None:
    cursor = connection.execute(
        """
        UPDATE db_definitions
        SET name = ?, db_type = ?
        WHERE id = ?
        """,
        (payload.name.strip(), payload.db_type, db_id),
    )
    if cursor.rowcount == 0:
        return None
    return get_db_definition(connection, db_id)


def delete_db_definition(connection: sqlite3.Connection, db_id: int) -> bool:
    # Remove the SQLite index as part of the metadata transaction. External
    # vector cleanup happens only after the metadata has been committed.
    delete_schema_rag_index(connection, db_id, delete_vector=False)
    connection.execute("DELETE FROM sql_feedback WHERE db_id = ?", (db_id,))
    connection.execute("DELETE FROM sql_history WHERE db_id = ?", (db_id,))
    connection.execute("DELETE FROM table_relation WHERE db_id = ?", (db_id,))
    connection.execute(
        """
        DELETE FROM column_meta
        WHERE table_id IN (
            SELECT id
            FROM table_meta
            WHERE db_id = ?
        )
        """,
        (db_id,),
    )
    connection.execute("DELETE FROM table_meta WHERE db_id = ?", (db_id,))
    cursor = connection.execute("DELETE FROM db_definitions WHERE id = ?", (db_id,))
    deleted = cursor.rowcount > 0
    connection.commit()
    delete_schema_rag_collection(db_id)
    delete_sql_feedback_rag_index(db_id)
    return deleted


def replace_table_schema(
    connection: sqlite3.Connection,
    db_id: int,
    payload: TableUploadRequest,
) -> dict:
    if get_db_definition(connection, db_id) is None:
        raise ValueError("目标数据库定义不存在。")

    table_name_to_id: dict[str, int] = {}
    table_name_to_columns: dict[str, set[str]] = {}

    connection.execute("DELETE FROM table_relation WHERE db_id = ?", (db_id,))
    connection.execute("DELETE FROM table_meta WHERE db_id = ?", (db_id,))

    for table in payload.tables:
        normalized_table_name = table.table_name.strip()
        if normalized_table_name in table_name_to_id:
            raise ValueError(f"上传数据中存在重复表名：{normalized_table_name}")

        table_cursor = connection.execute(
            """
            INSERT INTO table_meta (db_id, table_name, table_comment)
            VALUES (?, ?, ?)
            """,
            (db_id, normalized_table_name, table.table_comment.strip()),
        )
        table_id = int(table_cursor.lastrowid)
        table_name_to_id[normalized_table_name] = table_id

        seen_columns: set[str] = set()
        for column in table.columns:
            normalized_column_name = column.column_name.strip()
            if normalized_column_name in seen_columns:
                raise ValueError(f"表 {normalized_table_name} 中存在重复字段：{normalized_column_name}")
            seen_columns.add(normalized_column_name)
            connection.execute(
                """
                INSERT INTO column_meta (table_id, column_name, data_type, column_comment)
                VALUES (?, ?, ?, ?)
                """,
                (
                    table_id,
                    normalized_column_name,
                    column.data_type.strip(),
                    column.column_comment.strip(),
                ),
            )
        table_name_to_columns[normalized_table_name] = seen_columns

    for relation in payload.relations:
        from_table_name = relation.from_table.strip()
        to_table_name = relation.to_table.strip()
        from_column_name = relation.from_column.strip()
        to_column_name = relation.to_column.strip()

        from_table_id = table_name_to_id.get(from_table_name)
        to_table_id = table_name_to_id.get(to_table_name)
        if from_table_id is None or to_table_id is None:
            raise ValueError(
                f"关系 {from_table_name}.{from_column_name} -> "
                f"{to_table_name}.{to_column_name} 引用了不存在的表。"
            )
        if from_column_name not in table_name_to_columns.get(from_table_name, set()):
            raise ValueError(f"关系引用了不存在的字段：{from_table_name}.{from_column_name}")
        if to_column_name not in table_name_to_columns.get(to_table_name, set()):
            raise ValueError(f"关系引用了不存在的字段：{to_table_name}.{to_column_name}")

        connection.execute(
            """
            INSERT INTO table_relation (
                db_id,
                from_table_id,
                from_column,
                to_table_id,
                to_column,
                relation_type
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                db_id,
                from_table_id,
                from_column_name,
                to_table_id,
                to_column_name,
                relation.relation_type.strip(),
            ),
        )

    return _sync_rag_and_return_schema(connection, db_id)


def _get_table_row_by_name(connection: sqlite3.Connection, db_id: int, table_name: str) -> dict | None:
    cursor = connection.execute(
        """
        SELECT id, db_id, table_name, table_comment
        FROM table_meta
        WHERE db_id = ? AND table_name = ?
        """,
        (db_id, table_name),
    )
    return _row_to_dict(cursor.fetchone())


def _list_table_names(connection: sqlite3.Connection, db_id: int) -> set[str]:
    cursor = connection.execute(
        """
        SELECT table_name
        FROM table_meta
        WHERE db_id = ?
        """,
        (db_id,),
    )
    return {row["table_name"] for row in cursor.fetchall()}


def _list_table_columns(connection: sqlite3.Connection, table_id: int) -> set[str]:
    cursor = connection.execute(
        """
        SELECT column_name
        FROM column_meta
        WHERE table_id = ?
        """,
        (table_id,),
    )
    return {row["column_name"] for row in cursor.fetchall()}


def _validate_table_columns(table_name: str, column_names: list[str]) -> None:
    seen_columns: set[str] = set()
    for column_name in column_names:
        if column_name in seen_columns:
            raise ValueError(f"表 {table_name} 中存在重复字段：{column_name}")
        seen_columns.add(column_name)


def _replace_table_relations_for_scope(
    connection: sqlite3.Connection,
    *,
    db_id: int,
    table_id: int,
    table_name: str,
    relation_payloads: list,
) -> None:
    connection.execute(
        """
        DELETE FROM table_relation
        WHERE db_id = ?
          AND (from_table_id = ? OR to_table_id = ?)
        """,
        (db_id, table_id, table_id),
    )

    current_tables = _list_table_names(connection, db_id)
    current_table_columns: dict[str, set[str]] = {}
    for existing_table_name in current_tables:
        table_row = _get_table_row_by_name(connection, db_id, existing_table_name)
        if table_row is None:
            continue
        current_table_columns[existing_table_name] = _list_table_columns(connection, table_row["id"])

    for relation in relation_payloads:
        from_table_name = relation.from_table.strip()
        to_table_name = relation.to_table.strip()
        from_column_name = relation.from_column.strip()
        to_column_name = relation.to_column.strip()

        if from_table_name != table_name and to_table_name != table_name:
            raise ValueError(
                f"单表导入时，关系 {from_table_name}.{from_column_name} -> "
                f"{to_table_name}.{to_column_name} 必须包含当前表 {table_name}。"
            )

        from_table_row = _get_table_row_by_name(connection, db_id, from_table_name)
        to_table_row = _get_table_row_by_name(connection, db_id, to_table_name)
        if from_table_row is None or to_table_row is None:
            raise ValueError(
                f"关系 {from_table_name}.{from_column_name} -> "
                f"{to_table_name}.{to_column_name} 引用了不存在的表。"
            )

        if from_column_name not in current_table_columns.get(from_table_name, set()):
            raise ValueError(f"关系引用了不存在的字段：{from_table_name}.{from_column_name}")
        if to_column_name not in current_table_columns.get(to_table_name, set()):
            raise ValueError(f"关系引用了不存在的字段：{to_table_name}.{to_column_name}")

        connection.execute(
            """
            INSERT INTO table_relation (
                db_id,
                from_table_id,
                from_column,
                to_table_id,
                to_column,
                relation_type
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                db_id,
                from_table_row["id"],
                from_column_name,
                to_table_row["id"],
                to_column_name,
                relation.relation_type.strip(),
            ),
        )


def upsert_single_table_schema(
    connection: sqlite3.Connection,
    db_id: int,
    payload: SingleTableUploadRequest,
) -> dict:
    if get_db_definition(connection, db_id) is None:
        raise ValueError("目标数据库定义不存在。")

    table = payload.table
    normalized_table_name = table.table_name.strip()
    normalized_column_names = [column.column_name.strip() for column in table.columns]
    _validate_table_columns(normalized_table_name, normalized_column_names)

    existing_table = _get_table_row_by_name(connection, db_id, normalized_table_name)
    if existing_table is not None:
        table_id = existing_table["id"]
        connection.execute(
            """
            UPDATE table_meta
            SET table_comment = ?
            WHERE id = ?
            """,
            (table.table_comment.strip(), table_id),
        )
        connection.execute("DELETE FROM column_meta WHERE table_id = ?", (table_id,))
    else:
        table_cursor = connection.execute(
            """
            INSERT INTO table_meta (db_id, table_name, table_comment)
            VALUES (?, ?, ?)
            """,
            (db_id, normalized_table_name, table.table_comment.strip()),
        )
        table_id = int(table_cursor.lastrowid)

    for column in table.columns:
        connection.execute(
            """
            INSERT INTO column_meta (table_id, column_name, data_type, column_comment)
            VALUES (?, ?, ?, ?)
            """,
            (
                table_id,
                column.column_name.strip(),
                column.data_type.strip(),
                column.column_comment.strip(),
            ),
        )

    _replace_table_relations_for_scope(
        connection,
        db_id=db_id,
        table_id=table_id,
        table_name=normalized_table_name,
        relation_payloads=payload.relations,
    )

    return _sync_rag_and_return_schema(connection, db_id)


def delete_single_table_schema(connection: sqlite3.Connection, db_id: int, table_name: str) -> dict:
    if get_db_definition(connection, db_id) is None:
        raise ValueError("目标数据库定义不存在。")

    normalized_table_name = table_name.strip()
    existing_table = _get_table_row_by_name(connection, db_id, normalized_table_name)
    if existing_table is None:
        raise ValueError("目标数据表不存在。")

    table_id = existing_table["id"]
    connection.execute(
        """
        DELETE FROM table_relation
        WHERE db_id = ?
          AND (from_table_id = ? OR to_table_id = ?)
        """,
        (db_id, table_id, table_id),
    )
    connection.execute("DELETE FROM column_meta WHERE table_id = ?", (table_id,))
    connection.execute("DELETE FROM table_meta WHERE id = ?", (table_id,))
    return _sync_rag_and_return_schema(connection, db_id)


def get_table_schema(connection: sqlite3.Connection, db_id: int) -> dict:
    table_cursor = connection.execute(
        """
        SELECT id, table_name, table_comment
        FROM table_meta
        WHERE db_id = ?
        ORDER BY table_name ASC
        """,
        (db_id,),
    )
    tables: list[dict] = []

    for table_row in table_cursor.fetchall():
        table = dict(table_row)
        column_cursor = connection.execute(
            """
            SELECT id, column_name, data_type, column_comment
            FROM column_meta
            WHERE table_id = ?
            ORDER BY id ASC
            """,
            (table["id"],),
        )
        table["columns"] = [dict(row) for row in column_cursor.fetchall()]
        tables.append(table)

    relation_cursor = connection.execute(
        """
        SELECT
            relation.id,
            relation.from_table_id,
            from_table.table_name AS from_table,
            relation.from_column,
            relation.to_table_id,
            to_table.table_name AS to_table,
            relation.to_column,
            relation.relation_type
        FROM table_relation AS relation
        JOIN table_meta AS from_table ON from_table.id = relation.from_table_id
        JOIN table_meta AS to_table ON to_table.id = relation.to_table_id
        WHERE relation.db_id = ?
        ORDER BY relation.id ASC
        """,
        (db_id,),
    )

    return {
        "db_id": db_id,
        "tables": tables,
        "relations": [dict(row) for row in relation_cursor.fetchall()],
    }


def get_schema_bundle(connection: sqlite3.Connection, db_id: int) -> dict | None:
    db_definition = get_db_definition(connection, db_id)
    if db_definition is None:
        return None

    schema = get_table_schema(connection, db_id)
    return {
        "db_definition": db_definition,
        "tables": schema["tables"],
        "relations": schema["relations"],
    }


def get_model_runtime_config(connection: sqlite3.Connection) -> dict[str, str | bool | int | None]:
    config = default_model_config()
    cursor = connection.execute(
        """
        SELECT key, value
        FROM app_config
        WHERE key IN (
            'api_key',
            'base_url',
            'model_name',
            'enable_thinking',
            'reasoning_effort',
            'thinking_timeout_seconds',
            'prompt_max_chars',
            'rag_top_k',
            'feedback_rag_top_k',
            'embedding_model_path'
        )
        """
    )

    for row in cursor.fetchall():
        key = row["key"]
        raw_value = row["value"]
        if key == "enable_thinking":
            config[key] = _parse_bool_config(raw_value, bool(config["enable_thinking"]))
        elif key == "reasoning_effort":
            config[key] = normalize_reasoning_effort(raw_value)
        elif key == "prompt_max_chars":
            config[key] = normalize_prompt_max_chars(raw_value)
        elif key in {"thinking_timeout_seconds", "rag_top_k", "feedback_rag_top_k"}:
            config[key] = _parse_int_config(raw_value, int(config[key]))
        else:
            config[key] = raw_value

    embedding_model_path = str(config.get("embedding_model_path") or settings.rag_embedding_model)
    config["embedding_model_path"] = embedding_model_path
    config["rag_embedding_model"] = embedding_model_path
    config["rag_expand_depth"] = settings.rag_expand_depth
    return config


def get_model_config(connection: sqlite3.Connection) -> dict[str, str | bool | int | None]:
    """Compatibility alias for server-only callers; never expose through an API route."""
    return get_model_runtime_config(connection)


def get_model_config_view(connection: sqlite3.Connection) -> dict[str, str | bool | int | None]:
    runtime = get_model_runtime_config(connection)
    api_key = str(runtime.get("api_key", ""))
    return {
        "api_key_configured": bool(api_key),
        "api_key_last4": api_key[-4:] if len(api_key) > 4 else "",
        "base_url": str(runtime["base_url"]),
        "model_name": str(runtime["model_name"]),
        "enable_thinking": bool(runtime["enable_thinking"]),
        "reasoning_effort": normalize_reasoning_effort(runtime.get("reasoning_effort")),
        "thinking_timeout_seconds": int(runtime["thinking_timeout_seconds"]),
        "prompt_max_chars": normalize_prompt_max_chars(runtime["prompt_max_chars"]),
        "rag_embedding_model": str(runtime["rag_embedding_model"]),
        "embedding_model_path": str(runtime["embedding_model_path"]),
        "embedding_model_family": "Qwen",
        "rag_top_k": int(runtime["rag_top_k"]),
        "rag_expand_depth": int(runtime["rag_expand_depth"]),
    }


def _term_payload_values(payload, schema_bundle: dict | None) -> tuple:
    bindings = normalize_and_validate_bindings(
        db_id=payload.db_id,
        bindings=[binding.model_dump() for binding in payload.bindings],
        schema_bundle=schema_bundle,
    )
    synonyms: list[str] = []
    seen: set[str] = set()
    for raw_synonym in payload.synonyms:
        synonym = raw_synonym.strip()
        key = synonym.casefold()
        if key not in seen:
            synonyms.append(synonym)
            seen.add(key)
    return (
        payload.db_id,
        payload.term.strip(),
        json.dumps(synonyms, ensure_ascii=False),
        payload.definition.strip(),
        payload.category,
        json.dumps(bindings, ensure_ascii=False),
        payload.sql_hint.strip(),
        int(payload.enabled),
    )


def create_his_semantic_term(
    connection: sqlite3.Connection,
    payload: HisSemanticTermCreate,
    created_by: int,
) -> dict:
    schema_bundle = get_schema_bundle(connection, payload.db_id) if payload.db_id is not None else None
    values = _term_payload_values(payload, schema_bundle)
    timestamp = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO his_semantic_term (
            db_id, term, synonyms_json, definition, category, bindings_json,
            sql_hint, enabled, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (*values, created_by, timestamp, timestamp),
    )
    row = connection.execute(
        "SELECT * FROM his_semantic_term WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return row_to_term(row)


def get_his_semantic_term(connection: sqlite3.Connection, term_id: int) -> dict | None:
    row = connection.execute(
        "SELECT * FROM his_semantic_term WHERE id = ?",
        (term_id,),
    ).fetchone()
    return row_to_term(row) if row is not None else None


def list_his_semantic_terms(connection: sqlite3.Connection, query: HisSemanticTermQuery) -> dict:
    where = "WHERE 1 = 1"
    params: list = []
    if query.db_id is not None:
        where += " AND db_id = ?"
        params.append(query.db_id)
    if query.enabled is not None:
        where += " AND enabled = ?"
        params.append(int(query.enabled))
    if query.category is not None:
        where += " AND category = ?"
        params.append(query.category)
    if query.search.strip():
        where += " AND (lower(term) LIKE ? OR lower(synonyms_json) LIKE ? OR lower(definition) LIKE ?)"
        pattern = f"%{query.search.strip().casefold()}%"
        params.extend((pattern, pattern, pattern))

    total = int(connection.execute(f"SELECT COUNT(*) FROM his_semantic_term {where}", params).fetchone()[0])
    offset = (query.page - 1) * query.page_size
    rows = connection.execute(
        f"""
        SELECT * FROM his_semantic_term
        {where}
        ORDER BY CASE WHEN db_id IS NULL THEN 1 ELSE 0 END, updated_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, query.page_size, offset],
    ).fetchall()
    return {
        "items": [row_to_term(row) for row in rows],
        "total": total,
        "page": query.page,
        "page_size": query.page_size,
    }


def update_his_semantic_term(
    connection: sqlite3.Connection,
    term_id: int,
    payload: HisSemanticTermUpdate,
) -> dict | None:
    if get_his_semantic_term(connection, term_id) is None:
        return None
    schema_bundle = get_schema_bundle(connection, payload.db_id) if payload.db_id is not None else None
    values = _term_payload_values(payload, schema_bundle)
    connection.execute(
        """
        UPDATE his_semantic_term
        SET db_id = ?, term = ?, synonyms_json = ?, definition = ?, category = ?,
            bindings_json = ?, sql_hint = ?, enabled = ?, updated_at = ?
        WHERE id = ?
        """,
        (*values, utc_now(), term_id),
    )
    return get_his_semantic_term(connection, term_id)


def delete_his_semantic_term(connection: sqlite3.Connection, term_id: int) -> bool:
    cursor = connection.execute("DELETE FROM his_semantic_term WHERE id = ?", (term_id,))
    return cursor.rowcount > 0


def get_feedback_rag_config(connection: sqlite3.Connection) -> dict[str, int]:
    row = connection.execute(
        "SELECT value FROM app_config WHERE key = 'feedback_rag_top_k'"
    ).fetchone()
    raw_value = row["value"] if row is not None else settings.feedback_rag_top_k
    return {"top_k": _parse_int_config(raw_value, settings.feedback_rag_top_k)}


def update_feedback_rag_config(connection: sqlite3.Connection, top_k: int) -> dict[str, int]:
    connection.execute(
        """
        INSERT INTO app_config (key, value, updated_at)
        VALUES ('feedback_rag_top_k', ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (str(top_k), utc_now()),
    )
    return get_feedback_rag_config(connection)


def update_model_config(connection: sqlite3.Connection, payload: ConfigUpdate) -> dict[str, str | bool | int | None]:
    values: list[tuple[str, str]] = [
        ("base_url", payload.base_url.strip()),
        ("model_name", payload.model_name.strip()),
        ("enable_thinking", "1" if payload.enable_thinking else "0"),
        ("thinking_timeout_seconds", str(payload.thinking_timeout_seconds)),
        ("prompt_max_chars", str(payload.prompt_max_chars)),
        ("rag_top_k", str(payload.rag_top_k)),
    ]
    # A blank/null path means "keep the existing value": the frontend only
    # sends this field when the admin actually picked a directory, and a
    # pre-existing path must never be wiped by an unrelated config save.
    if (
        "embedding_model_path" in payload.model_fields_set
        and payload.embedding_model_path
        and payload.embedding_model_path.strip()
    ):
        embedding_model_path = validate_qwen_embedding_model_path(payload.embedding_model_path)
        values.append(("embedding_model_path", embedding_model_path))
    # Keep this optional field merge-compatible with older clients that do not
    # send it. An explicit null clears the provider-specific override.
    if "reasoning_effort" in payload.model_fields_set:
        values.append(("reasoning_effort", normalize_reasoning_effort(payload.reasoning_effort) or ""))
    api_key = (payload.api_key or "").strip()
    if api_key:
        values.insert(0, ("api_key", api_key))

    for key, value in values:
        connection.execute(
            """
            INSERT INTO app_config (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, utc_now()),
        )

    return get_model_config_view(connection)


def purge_expired_sql_history(connection: sqlite3.Connection, retention_days: int = 7) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    cursor = connection.execute(
        "DELETE FROM sql_history WHERE created_at < ?",
        (cutoff.isoformat(),),
    )
    return cursor.rowcount


def purge_expired_generation_data(
    connection: sqlite3.Connection,
    retention_days: int = 7,
    *,
    force: bool = False,
) -> dict[str, int]:
    global _LAST_RETENTION_PURGE

    now = time.monotonic()
    if not force and now - _LAST_RETENTION_PURGE < _RETENTION_PURGE_INTERVAL_SECONDS:
        return {"history": 0, "traces": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    history_count = purge_expired_sql_history(connection, retention_days)
    trace_cursor = connection.execute(
        "DELETE FROM generation_trace WHERE created_at < ?",
        (cutoff.isoformat(),),
    )
    _LAST_RETENTION_PURGE = now
    return {"history": history_count, "traces": trace_cursor.rowcount}


def create_generation_trace(connection: sqlite3.Connection, trace: dict) -> dict:
    fields = (
        "request_id",
        "history_id",
        "user_id",
        "db_id",
        "prompt_version",
        "policy_version",
        "context_hash",
        "model_name",
        "retrieval_mode",
        "retrieved_tables_json",
        "retrieved_terms_json",
        "policy_status",
        "validation_errors_json",
        "warnings_json",
        "model_calls",
        "outcome",
        "error_code",
        "duration_ms",
        "prompt_chars",
        "prompt_tokens",
        "completion_tokens",
        "created_at",
    )
    values = dict(trace)
    values.setdefault("history_id", None)
    values.setdefault("prompt_version", "")
    values.setdefault("policy_version", "")
    values.setdefault("context_hash", "")
    values.setdefault("model_name", "")
    values.setdefault("retrieval_mode", "")
    for key in ("retrieved_tables_json", "retrieved_terms_json", "validation_errors_json", "warnings_json"):
        value = values.get(key, [])
        values[key] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    values.setdefault("policy_status", "not_run")
    values.setdefault("model_calls", 0)
    values.setdefault("outcome", "error")
    values.setdefault("error_code", None)
    values.setdefault("duration_ms", 0)
    values.setdefault("prompt_chars", 0)
    values.setdefault("prompt_tokens", None)
    values.setdefault("completion_tokens", None)
    values.setdefault("created_at", utc_now())

    placeholders = ", ".join("?" for _ in fields)
    connection.execute(
        f"INSERT INTO generation_trace ({', '.join(fields)}) VALUES ({placeholders})",
        tuple(values[field] for field in fields),
    )
    row = connection.execute(
        "SELECT * FROM generation_trace WHERE request_id = ?",
        (values["request_id"],),
    ).fetchone()
    return dict(row)


def create_sql_history(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    db_id: int,
    natural_text: str,
    target_db_type: str,
    generated_sql: str,
    retrieved_tables: list[str] | None = None,
) -> dict:
    cursor = connection.execute(
        """
        INSERT INTO sql_history (
            user_id,
            db_id,
            natural_text,
            target_db_type,
            generated_sql,
            retrieved_tables_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            db_id,
            natural_text.strip(),
            target_db_type,
            generated_sql.strip(),
            json.dumps(retrieved_tables or [], ensure_ascii=False),
            utc_now(),
        ),
    )

    history_cursor = connection.execute(
        """
        SELECT
            history.id,
            history.user_id,
            users.username,
            history.db_id,
            db_definitions.name AS db_name,
            history.target_db_type,
            history.natural_text,
            history.generated_sql,
            history.retrieved_tables_json,
            history.created_at
        FROM sql_history AS history
        JOIN users ON users.id = history.user_id
        JOIN db_definitions ON db_definitions.id = history.db_id
        WHERE history.id = ?
        """,
        (cursor.lastrowid,),
    )
    return dict(history_cursor.fetchone())


def create_sql_feedback(
    connection: sqlite3.Connection,
    *,
    history_id: int,
    user_id: int,
    feedback_type: str,
    corrected_sql: str | None,
    approved: bool = False,
) -> dict | None:
    history_cursor = connection.execute(
        """
        SELECT id, user_id, db_id, natural_text, target_db_type, generated_sql
        FROM sql_history
        WHERE id = ? AND user_id = ?
        """,
        (history_id, user_id),
    )
    history = _row_to_dict(history_cursor.fetchone())
    if history is None:
        return None

    if feedback_type == "correct":
        final_sql = history["generated_sql"]
    elif feedback_type == "modified" and corrected_sql and corrected_sql.strip():
        final_sql = corrected_sql.strip()
    else:
        raise ValueError("A non-empty corrected SQL statement is required for modified feedback.")

    cursor = connection.execute(
        """
        INSERT INTO sql_feedback (
            history_id,
            user_id,
            db_id,
            natural_text,
            target_db_type,
            generated_sql,
            corrected_sql,
            feedback_type,
            approved,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            history["id"],
            user_id,
            history["db_id"],
            history["natural_text"],
            history["target_db_type"],
            history["generated_sql"],
            final_sql,
            feedback_type,
            0,
            utc_now(),
        ),
    )
    feedback_id = int(cursor.lastrowid)
    if approved:
        approve_sql_feedback(connection, feedback_id)
    feedback_cursor = connection.execute(
        """
        SELECT
            id,
            history_id,
            db_id,
            feedback_type,
            generated_sql,
            corrected_sql,
            approved,
            created_at
        FROM sql_feedback
        WHERE id = ?
        """,
        (feedback_id,),
    )
    return dict(feedback_cursor.fetchone())


def approve_sql_feedback(connection: sqlite3.Connection, feedback_id: int) -> int | None:
    row = connection.execute(
        """
        SELECT id, db_id, natural_text, target_db_type, corrected_sql
        FROM sql_feedback WHERE id = ?
        """,
        (feedback_id,),
    ).fetchone()
    if row is None:
        return None

    valid, issues = validate_feedback_for_rag(connection, dict(row))
    if not valid:
        raise FeedbackValidationError(issues)

    connection.execute(
        "UPDATE sql_feedback SET approved = 1 WHERE id = ?",
        (feedback_id,),
    )
    return int(row["db_id"])


def list_sql_feedback(connection: sqlite3.Connection, query) -> dict:
    where_sql = """
        FROM sql_feedback AS feedback
        JOIN users ON users.id = feedback.user_id
        JOIN db_definitions ON db_definitions.id = feedback.db_id
        WHERE 1 = 1
    """
    params: list = []
    if query.db_id is not None:
        where_sql += " AND feedback.db_id = ?"
        params.append(query.db_id)
    if query.approved is not None:
        where_sql += " AND feedback.approved = ?"
        params.append(int(query.approved))

    total = int(connection.execute(f"SELECT COUNT(*) {where_sql}", params).fetchone()[0])
    offset = (query.page - 1) * query.page_size
    cursor = connection.execute(
        f"""
        SELECT
            feedback.id,
            feedback.history_id,
            feedback.db_id,
            feedback.feedback_type,
            feedback.generated_sql,
            feedback.corrected_sql,
            feedback.approved,
            feedback.created_at,
            feedback.natural_text,
            feedback.target_db_type,
            users.username,
            db_definitions.name AS db_name
        {where_sql}
        ORDER BY feedback.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, query.page_size, offset],
    )
    return {
        "items": [dict(row) for row in cursor.fetchall()],
        "total": total,
        "page": query.page,
        "page_size": query.page_size,
    }


def delete_sql_feedback(connection: sqlite3.Connection, feedback_id: int) -> int | None:
    row = connection.execute(
        "SELECT db_id FROM sql_feedback WHERE id = ?",
        (feedback_id,),
    ).fetchone()
    if row is None:
        return None
    connection.execute("DELETE FROM sql_feedback WHERE id = ?", (feedback_id,))
    return int(row["db_id"])


def list_sql_history(connection: sqlite3.Connection, query: SqlHistoryQuery) -> dict:
    purge_expired_sql_history(connection)

    where_sql = """
        FROM sql_history AS history
        JOIN users ON users.id = history.user_id
        JOIN db_definitions ON db_definitions.id = history.db_id
        WHERE 1 = 1
    """
    params: list = []

    if query.user_id is not None:
        where_sql += " AND history.user_id = ?"
        params.append(query.user_id)
    if query.date_from:
        where_sql += " AND substr(history.created_at, 1, 10) >= ?"
        params.append(query.date_from)
    if query.date_to:
        where_sql += " AND substr(history.created_at, 1, 10) <= ?"
        params.append(query.date_to)

    count_cursor = connection.execute(f"SELECT COUNT(*) {where_sql}", params)
    total = int(count_cursor.fetchone()[0])

    offset = (query.page - 1) * query.page_size
    data_sql = f"""
        SELECT
            history.id,
            history.user_id,
            users.username,
            history.db_id,
            db_definitions.name AS db_name,
            history.target_db_type,
            history.natural_text,
            history.generated_sql,
            history.retrieved_tables_json,
            history.created_at
        {where_sql}
        ORDER BY history.id DESC
        LIMIT ? OFFSET ?
    """
    cursor = connection.execute(data_sql, [*params, query.page_size, offset])
    return {
        "items": [dict(row) for row in cursor.fetchall()],
        "total": total,
        "page": query.page,
        "page_size": query.page_size,
    }


def list_sql_history_for_user(
    connection: sqlite3.Connection,
    query: SqlHistoryQuery,
    *,
    current_user_id: int,
) -> dict:
    scoped_query = query.model_copy(update={"user_id": current_user_id})
    return list_sql_history(connection, scoped_query)
