from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings
from .utils import utc_now

logger = logging.getLogger(__name__)

try:
    import chromadb
except ImportError:  # pragma: no cover - optional dependency
    chromadb = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover - optional dependency
    SentenceTransformer = None

try:
    import sqlglot
except ImportError:  # pragma: no cover - optional dependency
    sqlglot = None


@dataclass(slots=True)
class RagDocument:
    db_id: int
    table_id: int
    table_name: str
    table_comment: str
    retrieval_text: str
    ddl_sql: str
    foreign_keys: list[dict[str, str]]
    content_hash: str


class _EmbeddingRuntime:
    def __init__(self) -> None:
        self._model: Any = None

    def is_available(self) -> bool:
        return chromadb is not None and SentenceTransformer is not None

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not self.is_available():
            raise RuntimeError("Local vector dependencies are unavailable.")

        if self._model is None:
            self._model = SentenceTransformer(
                settings.rag_embedding_model,
                local_files_only=_prefer_local_model_files(),
                trust_remote_code=_model_requires_remote_code(),
            )

        encoded_inputs = [_prepare_embedding_input(text) for text in texts]
        vectors = self._model.encode(
            encoded_inputs,
            normalize_embeddings=True,
        )
        return [vector.tolist() for vector in vectors]


class _ChromaEmbeddingFunction:
    def __call__(self, input: list[str]) -> list[list[float]]:
        return _EMBEDDING_RUNTIME.encode(list(input))

    def name(self) -> str:
        model_name = settings.rag_embedding_model.strip() or "embedding"
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", model_name)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return _EMBEDDING_RUNTIME.encode(list(input))

    def embed_query(self, input: str | list[str]) -> list[list[float]] | list[float]:
        if isinstance(input, str):
            return _EMBEDDING_RUNTIME.encode([input])[0]
        return _EMBEDDING_RUNTIME.encode(list(input))


_EMBEDDING_RUNTIME = _EmbeddingRuntime()
_CHROMA_EMBEDDING_FUNCTION = _ChromaEmbeddingFunction()
_CHROMA_CLIENT: Any = None


def _sqlglot_dialect(db_type: str) -> str:
    return {
        "mysql": "mysql",
        "pg": "postgres",
        "oracle": "oracle",
    }.get(db_type, "mysql")


def _vector_search_available() -> bool:
    return _EMBEDDING_RUNTIME.is_available()


def _prefer_local_model_files() -> bool:
    model_name = settings.rag_embedding_model.strip()
    return bool(model_name) and (Path(model_name).exists() or Path(model_name).is_absolute())


def _model_requires_remote_code() -> bool:
    model_name = settings.rag_embedding_model.lower()
    return "bge-m3" in model_name or "bge-large-zh" in model_name


def _prepare_embedding_input(text: str) -> str:
    model_name = settings.rag_embedding_model.lower()
    if "bge" in model_name:
        return f"为这个句子生成用于检索的表示：{text}"
    return text


def _get_chroma_client():
    global _CHROMA_CLIENT

    if chromadb is None:
        raise RuntimeError("chromadb is not installed.")

    if _CHROMA_CLIENT is None:
        settings.rag_chroma_path.mkdir(parents=True, exist_ok=True)
        _CHROMA_CLIENT = chromadb.PersistentClient(path=str(settings.rag_chroma_path))

    return _CHROMA_CLIENT


def _build_collection_name(db_id: int, suffix: str = "") -> str:
    prefix = re.sub(r"[^a-zA-Z0-9_-]+", "_", settings.rag_collection_prefix).strip("_") or "sqlgenie"
    base = f"{prefix}_db_{db_id}"
    return f"{base}_{suffix}" if suffix else base


def _collection_name(db_id: int) -> str:
    return _build_collection_name(db_id)


def _feedback_collection_name(db_id: int) -> str:
    return _build_collection_name(db_id, "feedback")


def _normalize_identifier(name: str) -> str:
    return name.strip()


def _normalize_ddl(ddl_sql: str, db_type: str) -> str:
    if sqlglot is None:
        return ddl_sql

    try:
        parsed = sqlglot.parse_one(ddl_sql, read=_sqlglot_dialect(db_type))
        return parsed.sql(dialect=_sqlglot_dialect(db_type), pretty=True)
    except Exception:
        logger.debug("sqlglot failed to normalize DDL", exc_info=True)
        return ddl_sql


def _extract_foreign_keys_from_ddl(ddl_sql: str, db_type: str, *, normalize: bool = True) -> list[dict[str, str]]:
    normalized_sql = ddl_sql

    if normalize and sqlglot is not None:
        try:
            parsed = sqlglot.parse_one(ddl_sql, read=_sqlglot_dialect(db_type))
            normalized_sql = parsed.sql(dialect=_sqlglot_dialect(db_type), pretty=False)
        except Exception:
            logger.debug("sqlglot failed to parse DDL for foreign keys", exc_info=True)

    matches: list[dict[str, str]] = []
    pattern = re.compile(
        r"FOREIGN KEY\s*\((?P<local>[^\)]+)\)\s+REFERENCES\s+(?P<ref_table>[^\s\(]+)\s*\((?P<remote>[^\)]+)\)",
        flags=re.IGNORECASE,
    )

    for match in pattern.finditer(normalized_sql):
        local_columns = [
            item.strip().strip("\"`[]")
            for item in match.group("local").split(",")
            if item.strip()
        ]
        remote_columns = [
            item.strip().strip("\"`[]")
            for item in match.group("remote").split(",")
            if item.strip()
        ]
        ref_table = match.group("ref_table").strip().strip("\"`[]")

        for local_column, remote_column in zip(local_columns, remote_columns):
            matches.append(
                {
                    "column_name": local_column,
                    "references_table": ref_table,
                    "references_column": remote_column,
                }
            )

    return matches


def _infer_primary_keys(table: dict, relations: list[dict]) -> list[str]:
    column_names = [column["column_name"] for column in table["columns"]]
    pk_candidates = {
        relation["from_column"]
        for relation in relations
        if relation["from_table"] == table["table_name"]
    }

    if "id" in column_names:
        pk_candidates.add("id")

    return [column_name for column_name in column_names if column_name in pk_candidates]


def _build_table_ddl(
    *,
    table: dict,
    child_relations: list[dict],
    primary_keys: list[str],
    db_type: str,
) -> str:
    entries = [
        f"  {_normalize_identifier(column['column_name'])} {column['data_type'].strip()}"
        for column in table["columns"]
    ]

    if primary_keys:
        entries.append(f"  PRIMARY KEY ({', '.join(primary_keys)})")

    for relation in child_relations:
        entries.append(
            "  "
            f"FOREIGN KEY ({relation['to_column']}) "
            f"REFERENCES {relation['from_table']} ({relation['from_column']})"
        )

    ddl_sql = (
        f"CREATE TABLE {_normalize_identifier(table['table_name'])} (\n"
        + ",\n".join(entries)
        + "\n);"
    )
    return _normalize_ddl(ddl_sql, db_type)


def _build_retrieval_text(
    *,
    table: dict,
    primary_keys: list[str],
    foreign_keys: list[dict[str, str]],
) -> str:
    pk_set = set(primary_keys)
    fk_map = {item["column_name"]: item for item in foreign_keys}
    column_segments: list[str] = []

    for column in table["columns"]:
        tags: list[str] = []
        column_name = column["column_name"]
        if column_name in pk_set:
            tags.append("PK")
        if column_name in fk_map:
            fk = fk_map[column_name]
            tags.append(f"FK->{fk['references_table']}.{fk['references_column']}")
        if column["column_comment"]:
            tags.append(f"Comment:{column['column_comment']}")

        tag_suffix = f",{','.join(tags)}" if tags else ""
        column_segments.append(f"{column_name}({column['data_type']}{tag_suffix})")

    parts = [
        f"Table: {table['table_name']}",
        f"Comment: {table['table_comment'] or '-'}",
        f"Columns: {', '.join(column_segments) if column_segments else '-'}",
    ]

    if foreign_keys:
        fk_segments = [
            f"{item['column_name']} -> {item['references_table']}({item['references_column']})"
            for item in foreign_keys
        ]
        parts.append(f"ForeignKeys: {', '.join(fk_segments)}")

    if primary_keys:
        parts.append(f"PrimaryKeys: {', '.join(primary_keys)}")

    return " | ".join(parts)


def _build_documents(schema_bundle: dict) -> list[RagDocument]:
    db_definition = schema_bundle["db_definition"]
    relations = schema_bundle["relations"]
    db_id = db_definition["id"]
    db_type = db_definition["db_type"]
    documents: list[RagDocument] = []

    for table in schema_bundle["tables"]:
        child_relations = [relation for relation in relations if relation["to_table"] == table["table_name"]]
        primary_keys = _infer_primary_keys(table, relations)
        ddl_sql = _build_table_ddl(
            table=table,
            child_relations=child_relations,
            primary_keys=primary_keys,
            db_type=db_type,
        )
        foreign_keys = _extract_foreign_keys_from_ddl(ddl_sql, db_type, normalize=False)
        retrieval_text = _build_retrieval_text(
            table=table,
            primary_keys=primary_keys,
            foreign_keys=foreign_keys,
        )
        content_hash = hashlib.sha256(
            f"{table['table_name']}\n{retrieval_text}\n{ddl_sql}".encode("utf-8")
        ).hexdigest()

        documents.append(
            RagDocument(
                db_id=db_id,
                table_id=int(table["id"]),
                table_name=table["table_name"],
                table_comment=table["table_comment"] or "",
                retrieval_text=retrieval_text,
                ddl_sql=ddl_sql,
                foreign_keys=foreign_keys,
                content_hash=content_hash,
            )
        )

    return documents


def _load_index_rows(connection: sqlite3.Connection, db_id: int) -> list[dict]:
    cursor = connection.execute(
        """
        SELECT
            db_id,
            table_id,
            table_name,
            table_comment,
            retrieval_text,
            ddl_sql,
            foreign_keys_json,
            content_hash,
            indexed_at
        FROM schema_rag_index
        WHERE db_id = ?
        ORDER BY table_name ASC
        """,
        (db_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _persist_index_rows(connection: sqlite3.Connection, db_id: int, documents: list[RagDocument]) -> None:
    connection.execute("DELETE FROM schema_rag_index WHERE db_id = ?", (db_id,))

    for document in documents:
        connection.execute(
            """
            INSERT INTO schema_rag_index (
                db_id,
                table_id,
                table_name,
                table_comment,
                retrieval_text,
                ddl_sql,
                foreign_keys_json,
                content_hash,
                indexed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                db_id,
                document.table_id,
                document.table_name,
                document.table_comment,
                document.retrieval_text,
                document.ddl_sql,
                json.dumps(document.foreign_keys, ensure_ascii=False),
                document.content_hash,
                utc_now(),
            ),
        )


def _collection_exists(db_id: int) -> bool:
    if not _vector_search_available():
        return False

    try:
        client = _get_chroma_client()
        client.get_collection(
            name=_collection_name(db_id),
            embedding_function=_CHROMA_EMBEDDING_FUNCTION,
        )
        return True
    except Exception:
        return False


def _replace_vector_collection(db_id: int, documents: list[RagDocument]) -> None:
    if not _vector_search_available():
        return

    client = _get_chroma_client()
    collection_name = _collection_name(db_id)

    try:
        client.delete_collection(collection_name)
    except Exception:
        logger.debug("No existing Chroma collection to delete", exc_info=True)

    if not documents:
        return

    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=_CHROMA_EMBEDDING_FUNCTION,
        metadata={"source": "sqlgenie", "db_id": db_id},
    )
    collection.add(
        ids=[str(document.table_id) for document in documents],
        documents=[document.retrieval_text for document in documents],
        metadatas=[
            {
                "table_id": document.table_id,
                "table_name": document.table_name,
                "content_hash": document.content_hash,
            }
            for document in documents
        ],
    )


def sync_schema_rag_index(
    connection: sqlite3.Connection,
    *,
    schema_bundle: dict,
    force: bool = False,
) -> list[dict]:
    db_id = int(schema_bundle["db_definition"]["id"])
    existing_rows = _load_index_rows(connection, db_id)

    if force:
        should_sync = True
    elif not existing_rows:
        should_sync = True
    elif _vector_search_available() and not _collection_exists(db_id):
        should_sync = True
    else:
        # Fast check: same table names → assume no change.
        existing_names = {row["table_name"] for row in existing_rows}
        current_names = {table["table_name"] for table in schema_bundle["tables"]}
        if existing_names == current_names:
            return existing_rows
        should_sync = True

    if not should_sync:
        return existing_rows

    documents = _build_documents(schema_bundle)
    _persist_index_rows(connection, db_id, documents)
    try:
        _replace_vector_collection(db_id, documents)
    except Exception:
        logger.warning("Failed to refresh Chroma collection for db_id=%s", db_id, exc_info=True)

    return _load_index_rows(connection, db_id)


def delete_schema_rag_index(connection: sqlite3.Connection, db_id: int) -> None:
    connection.execute("DELETE FROM schema_rag_index WHERE db_id = ?", (db_id,))

    if not _vector_search_available():
        return

    try:
        _get_chroma_client().delete_collection(_collection_name(db_id))
    except Exception:
        logger.debug("Failed to delete Chroma collection for db_id=%s", db_id, exc_info=True)


def sync_sql_feedback_rag_index(connection: sqlite3.Connection, db_id: int) -> None:
    if not _vector_search_available():
        return

    rows = _load_sql_feedback_rows(connection, db_id)
    try:
        client = _get_chroma_client()
        collection_name = _feedback_collection_name(db_id)
        try:
            client.delete_collection(collection_name)
        except Exception:
            logger.debug("No feedback collection to delete for db_id=%s", db_id, exc_info=True)

        if not rows:
            return

        collection = client.get_or_create_collection(
            name=collection_name,
            embedding_function=_CHROMA_EMBEDDING_FUNCTION,
            metadata={"source": "sqlgenie_feedback", "db_id": db_id},
        )
        collection.add(
            ids=[str(row["id"]) for row in rows],
            documents=[_feedback_retrieval_text(row) for row in rows],
            metadatas=[
                {
                    "feedback_id": row["id"],
                    "target_db_type": row["target_db_type"],
                }
                for row in rows
            ],
        )
    except Exception:
        logger.warning("Failed to refresh feedback RAG collection for db_id=%s", db_id, exc_info=True)


def delete_sql_feedback_rag_index(db_id: int) -> None:
    if not _vector_search_available():
        return

    try:
        _get_chroma_client().delete_collection(_feedback_collection_name(db_id))
    except Exception:
        logger.debug("Failed to delete feedback collection for db_id=%s", db_id, exc_info=True)


def _tokenize_query(text: str) -> list[str]:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-zA-Z0-9_]+", lowered))

    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(chunk) <= 2:
            tokens.add(chunk)
            continue

        for size in (2, 3, 4):
            for index in range(0, max(0, len(chunk) - size + 1)):
                tokens.add(chunk[index:index + size])

    return [token for token in tokens if token]


def _keyword_recall(rows: list[dict], question: str, limit: int) -> list[dict]:
    tokens = _tokenize_query(question)
    lowered_question = question.lower()
    ranked: list[tuple[float, dict]] = []

    for row in rows:
        score = 0.0
        table_name = row["table_name"].lower()
        table_comment = (row["table_comment"] or "").lower()
        retrieval_text = row["retrieval_text"].lower()
        ddl_sql = row["ddl_sql"].lower()

        if table_name in lowered_question:
            score += 12
        if table_comment and table_comment in lowered_question:
            score += 7

        for token in tokens:
            if token in table_name:
                score += 5
            if table_comment and token in table_comment:
                score += 4
            if token in retrieval_text:
                score += 2
            if token in ddl_sql:
                score += 1

        if score > 0:
            ranked.append((score, row))

    ranked.sort(key=lambda item: (-item[0], item[1]["table_name"]))
    return [row for _, row in ranked[:limit]]


def _load_sql_feedback_rows(connection: sqlite3.Connection, db_id: int) -> list[dict]:
    cursor = connection.execute(
        """
        SELECT id, natural_text, target_db_type, corrected_sql
        FROM sql_feedback
        WHERE db_id = ? AND approved = 1
        ORDER BY id DESC
        """,
        (db_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _feedback_retrieval_text(row: dict) -> str:
    return f"Question: {row['natural_text']}\nVerified SQL: {row['corrected_sql']}"


def _keyword_feedback_recall(rows: list[dict], question: str, limit: int) -> list[dict]:
    tokens = _tokenize_query(question)
    lowered_question = question.lower()
    ranked: list[tuple[float, dict]] = []

    for row in rows:
        text = _feedback_retrieval_text(row).lower()
        score = 12.0 if row["natural_text"].lower() in lowered_question else 0.0
        score += sum(2 for token in tokens if token in text)
        if score > 0:
            ranked.append((score, row))

    ranked.sort(key=lambda item: (-item[0], -item[1]["id"]))
    return [row for _, row in ranked[:limit]]


def _vector_feedback_recall(
    rows_by_id: dict[int, dict],
    question: str,
    target_db_type: str,
    limit: int,
    db_id: int,
) -> list[dict]:
    if not _vector_search_available():
        return []

    try:
        collection = _get_chroma_client().get_collection(
            name=_feedback_collection_name(db_id),
            embedding_function=_CHROMA_EMBEDDING_FUNCTION,
        )
        result = collection.query(
            query_texts=[question],
            n_results=limit,
            where={"target_db_type": target_db_type},
            include=["metadatas", "distances"],
        )
    except Exception:
        logger.debug("Feedback Chroma query failed for db_id=%s", db_id, exc_info=True)
        return []

    hits: list[dict] = []
    for metadata in result.get("metadatas", [[]])[0]:
        try:
            feedback_id = int(metadata.get("feedback_id"))
        except (TypeError, ValueError):
            continue
        row = rows_by_id.get(feedback_id)
        if row is not None:
            hits.append(row)
    return hits


def retrieve_sql_feedback_context(
    connection: sqlite3.Connection,
    *,
    db_id: int,
    question: str,
    target_db_type: str,
    top_k: int = 3,
) -> dict[str, Any]:
    rows = [
        row
        for row in _load_sql_feedback_rows(connection, db_id)
        if row["target_db_type"] == target_db_type
    ]
    if not rows:
        return {"examples": [], "retrieval_mode": "empty"}

    limit = min(max(top_k, 1), len(rows))
    rows_by_id = {int(row["id"]): row for row in rows}
    vector_hits = _vector_feedback_recall(rows_by_id, question, target_db_type, limit, db_id)
    keyword_hits = _keyword_feedback_recall(rows, question, limit)

    selected: list[dict] = []
    seen_ids: set[int] = set()
    for row in [*vector_hits, *keyword_hits]:
        feedback_id = int(row["id"])
        if feedback_id not in seen_ids:
            selected.append(row)
            seen_ids.add(feedback_id)
        if len(selected) == limit:
            break

    retrieval_mode = "empty"
    if vector_hits and keyword_hits:
        retrieval_mode = "vector+keyword"
    elif vector_hits:
        retrieval_mode = "vector"
    elif keyword_hits:
        retrieval_mode = "keyword"

    return {
        "examples": [
            {"natural_text": row["natural_text"], "corrected_sql": row["corrected_sql"]}
            for row in selected
        ],
        "retrieval_mode": retrieval_mode,
    }


def _append_unique_rows(
    target: list[dict],
    seen_tables: set[str],
    rows: list[dict],
    *,
    retrieval_reason: str,
    limit: int | None = None,
    max_new_rows: int | None = None,
) -> int:
    appended = 0

    for row in rows:
        if limit is not None and len(target) >= limit:
            break
        if max_new_rows is not None and appended >= max_new_rows:
            break

        table_name = row["table_name"]
        if table_name in seen_tables:
            continue

        next_row = dict(row)
        next_row["_retrieval_reason"] = retrieval_reason
        target.append(next_row)
        seen_tables.add(table_name)
        appended += 1

    return appended


def _vector_recall(rows_by_table: dict[str, dict], question: str, limit: int, db_id: int) -> list[dict]:
    if not _vector_search_available():
        return []

    try:
        collection = _get_chroma_client().get_collection(
            name=_collection_name(db_id),
            embedding_function=_CHROMA_EMBEDDING_FUNCTION,
        )
        result = collection.query(
            query_texts=[question],
            n_results=limit,
            include=["metadatas", "distances"],
        )
    except Exception:
        logger.warning("Chroma query failed for db_id=%s", db_id, exc_info=True)
        return []

    hits: list[dict] = []
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]

    for metadata, distance in zip(metadatas, distances):
        table_name = str(metadata.get("table_name", "")).strip()
        row = rows_by_table.get(table_name)
        if row is None:
            continue

        hit = dict(row)
        hit["_retrieval_reason"] = "vector"
        hit["_distance"] = distance
        hits.append(hit)

    return hits


def _parse_foreign_keys_json(raw_value: str) -> list[dict[str, str]]:
    try:
        data = json.loads(raw_value or "[]")
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    return [
        {
            "column_name": str(item.get("column_name", "")),
            "references_table": str(item.get("references_table", "")),
            "references_column": str(item.get("references_column", "")),
        }
        for item in data
        if isinstance(item, dict)
    ]


def _expand_by_foreign_keys(
    rows: list[dict],
    rows_by_table: dict[str, dict],
    depth: int,
    *,
    limit: int | None = None,
) -> list[dict]:
    expanded = [dict(row) for row in rows]
    seen = {row["table_name"] for row in expanded}
    frontier = [dict(row) for row in expanded]

    for _ in range(max(0, depth)):
        next_frontier: list[dict] = []
        for row in frontier:
            if limit is not None and len(expanded) >= limit:
                return expanded[:limit]

            for foreign_key in _parse_foreign_keys_json(row.get("foreign_keys_json", "[]")):
                ref_table = foreign_key.get("references_table", "").strip()
                if not ref_table or ref_table in seen:
                    continue

                matched = rows_by_table.get(ref_table)
                if matched is None:
                    continue

                next_row = dict(matched)
                next_row["_retrieval_reason"] = "fk_expand"
                expanded.append(next_row)
                next_frontier.append(next_row)
                seen.add(ref_table)

                if limit is not None and len(expanded) >= limit:
                    return expanded[:limit]

        if not next_frontier:
            break
        frontier = next_frontier

    return expanded


def _format_retrieved_ddl(row: dict) -> str:
    lines = [f"-- Table: {row['table_name']}"]
    if row.get("table_comment"):
        lines.append(f"-- Comment: {row['table_comment']}")
    lines.append(row["ddl_sql"])
    return "\n".join(lines)


def _infer_operation(question: str) -> str:
    lowered = question.lower()

    if any(token in lowered for token in ("insert", "create", "新增", "插入", "写入")):
        return "INSERT"
    if any(token in lowered for token in ("update", "modify", "set", "更新", "修改")):
        return "UPDATE"
    if any(token in lowered for token in ("delete", "remove", "删除", "移除")):
        return "DELETE"
    return "SELECT"


def retrieve_schema_context(
    connection: sqlite3.Connection,
    *,
    schema_bundle: dict,
    question: str,
    top_k: int | None = None,
) -> dict[str, Any]:
    db_id = int(schema_bundle["db_definition"]["id"])
    rows = sync_schema_rag_index(connection, schema_bundle=schema_bundle)
    if not rows:
        return {
            "operation": _infer_operation(question),
            "retrieval_mode": "empty",
            "retrieved_tables": [],
            "retrieved_tables_ddl": "",
            "vector_enabled": _vector_search_available(),
        }

    limit = min(max(top_k or settings.rag_top_k, 1), len(rows))
    rows_by_table = {row["table_name"]: row for row in rows}

    vector_hits = _vector_recall(rows_by_table, question, limit, db_id)
    keyword_hits = _keyword_recall(rows, question, limit)

    selected: list[dict] = []
    seen_tables: set[str] = set()
    minimum_keyword_hits = min(max(settings.rag_min_keyword_hits, 0), limit)

    _append_unique_rows(
        selected,
        seen_tables,
        vector_hits,
        retrieval_reason="vector",
        limit=limit,
        max_new_rows=max(limit - minimum_keyword_hits, 0),
    )
    _append_unique_rows(
        selected,
        seen_tables,
        keyword_hits,
        retrieval_reason="keyword",
        limit=limit,
        max_new_rows=minimum_keyword_hits,
    )
    _append_unique_rows(
        selected,
        seen_tables,
        vector_hits,
        retrieval_reason="vector",
        limit=limit,
    )
    _append_unique_rows(
        selected,
        seen_tables,
        keyword_hits,
        retrieval_reason="keyword",
        limit=limit,
    )

    if not selected:
        selected = rows[:limit]
        for row in selected:
            row["_retrieval_reason"] = "schema_fallback"

    expanded = _expand_by_foreign_keys(
        selected,
        rows_by_table,
        settings.rag_expand_depth,
        limit=limit,
    )
    retrieved_tables = [row["table_name"] for row in expanded]
    retrieval_mode = "keyword"
    has_vector_hits = any(row.get("_retrieval_reason") == "vector" for row in selected)
    has_keyword_hits = any(row.get("_retrieval_reason") == "keyword" for row in selected)
    if has_vector_hits and has_keyword_hits:
        retrieval_mode = "vector+keyword"
    elif has_vector_hits:
        retrieval_mode = "vector"
    elif selected and selected[0].get("_retrieval_reason") == "schema_fallback":
        retrieval_mode = "schema_fallback"

    return {
        "operation": _infer_operation(question),
        "retrieval_mode": retrieval_mode,
        "retrieved_tables": retrieved_tables,
        "retrieved_tables_ddl": "\n\n".join(_format_retrieved_ddl(row) for row in expanded),
        "vector_enabled": _vector_search_available(),
    }
