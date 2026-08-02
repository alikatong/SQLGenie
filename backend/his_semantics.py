from __future__ import annotations

import json
import logging
import re
import sqlite3
import unicodedata
from typing import Any, Iterable, Mapping

from .config import settings

logger = logging.getLogger(__name__)


def _json_list(raw_value: str) -> list[Any]:
    try:
        value = json.loads(raw_value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def row_to_term(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "db_id": int(row["db_id"]) if row["db_id"] is not None else None,
        "term": str(row["term"]),
        "synonyms": [str(item) for item in _json_list(str(row["synonyms_json"]))],
        "definition": str(row["definition"]),
        "category": str(row["category"]),
        "bindings": [item for item in _json_list(str(row["bindings_json"])) if isinstance(item, dict)],
        "sql_hint": str(row["sql_hint"]),
        "enabled": bool(row["enabled"]),
        "created_by": int(row["created_by"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _normalize_match_text(value: Any) -> str:
    """Normalize user and catalog text without changing stored display values."""
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def normalize_and_validate_bindings(
    *,
    db_id: int | None,
    bindings: Iterable[Mapping[str, Any]],
    schema_bundle: Mapping[str, Any] | None,
    strict: bool = True,
    warnings: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    binding_items = list(bindings)
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...], str]] = set()

    warning_items = warnings if warnings is not None else []

    def warn(code: str, message: str) -> None:
        warning_items.append({"code": code, "message": message})

    if db_id is None:
        if binding_items:
            if strict:
                raise ValueError("全局 HIS 术语不能包含 Schema 绑定。")
            warn("GLOBAL_BINDING_IGNORED", "Global HIS terms cannot contain Schema bindings.")
        return normalized

    if schema_bundle is None or int(schema_bundle["db_definition"]["id"]) != db_id:
        if strict:
            raise ValueError("术语绑定对应的数据库定义不存在。")
        warn("STALE_DATABASE_BINDING", "HIS binding refers to a missing database definition.")
        return normalized

    tables: dict[str, dict[str, Any]] = {
        str(table["table_name"]).casefold(): dict(table)
        for table in schema_bundle.get("tables", [])
    }
    for raw_binding in binding_items:
        table_name = str(raw_binding.get("table", "")).strip()
        table = tables.get(table_name.casefold())
        if table is None:
            if strict:
                raise ValueError(f"术语绑定引用了未知表：{table_name}")
            warn("STALE_TABLE_BINDING", f"HIS binding references an unknown table: {table_name}")
            continue

        canonical_table = str(table["table_name"])
        columns_by_key = {
            str(column["column_name"]).casefold(): str(column["column_name"])
            for column in table.get("columns", [])
        }
        raw_columns = list(raw_binding.get("columns", []))
        canonical_columns: list[str] = []
        for raw_column in raw_columns:
            column_name = str(raw_column).strip()
            canonical_column = columns_by_key.get(column_name.casefold())
            if canonical_column is None:
                if strict:
                    raise ValueError(f"术语绑定引用了未知字段：{canonical_table}.{column_name}")
                warn(
                    "STALE_COLUMN_BINDING",
                    f"HIS binding references an unknown column: {canonical_table}.{column_name}",
                )
                continue
            if canonical_column not in canonical_columns:
                canonical_columns.append(canonical_column)

        if not strict and raw_columns and not canonical_columns:
            continue

        role = str(raw_binding.get("role") or "").strip()
        key = (canonical_table.casefold(), tuple(item.casefold() for item in canonical_columns), role.casefold())
        if key in seen:
            continue
        seen.add(key)
        item: dict[str, Any] = {"table": canonical_table, "columns": canonical_columns}
        if role:
            item["role"] = role
        normalized.append(item)

    return normalized


def _query_ngrams(text: str) -> set[str]:
    normalized = _normalize_match_text(text)
    ngrams: set[str] = set(re.findall(r"[a-zA-Z0-9_]+", normalized))
    for chunk in re.findall(r"[\u4e00-\u9fff]+", normalized):
        for size in (2, 3, 4):
            ngrams.update(
                chunk[index:index + size]
                for index in range(max(0, len(chunk) - size + 1))
            )
    return {item for item in ngrams if item}


def _semantic_name_mentioned(text: str, name: str) -> bool:
    normalized_text = _normalize_match_text(text)
    normalized_name = _normalize_match_text(name)
    if not normalized_name:
        return False
    if re.search(r"[a-zA-Z0-9_]", normalized_name):
        escaped = re.escape(normalized_name)
        return re.search(
            rf"(?<![A-Za-z0-9_$#]){escaped}(?![A-Za-z0-9_$#])",
            normalized_text,
            re.I,
        ) is not None
    return normalized_name in normalized_text


def retrieve_his_semantics(
    connection: sqlite3.Connection,
    *,
    db_id: int,
    question: str,
    schema_bundle: Mapping[str, Any],
    top_k: int | None = None,
) -> dict[str, Any]:
    cursor = connection.execute(
        """
        SELECT *
        FROM his_semantic_term
        WHERE enabled = 1 AND (db_id = ? OR db_id IS NULL)
        ORDER BY CASE WHEN db_id = ? THEN 0 ELSE 1 END, id DESC
        """,
        (db_id, db_id),
    )
    normalized_question = _normalize_match_text(question)
    query_ngrams = _query_ngrams(normalized_question)
    ranked: list[tuple[float, int, dict[str, Any]]] = []
    retrieval_warnings: list[dict[str, str]] = []

    for row in cursor.fetchall():
        term = row_to_term(row)
        score = 0.0
        binding_evidence = False
        term_name = _normalize_match_text(term["term"])
        exact_match = False
        if term_name and _semantic_name_mentioned(normalized_question, term_name):
            score = max(score, 12.0)
            exact_match = True
        for synonym in term["synonyms"]:
            synonym_key = _normalize_match_text(synonym)
            if synonym_key and _semantic_name_mentioned(normalized_question, synonym_key):
                score = max(score, 10.0)
                exact_match = True

        term_ngrams = _query_ngrams(term["term"])
        overlap = len(query_ngrams & term_ngrams)
        if overlap:
            score += min(overlap * 1.5, 4.0)
        definition_tokens = _query_ngrams(term["definition"])
        definition_overlap = len(query_ngrams & definition_tokens)
        if definition_overlap:
            score += min(definition_overlap * 0.25, 2.0)
        if score <= 0:
            continue

        binding_warnings: list[dict[str, str]] = []
        try:
            valid_bindings = normalize_and_validate_bindings(
                db_id=term["db_id"],
                bindings=term["bindings"],
                schema_bundle=schema_bundle if term["db_id"] is not None else None,
                strict=False,
                warnings=binding_warnings,
            )
        except ValueError:
            # A term can outlive a table/column rename. Keep its textual
            # definition available, but never let stale bindings become
            # strong Schema evidence or turn generation into a 500 response.
            logger.warning(
                "Ignoring stale HIS semantic bindings for term_id=%s db_id=%s",
                term["id"],
                term["db_id"],
            )
            valid_bindings = []
        if binding_warnings:
            term["warnings"] = binding_warnings
            retrieval_warnings.extend(
                {"term": term["term"], **warning}
                for warning in binding_warnings
            )
        term["bindings"] = valid_bindings
        term["score"] = round(score, 4)
        term["scope"] = "database" if term["db_id"] is not None else "global"
        # Only a complete term/synonym match is allowed to create a binding.
        # N-gram and definition overlap remain ranking-only evidence.
        term["_binding_evidence"] = exact_match
        term["match_kind"] = "exact" if exact_match else "fuzzy"
        ranked.append((score, 1 if term["db_id"] is None else 0, term))

    # Database-specific terms are preferred over global terms at every score;
    # this prevents a generic global definition from masking local semantics.
    ranked.sort(key=lambda item: (item[1], -item[0], _normalize_match_text(item[2]["term"])))
    limit = min(max(top_k or settings.his_term_top_k, 1), 20)
    selected = [item[2] for item in ranked[:limit]]

    table_bindings: dict[str, list[str]] = {}
    table_binding_columns: dict[str, list[str]] = {}
    for term in selected:
        if term.pop("_binding_evidence", False):
            for binding in term["bindings"]:
                table_bindings.setdefault(binding["table"], []).append(term["term"])
                columns = table_binding_columns.setdefault(binding["table"], [])
                for column in binding.get("columns", []):
                    if column not in columns:
                        columns.append(column)

    return {
        "terms": selected,
        "retrieved_terms": [
            {
                "id": item["id"],
                "term": item["term"],
                "category": item["category"],
                "scope": item["scope"],
                "score": item["score"],
            }
            for item in selected
        ],
        "table_bindings": table_bindings,
        "table_binding_columns": table_binding_columns,
        "warnings": retrieval_warnings,
    }
