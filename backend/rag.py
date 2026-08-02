from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import settings
from .utils import utc_now

logger = logging.getLogger(__name__)
# Bump whenever embedding space or preprocessing changes so existing vectors
# cannot be silently mixed with a new retrieval space.
SCHEMA_INDEX_VERSION = "schema-v4-typed-cosine"
FEEDBACK_INDEX_VERSION = "feedback-v2-typed-cosine"
EMBEDDING_PREPROCESSING_VERSION = "bge-query-instruction-v1"
_SCHEMA_SYNC_LOCKS: dict[int, threading.RLock] = {}
_SCHEMA_SYNC_LOCKS_GUARD = threading.Lock()

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
        self._model_name = ""
        self._model_max_seq_length: int | None = None

    def is_available(self) -> bool:
        return chromadb is not None and SentenceTransformer is not None

    def encode(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        if not self.is_available():
            raise RuntimeError("Local vector dependencies are unavailable.")

        model_name = settings.rag_embedding_model.strip()
        max_seq_length = int(settings.rag_embedding_max_seq_length)
        if (
            self._model is None
            or self._model_name != model_name
            or self._model_max_seq_length != max_seq_length
        ):
            self._model = SentenceTransformer(
                model_name,
                local_files_only=_prefer_local_model_files(),
                trust_remote_code=_model_requires_remote_code(),
            )
            self._model.max_seq_length = min(
                self._model.max_seq_length,
                max_seq_length,
            )
            self._model_name = model_name
            self._model_max_seq_length = max_seq_length

        encoded_inputs = [_prepare_embedding_input(text, kind=kind) for text in texts]
        vectors = self._model.encode(
            encoded_inputs,
            batch_size=settings.rag_embedding_batch_size,
            normalize_embeddings=True,
        )
        return [vector.tolist() for vector in vectors]


class _ChromaEmbeddingFunction:
    def __call__(self, input: list[str]) -> list[list[float]]:
        return _EMBEDDING_RUNTIME.encode(list(input), kind="document")

    def name(self) -> str:
        model_name = settings.rag_embedding_model.strip() or "embedding"
        return re.sub(r"[^a-zA-Z0-9_-]+", "_", model_name)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return _EMBEDDING_RUNTIME.encode(list(input), kind="document")

    def embed_query(self, input: str | list[str]) -> list[list[float]] | list[float]:
        if isinstance(input, str):
            return _EMBEDDING_RUNTIME.encode([input], kind="query")[0]
        return _EMBEDDING_RUNTIME.encode(list(input), kind="query")


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


def ensure_embedding_runtime() -> None:
    if not _vector_search_available():
        raise RuntimeError("Chroma 和 sentence-transformers 依赖不可用，无法初始化 Embedding RAG。")
    _EMBEDDING_RUNTIME.encode(["SQLGenie Embedding RAG initialization preflight"], kind="document")


def _prefer_local_model_files() -> bool:
    model_name = settings.rag_embedding_model.strip()
    return bool(model_name) and (Path(model_name).exists() or Path(model_name).is_absolute())


def _model_requires_remote_code() -> bool:
    model_name = settings.rag_embedding_model.lower()
    return "bge-m3" in model_name or "bge-large-zh" in model_name


def _prepare_embedding_input(text: str, *, kind: str = "document") -> str:
    model_name = settings.rag_embedding_model.lower()
    if kind == "query" and "bge" in model_name:
        return f"Represent this sentence for searching relevant passages: {text}"
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


def _documents_fingerprint(documents: list[RagDocument]) -> str:
    payload = [
        {"table_id": document.table_id, "content_hash": document.content_hash}
        for document in sorted(documents, key=lambda item: item.table_id)
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _feedback_content_hash(row: dict[str, Any]) -> str:
    payload = {
        "id": int(row["id"]),
        "natural_text": str(row["natural_text"]),
        "target_db_type": str(row["target_db_type"]),
        "corrected_sql": str(row["corrected_sql"]),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _feedback_fingerprint(rows: list[dict] | Any) -> str:
    payload = [
        {"feedback_id": int(row["id"]), "content_hash": _feedback_content_hash(row)}
        for row in sorted(rows, key=lambda item: int(item["id"]))
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _collection_metadata(
    *,
    source: str,
    db_id: int,
    index_version: str,
    content_fingerprint: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "db_id": db_id,
        "index_version": index_version,
        "embedding_model": settings.rag_embedding_model.strip(),
        "embedding_max_seq_length": int(settings.rag_embedding_max_seq_length),
        "preprocessing_version": EMBEDDING_PREPROCESSING_VERSION,
        "content_fingerprint": content_fingerprint,
        "hnsw:space": "cosine",
    }


def _schema_rows_fingerprint(rows: list[dict[str, Any]] | Any) -> str:
    payload = [
        {"table_id": int(row["table_id"]), "content_hash": str(row["content_hash"])}
        for row in sorted(rows, key=lambda item: int(item["table_id"]))
    ]
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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
        related_relations = sorted(
            (
                {
                    "from_table": relation["from_table"],
                    "from_column": relation["from_column"],
                    "to_table": relation["to_table"],
                    "to_column": relation["to_column"],
                    "relation_type": relation["relation_type"],
                }
                for relation in relations
                if table["table_name"] in {relation["from_table"], relation["to_table"]}
            ),
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
        content_hash = hashlib.sha256(
            (
                f"{table['table_name']}\n{retrieval_text}\n{ddl_sql}\n"
                + json.dumps(related_relations, ensure_ascii=False, sort_keys=True)
            ).encode("utf-8")
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


def _collection_exists(db_id: int, documents: list[RagDocument]) -> bool:
    if not _vector_search_available():
        return False

    try:
        client = _get_chroma_client()
        collection = client.get_collection(
            name=_collection_name(db_id),
            embedding_function=_CHROMA_EMBEDDING_FUNCTION,
        )
        metadata = collection.metadata or {}
        expected = _collection_metadata(
            source="sqlgenie",
            db_id=db_id,
            index_version=SCHEMA_INDEX_VERSION,
            content_fingerprint=_documents_fingerprint(documents),
        )
        return all(metadata.get(key) == value for key, value in expected.items())
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
        metadata=_collection_metadata(
            source="sqlgenie",
            db_id=db_id,
            index_version=SCHEMA_INDEX_VERSION,
            content_fingerprint=_documents_fingerprint(documents),
        ),
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
    strict: bool = False,
) -> list[dict]:
    db_id = int(schema_bundle["db_definition"]["id"])
    with _SCHEMA_SYNC_LOCKS_GUARD:
        sync_lock = _SCHEMA_SYNC_LOCKS.setdefault(db_id, threading.RLock())

    with sync_lock:
        existing_rows = _load_index_rows(connection, db_id)
        documents = _build_documents(schema_bundle)
        existing_hashes = {
            (int(row["table_id"]), str(row["content_hash"]))
            for row in existing_rows
        }
        current_hashes = {
            (document.table_id, document.content_hash)
            for document in documents
        }
        should_sync = (
            force
            or not existing_rows
            or existing_hashes != current_hashes
            or (_vector_search_available() and not _collection_exists(db_id, documents))
        )
        if not should_sync:
            return existing_rows

        _persist_index_rows(connection, db_id, documents)
        # End SQLite write transaction before embedding/Chroma work.
        connection.commit()
        try:
            _replace_vector_collection(db_id, documents)
        except Exception:
            if strict:
                raise
            logger.warning("Failed to refresh Chroma collection for db_id=%s", db_id, exc_info=True)

        return _load_index_rows(connection, db_id)


def delete_schema_rag_index(
    connection: sqlite3.Connection,
    db_id: int,
    *,
    delete_vector: bool = True,
) -> None:
    connection.execute("DELETE FROM schema_rag_index WHERE db_id = ?", (db_id,))

    if not delete_vector or not _vector_search_available():
        return

    delete_schema_rag_collection(db_id)


def delete_schema_rag_collection(db_id: int) -> None:
    """Delete the external schema vector collection outside SQLite writes."""
    if not _vector_search_available():
        return

    try:
        _get_chroma_client().delete_collection(_collection_name(db_id))
    except Exception:
        logger.debug("Failed to delete Chroma collection for db_id=%s", db_id, exc_info=True)


def sync_sql_feedback_rag_index(
    connection: sqlite3.Connection,
    db_id: int,
    *,
    strict: bool = False,
) -> None:
    rows = _load_sql_feedback_rows(connection, db_id)
    # Approval/update transaction must finish before local embedding work begins.
    connection.commit()
    if not _vector_search_available():
        return

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
            metadata=_collection_metadata(
                source="sqlgenie_feedback",
                db_id=db_id,
                index_version=FEEDBACK_INDEX_VERSION,
                content_fingerprint=_feedback_fingerprint(rows),
            ),
        )
        collection.add(
            ids=[str(row["id"]) for row in rows],
            documents=[_feedback_retrieval_text(row) for row in rows],
            metadatas=[
                {
                    "feedback_id": row["id"],
                    "target_db_type": row["target_db_type"],
                    "content_hash": _feedback_content_hash(row),
                }
                for row in rows
            ],
        )
    except Exception:
        if strict:
            raise
        logger.warning("Failed to refresh feedback RAG collection for db_id=%s", db_id, exc_info=True)


def initialize_database_rag(
    connection: sqlite3.Connection,
    schema_bundle: dict,
) -> dict[str, int]:
    """Rebuild schema and verified SQL vector indexes for one database."""
    from .config import validate_qwen_embedding_model_path

    model_path = validate_qwen_embedding_model_path(settings.rag_embedding_model)
    settings.rag_embedding_model = model_path
    ensure_embedding_runtime()
    db_id = int(schema_bundle["db_definition"]["id"])
    sync_schema_rag_index(connection, schema_bundle=schema_bundle, force=True, strict=True)
    sync_sql_feedback_rag_index(connection, db_id, strict=True)
    return {
        "table_count": len(_load_index_rows(connection, db_id)),
        "feedback_example_count": len(_load_sql_feedback_rows(connection, db_id)),
    }


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
    row_count = max(len(rows), 1)
    searchable_by_table = {
        row["table_name"]: "\n".join(
            (
                row["table_name"].lower(),
                (row["table_comment"] or "").lower(),
                row["retrieval_text"].lower(),
            )
        )
        for row in rows
    }
    token_document_frequency = {
        token: sum(token in searchable_text for searchable_text in searchable_by_table.values())
        for token in tokens
    }

    for row in rows:
        score = 0.0
        table_name = row["table_name"].lower()
        table_comment = (row["table_comment"] or "").lower()
        retrieval_text = row["retrieval_text"].lower()

        if table_name in lowered_question:
            score += 16
        if table_comment and table_comment in lowered_question:
            score += 10

        for token in tokens:
            document_frequency = token_document_frequency[token]
            # Chinese two-character fragments such as "病人" and "记录" are
            # useful only when they are selective.  Otherwise their ubiquitous
            # presence in table definitions overwhelms semantic retrieval.
            if len(token) == 2 and document_frequency > max(1, row_count // 4):
                continue
            if row_count > 1 and document_frequency == row_count:
                continue

            rarity = 1.0 + math.log((row_count + 1) / (document_frequency + 1))
            if token in table_name:
                score += 7 * rarity
            elif table_comment and token in table_comment:
                score += 4 * rarity
            # DDL is intentionally excluded: generic column names, types and
            # FK boilerplate made unrelated tables accrue the same score.
            elif len(token) >= 3 and token in retrieval_text:
                score += 1.5 * rarity

        if score > 0:
            ranked.append((score, row))

    ranked.sort(key=lambda item: (-item[0], item[1]["table_name"]))
    return [dict(row, _keyword_score=score) for score, row in ranked[:limit]]


def _keyword_evidence_score(score: float, leading_score: float) -> float:
    """Convert lexical rank into a bounded, non-probabilistic fusion score."""
    if score <= 0 or leading_score <= 0:
        return 0.0

    # Keep lexical evidence below explicit identifiers (1.0) and preserve
    # rank differences instead of clipping every score above a fixed threshold.
    relative_score = min(max(score / leading_score, 0.0), 1.0)
    return round(0.25 + relative_score * 0.60, 6)


def _evidence_priority(reasons: list[str]) -> int:
    reason_set = set(reasons)
    if "explicit" in reason_set:
        return 3
    if "his_term" in reason_set:
        return 2
    if reason_set & {"keyword", "vector"}:
        return 1
    return 0


def _load_schema_bundle_for_policy(connection: sqlite3.Connection, db_id: int) -> dict | None:
    definition = connection.execute(
        "SELECT id, name, db_type, created_by FROM db_definitions WHERE id = ?",
        (db_id,),
    ).fetchone()
    if definition is None:
        return None
    table_rows = connection.execute(
        "SELECT id, table_name, table_comment FROM table_meta WHERE db_id = ? ORDER BY id",
        (db_id,),
    ).fetchall()
    tables: list[dict] = []
    for table_row in table_rows:
        columns = connection.execute(
            "SELECT id, column_name, data_type, column_comment FROM column_meta WHERE table_id = ? ORDER BY id",
            (table_row["id"],),
        ).fetchall()
        tables.append({**dict(table_row), "columns": [dict(column) for column in columns]})
    relations = connection.execute(
        """
        SELECT r.id, r.from_table_id, ft.table_name AS from_table, r.from_column,
               r.to_table_id, tt.table_name AS to_table, r.to_column, r.relation_type
        FROM table_relation r
        JOIN table_meta ft ON ft.id = r.from_table_id
        JOIN table_meta tt ON tt.id = r.to_table_id
        WHERE r.db_id = ? ORDER BY r.id
        """,
        (db_id,),
    ).fetchall()
    return {
        "db_definition": dict(definition),
        "tables": tables,
        "relations": [dict(row) for row in relations],
    }


def validate_feedback_for_rag(
    connection: sqlite3.Connection,
    row: dict,
    *,
    schema_bundle: dict | None = None,
) -> tuple[bool, list[dict[str, str]]]:
    schema_bundle = schema_bundle or _load_schema_bundle_for_policy(connection, int(row["db_id"]))
    if schema_bundle is None:
        return False, [{"code": "UNKNOWN_DATABASE", "message": "数据库定义不存在。"}]
    expected_dialect = str(schema_bundle["db_definition"]["db_type"])
    if str(row["target_db_type"]) != expected_dialect:
        return False, [{"code": "DIALECT_MISMATCH", "message": "反馈方言与数据库定义不一致。"}]

    try:
        from .intent import analyze_intent
        from .sql_policy import validate_sql
    except ImportError:
        logger.warning("Generation policy modules unavailable; feedback excluded from RAG")
        return False, [{"code": "POLICY_UNAVAILABLE", "message": "本地策略模块不可用。"}]

    from .his_semantics import retrieve_his_semantics

    semantic_context = retrieve_his_semantics(
        connection,
        db_id=int(row["db_id"]),
        question=str(row["natural_text"]),
        schema_bundle=schema_bundle,
    )
    intent = analyze_intent(
        str(row["natural_text"]),
        schema_bundle=schema_bundle,
        his_semantics=semantic_context["terms"],
    )
    if not intent.accepted or intent.requires_clarification:
        code = intent.error_code or "AMBIGUOUS_FEEDBACK_INTENT"
        return False, [{"code": code, "message": intent.clarification_reason or "反馈问题缺少明确查询目标。"}]

    documents = _build_documents(schema_bundle)
    schema_tables = {str(table["table_name"]): table for table in schema_bundle.get("tables", [])}
    evidence_rows = {
        document.table_name: {
            "table_name": document.table_name,
            "table_comment": document.table_comment,
            "retrieval_text": document.retrieval_text,
            "ddl_sql": document.ddl_sql,
            "content_hash": document.content_hash,
            "_schema_columns": schema_tables.get(document.table_name, {}).get("columns", []),
        }
        for document in documents
    }
    explicit_tables, _matched_columns, explicit_errors = _explicit_identifier_evidence(
        evidence_rows,
        str(row["natural_text"]),
        list(intent.explicit_tables),
        list(intent.explicit_columns),
    )
    if explicit_errors:
        return False, explicit_errors

    strong_tables = set(explicit_tables)
    strong_tables.update(
        table_name
        for table_name in semantic_context["table_bindings"]
        if table_name in evidence_rows
    )
    for hit in _keyword_recall(list(evidence_rows.values()), str(row["natural_text"]), len(evidence_rows)):
        if float(hit.get("_keyword_score", 0.0)) >= settings.rag_min_keyword_score:
            strong_tables.add(str(hit["table_name"]))
    if not strong_tables:
        return False, [{
            "code": "LOW_SCHEMA_EVIDENCE",
            "message": "反馈问题没有达到强证据门槛的 Schema 表。",
        }]

    policy = validate_sql(
        str(row["corrected_sql"]),
        dialect=expected_dialect,
        schema_bundle=schema_bundle,
        intent=intent,
        his_semantics=semantic_context["terms"],
        strict_evidence=True,
        strong_evidence_tables=sorted(strong_tables),
    )
    if not policy.passed:
        return False, [
            {"code": issue.code, "message": issue.message}
            for issue in policy.errors
        ]
    return True, []


def _load_sql_feedback_rows(connection: sqlite3.Connection, db_id: int) -> list[dict]:
    cursor = connection.execute(
        """
        SELECT id, db_id, natural_text, target_db_type, corrected_sql
        FROM sql_feedback
        WHERE db_id = ? AND approved = 1
        ORDER BY id DESC
        """,
        (db_id,),
    )
    schema_bundle = _load_schema_bundle_for_policy(connection, db_id)
    valid_rows: list[dict] = []
    for raw_row in cursor.fetchall():
        row = dict(raw_row)
        valid, _ = validate_feedback_for_rag(connection, row, schema_bundle=schema_bundle)
        if valid:
            valid_rows.append(row)
    return valid_rows


def _feedback_retrieval_text(row: dict) -> str:
    return f"Question: {row['natural_text']}\nVerified SQL: {row['corrected_sql']}"


def _keyword_feedback_recall(rows: list[dict], question: str, limit: int) -> list[dict]:
    tokens = _tokenize_query(question)
    lowered_question = question.lower()
    ranked: list[tuple[float, dict]] = []
    natural_texts = [str(row["natural_text"]).lower() for row in rows]
    row_count = max(len(rows), 1)

    for row in rows:
        text = str(row["natural_text"]).lower()
        exact_match = text in lowered_question or lowered_question in text
        score = 16.0 if exact_match else 0.0
        independent_matches = 0
        for token in tokens:
            if token in {"id", "ids", "data", "record", "records"} or token not in text:
                continue
            document_frequency = sum(token in candidate for candidate in natural_texts)
            if row_count > 1 and document_frequency == row_count:
                continue
            rarity = 1.0 + math.log((row_count + 1) / (document_frequency + 1))
            score += rarity
            independent_matches += 1
        if exact_match or independent_matches >= max(2, settings.rag_min_keyword_hits):
            ranked.append((score, dict(row, _keyword_score=round(score, 6))))

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
            n_results=min(max(limit, 2), len(rows_by_id)),
            where={"target_db_type": target_db_type},
            include=["metadatas", "distances"],
        )
    except Exception:
        logger.debug("Feedback Chroma query failed for db_id=%s", db_id, exc_info=True)
        return []

    expected_metadata = _collection_metadata(
        source="sqlgenie_feedback",
        db_id=db_id,
        index_version=FEEDBACK_INDEX_VERSION,
        content_fingerprint=_feedback_fingerprint(list(rows_by_id.values())),
    )
    metadata = collection.metadata or {}
    if not all(metadata.get(key) == value for key, value in expected_metadata.items()):
        logger.warning("Ignoring incompatible feedback vector collection for db_id=%s", db_id)
        return []

    candidates: list[tuple[float, dict]] = []
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    for hit_metadata, distance in zip(metadatas, distances):
        try:
            feedback_id = int(hit_metadata.get("feedback_id"))
        except (TypeError, ValueError):
            continue
        row = rows_by_id.get(feedback_id)
        if row is None or hit_metadata.get("content_hash") != _feedback_content_hash(row):
            continue
        similarity = 1.0 - float(distance)
        candidates.append((similarity, dict(row, _vector_similarity=similarity)))

    if not candidates:
        return []
    candidates.sort(key=lambda item: (-item[0], -int(item[1]["id"])))
    leading_similarity = candidates[0][0]
    runner_up = candidates[1][0] if len(candidates) > 1 else 0.0
    if (
        leading_similarity < settings.rag_min_vector_similarity
        or leading_similarity - runner_up < settings.rag_min_vector_margin
    ):
        return []
    # Feedback admission uses the leading, margin-validated example; the
    # runner-up is queried only to establish confidence and is not surfaced.
    return [candidates[0][1]][:limit]


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

    candidates: dict[int, dict[str, Any]] = {}
    leading_keyword = max((float(row.get("_keyword_score", 0.0)) for row in keyword_hits), default=0.0)
    for row in keyword_hits:
        feedback_id = int(row["id"])
        candidates[feedback_id] = {
            "row": row,
            "quality": float(row.get("_keyword_score", 0.0)) / leading_keyword if leading_keyword else 0.0,
        }
    for row in vector_hits:
        feedback_id = int(row["id"])
        vector_quality = float(row.get("_vector_similarity", 0.0))
        item = candidates.setdefault(feedback_id, {"row": row, "quality": 0.0})
        item["quality"] = max(float(item["quality"]), vector_quality)

    selected = [
        item["row"]
        for _, item in sorted(
            candidates.items(),
            key=lambda pair: (-float(pair[1]["quality"]), -int(pair[0])),
        )[:limit]
    ]

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
        expected_metadata = _collection_metadata(
            source="sqlgenie",
            db_id=db_id,
            index_version=SCHEMA_INDEX_VERSION,
            content_fingerprint=_schema_rows_fingerprint(list(rows_by_table.values())),
        )
        metadata = collection.metadata or {}
        if not all(metadata.get(key) == value for key, value in expected_metadata.items()):
            logger.warning("Ignoring incompatible schema vector collection for db_id=%s", db_id)
            return []
        result = collection.query(
            query_texts=[question],
            n_results=min(max(limit, 2), len(rows_by_table)),
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
        if metadata.get("table_id") is not None and str(metadata["table_id"]) != str(row.get("table_id")):
            continue
        if metadata.get("content_hash") is not None and metadata["content_hash"] != row.get("content_hash"):
            continue

        hit = dict(row)
        hit["_retrieval_reason"] = "vector"
        hit["_distance"] = float(distance)
        hit["_vector_similarity"] = 1.0 - float(distance)
        hits.append(hit)

    hits.sort(key=lambda item: (-float(item.get("_vector_similarity", -1.0)), str(item.get("table_name", "")).casefold()))
    if hits:
        leading = float(hits[0].get("_vector_similarity", -1.0))
        runner_up = float(hits[1].get("_vector_similarity", 0.0)) if len(hits) > 1 else 0.0
        hits[0]["_vector_margin"] = leading - runner_up
    return hits[:limit]


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
    relations: list[dict[str, Any]] | None = None,
) -> list[dict]:
    expanded = [dict(row) for row in rows]
    seen = {row["table_name"] for row in expanded}
    frontier = [dict(row) for row in expanded]

    adjacency: dict[str, list[tuple[str, dict[str, str]]]] = {}
    for relation in relations or []:
        from_table = str(relation.get("from_table", "")).strip()
        to_table = str(relation.get("to_table", "")).strip()
        from_column = str(relation.get("from_column", "")).strip()
        to_column = str(relation.get("to_column", "")).strip()
        if not from_table or not to_table:
            continue
        forward = {
            "from_table": from_table,
            "from_column": from_column,
            "to_table": to_table,
            "to_column": to_column,
            "relation_type": str(relation.get("relation_type", "")),
        }
        reverse = {
            "from_table": to_table,
            "from_column": to_column,
            "to_table": from_table,
            "to_column": from_column,
            "relation_type": str(relation.get("relation_type", "")),
        }
        adjacency.setdefault(from_table, []).append((to_table, forward))
        adjacency.setdefault(to_table, []).append((from_table, reverse))

    # Relations from the schema bundle are authoritative. The legacy fallback
    # keeps direct callers working until they can pass the bundle relation list.
    if relations is None:
        for row in rows_by_table.values():
            table_name = str(row["table_name"])
            for foreign_key in _parse_foreign_keys_json(row.get("foreign_keys_json", "[]")):
                ref_table = foreign_key.get("references_table", "").strip()
                if not ref_table:
                    continue
                forward = {
                    "from_table": table_name,
                    "from_column": foreign_key.get("column_name", ""),
                    "to_table": ref_table,
                    "to_column": foreign_key.get("references_column", ""),
                    "relation_type": "foreign_key",
                }
                reverse = {
                    "from_table": ref_table,
                    "from_column": foreign_key.get("references_column", ""),
                    "to_table": table_name,
                    "to_column": foreign_key.get("column_name", ""),
                    "relation_type": "foreign_key",
                }
                adjacency.setdefault(table_name, []).append((ref_table, forward))
                adjacency.setdefault(ref_table, []).append((table_name, reverse))

    for _ in range(max(0, depth)):
        next_frontier: list[dict] = []
        for row in frontier:
            if limit is not None and len(expanded) >= limit:
                return expanded[:limit]

            for ref_table, relation in sorted(
                adjacency.get(str(row["table_name"]), []),
                key=lambda item: (item[0].casefold(), item[1]["to_column"].casefold()),
            ):
                if ref_table in seen:
                    continue

                matched = rows_by_table.get(ref_table)
                if matched is None:
                    continue

                next_row = dict(matched)
                next_row["_retrieval_reason"] = "fk_expand"
                next_row["_expanded_from"] = row["table_name"]
                next_row["_join_relation"] = relation
                next_row["_join_path"] = [*row.get("_join_path", []), relation]
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


def _explicit_identifier_evidence(
    rows_by_table: dict[str, dict],
    question: str,
    explicit_tables: list[str],
    explicit_columns: list[str],
) -> tuple[set[str], dict[str, list[str]], list[dict[str, str]]]:
    tables_by_key = {name.casefold(): name for name in rows_by_table}
    resolved: set[str] = set()
    matched_columns: dict[str, list[str]] = {}
    errors: list[dict[str, str]] = []

    candidates = {item.strip() for item in explicit_tables if item.strip()}
    qualified = list(re.findall(
        r"(?<![\w$#@/:])((?:[^\W\d]|[$#])[\w$#]*)\s*\.\s*([\w$#*]+)",
        question,
    ))
    candidates.update(table for table, _ in qualified)
    for table_name in rows_by_table:
        escaped = re.escape(table_name)
        if re.search(rf"(?<![\w$#]){escaped}(?![\w$#])", question, flags=re.IGNORECASE):
            candidates.add(table_name)

    for raw_name in candidates:
        canonical = tables_by_key.get(raw_name.casefold())
        if canonical is None:
            errors.append({"code": "UNKNOWN_EXPLICIT_TABLE", "message": f"问题显式引用了未知表：{raw_name}"})
        else:
            resolved.add(canonical)

    # Intent analysis only lists columns that already exist in the current
    # Schema. Validate every qualified identifier here too, including names
    # such as ``orders.bad_column`` that intent cannot recognize.
    for raw_table, raw_column in qualified:
        canonical = tables_by_key.get(raw_table.casefold())
        if canonical is None or raw_column == "*":
            continue
        columns = {
            str(column["column_name"]).casefold()
            for column in rows_by_table[canonical].get("_schema_columns", [])
        }
        if raw_column.casefold() not in columns:
            errors.append({
                "code": "UNKNOWN_EXPLICIT_COLUMN",
                "message": f"问题显式引用了未知字段：{canonical}.{raw_column}",
            })
        else:
            matched_columns.setdefault(canonical, []).append(raw_column)

    for raw_column in explicit_columns:
        if "." not in raw_column:
            continue
        table_name, column_name = (part.strip() for part in raw_column.split(".", 1))
        canonical = tables_by_key.get(table_name.casefold())
        if canonical is None:
            errors.append({"code": "UNKNOWN_EXPLICIT_TABLE", "message": f"问题显式引用了未知表：{table_name}"})
            continue
        columns = {
            str(column["column_name"]).casefold()
            for column in rows_by_table[canonical].get("_schema_columns", [])
        }
        if columns and column_name.casefold() not in columns:
            errors.append({
                "code": "UNKNOWN_EXPLICIT_COLUMN",
                "message": f"问题显式引用了未知字段：{canonical}.{column_name}",
            })
        else:
            resolved.add(canonical)
            if column_name != "*":
                matched_columns.setdefault(canonical, []).append(column_name)
    return (
        resolved,
        {
            table_name: list(dict.fromkeys(columns))
            for table_name, columns in matched_columns.items()
        },
        errors,
    )


def retrieve_schema_context(
    connection: sqlite3.Connection,
    *,
    schema_bundle: dict,
    question: str,
    top_k: int | None = None,
    term_matches: dict[str, list[str]] | None = None,
    term_columns: dict[str, list[str]] | None = None,
    explicit_tables: list[str] | tuple[str, ...] = (),
    explicit_columns: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    db_id = int(schema_bundle["db_definition"]["id"])
    rows = sync_schema_rag_index(connection, schema_bundle=schema_bundle)
    if not rows:
        return {
            "operation": "SELECT",
            "retrieval_mode": "empty",
            "retrieved_tables": [],
            "retrieved_tables_ddl": "",
            "retrieved_evidence": [],
            "strong_evidence_tables": [],
            "has_strong_evidence": False,
            "clarification_reason": "当前数据库定义没有可检索的表结构。",
            "vector_enabled": _vector_search_available(),
        }

    limit = min(max(top_k or settings.rag_top_k, 1), min(len(rows), 20))
    rows_by_table = {row["table_name"]: row for row in rows}

    # Attach current columns for deterministic explicit table.column checks.
    schema_tables = {table["table_name"]: table for table in schema_bundle.get("tables", [])}
    for name, row in rows_by_table.items():
        row["_schema_columns"] = schema_tables.get(name, {}).get("columns", [])

    vector_hits = _vector_recall(rows_by_table, question, limit, db_id)
    keyword_hits = _keyword_recall(rows, question, limit)

    evidence_by_table: dict[str, dict[str, Any]] = {
        name: {
            "table_name": name,
            "reasons": [],
            "keyword_score": 0.0,
            "vector_similarity": None,
            "vector_margin": None,
            "evidence_score": 0.0,
            "matched_terms": [],
            "matched_columns": [],
            "join_path": [],
            "expanded_from": None,
        }
        for name in rows_by_table
    }
    strong_tables: set[str] = set()

    explicit, explicit_columns_by_table, explicit_errors = _explicit_identifier_evidence(
        rows_by_table,
        question,
        list(explicit_tables),
        list(explicit_columns),
    )
    for table_name in explicit:
        item = evidence_by_table[table_name]
        item["reasons"].append("explicit")
        item["evidence_score"] = 1.0
        item["matched_columns"] = explicit_columns_by_table.get(table_name, [])
        strong_tables.add(table_name)

    for table_name, matched_terms in (term_matches or {}).items():
        if table_name not in rows_by_table:
            continue
        item = evidence_by_table[table_name]
        item["reasons"].append("his_term")
        item["matched_terms"] = list(dict.fromkeys(matched_terms))
        item["matched_columns"] = list(dict.fromkeys(
            [*item["matched_columns"], *(term_columns or {}).get(table_name, [])]
        ))
        item["evidence_score"] = max(item["evidence_score"], 0.9)
        strong_tables.add(table_name)

    leading_keyword_score = max(
        (float(hit.get("_keyword_score", 0.0)) for hit in keyword_hits),
        default=0.0,
    )
    for hit in keyword_hits:
        table_name = hit["table_name"]
        score = float(hit.get("_keyword_score", 0.0))
        item = evidence_by_table[table_name]
        item["keyword_score"] = score
        item["reasons"].append("keyword")
        item["evidence_score"] = max(
            item["evidence_score"],
            _keyword_evidence_score(score, leading_keyword_score),
        )
        if score >= settings.rag_min_keyword_score:
            strong_tables.add(table_name)

    similarities = [float(hit.get("_vector_similarity", -1.0)) for hit in vector_hits]
    leading_margin = (
        float(vector_hits[0].get("_vector_margin"))
        if vector_hits and vector_hits[0].get("_vector_margin") is not None
        else (
            similarities[0] - similarities[1]
            if len(similarities) > 1
            else (1.0 if similarities else 0.0)
        )
    )
    for index, hit in enumerate(vector_hits):
        table_name = hit["table_name"]
        similarity = float(hit.get("_vector_similarity", -1.0))
        item = evidence_by_table[table_name]
        item["reasons"].append("vector")
        item["vector_similarity"] = round(similarity, 6)
        item["vector_margin"] = round(leading_margin, 6) if index == 0 else None
        item["evidence_score"] = max(item["evidence_score"], min(max(similarity, 0.0), 1.0))
        if (
            index == 0
            and similarity >= settings.rag_min_vector_similarity
            and leading_margin >= settings.rag_min_vector_margin
        ):
            strong_tables.add(table_name)

    # An unknown identifier is an explicit contradiction, even when another
    # known table also happens to match by keyword or vector similarity.
    # Clear all strong evidence so the caller returns NO_SQL locally instead of
    # sending a mixed, potentially misleading context to the remote model.
    if explicit_errors:
        strong_tables.clear()

    selected_names = sorted(
        strong_tables,
        key=lambda name: (
            -_evidence_priority(evidence_by_table[name]["reasons"]),
            -float(evidence_by_table[name]["evidence_score"]),
            -float(evidence_by_table[name]["keyword_score"]),
            name.casefold(),
        ),
    )[:limit]
    selected = [dict(rows_by_table[name], _retrieval_reason="strong") for name in selected_names]

    expanded = _expand_by_foreign_keys(
        selected,
        rows_by_table,
        settings.rag_expand_depth,
        limit=limit,
        relations=list(schema_bundle.get("relations", [])),
    )
    retrieved_tables = [row["table_name"] for row in expanded]
    for row in expanded:
        if row["table_name"] in strong_tables:
            continue
        source = str(row.get("_expanded_from") or "")
        source_score = float(evidence_by_table.get(source, {}).get("evidence_score", 0.0))
        item = evidence_by_table[row["table_name"]]
        item["reasons"] = ["fk_expand"]
        item["expanded_from"] = source or None
        item["join_path"] = list(row.get("_join_path", []))
        item["evidence_score"] = round(source_score * 0.85, 6)

    retrieval_reasons = {
        reason
        for name in strong_tables
        for reason in evidence_by_table[name]["reasons"]
    }
    retrieval_mode = "+".join(
        reason for reason in ("explicit", "his_term", "keyword", "vector") if reason in retrieval_reasons
    ) or "empty"
    retrieved_evidence = [evidence_by_table[name] for name in retrieved_tables]
    clarification_reason = ""
    if explicit_errors:
        clarification_reason = explicit_errors[0]["message"]
    elif not strong_tables:
        clarification_reason = (
            "未找到足够相关的 Schema 证据；请明确表名、字段名或补充 HIS 术语绑定。"
        )

    return {
        "operation": "SELECT",
        "retrieval_mode": retrieval_mode,
        "retrieved_tables": retrieved_tables,
        "retrieved_tables_ddl": "\n\n".join(_format_retrieved_ddl(row) for row in expanded),
        "retrieved_evidence": retrieved_evidence,
        "strong_evidence_tables": selected_names,
        "has_strong_evidence": bool(strong_tables) and not explicit_errors,
        "clarification_reason": clarification_reason,
        "clarification_errors": explicit_errors,
        "vector_enabled": _vector_search_available(),
    }
