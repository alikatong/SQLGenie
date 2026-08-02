from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config import settings
from backend.crud import (
    create_db_definition,
    create_his_semantic_term,
    create_user,
    delete_db_definition,
    get_schema_bundle,
    replace_table_schema,
)
from backend.database import db_session, init_db
from backend.his_semantics import normalize_and_validate_bindings, retrieve_his_semantics
from backend.rag import (
    FEEDBACK_INDEX_VERSION,
    _collection_metadata,
    _explicit_identifier_evidence,
    _expand_by_foreign_keys,
    _feedback_content_hash,
    _feedback_fingerprint,
    _keyword_evidence_score,
    _load_index_rows,
    _schema_rows_fingerprint,
    _vector_feedback_recall,
    _vector_recall,
    retrieve_schema_context,
    sync_schema_rag_index,
)
from backend.schemas import (
    ColumnUpload,
    DbDefinitionCreate,
    HisSemanticBinding,
    HisSemanticTermCreate,
    TableUpload,
    TableUploadRequest,
    UserCreateRequest,
)


class RagEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings_patch = patch.object(settings, "db_path", Path(self.temp.name) / "test.db")
        self.settings_patch.start()
        init_db()
        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                user = create_user(connection, UserCreateRequest(username="rag-evidence", password="safe-password"))
                self.user = user
                database = create_db_definition(
                    connection,
                    DbDefinitionCreate(name="rag-evidence-db", db_type="mysql"),
                    user["id"],
                )
                replace_table_schema(
                    connection,
                    database["id"],
                    TableUploadRequest(
                        tables=[
                            TableUpload(
                                table_name="orders",
                                table_comment="订单",
                                columns=[
                                    ColumnUpload(column_name="id", data_type="INT", column_comment="订单编号"),
                                    ColumnUpload(column_name="status", data_type="VARCHAR(20)", column_comment="订单状态"),
                                ],
                            ),
                            TableUpload(
                                table_name="customers",
                                table_comment="客户",
                                columns=[ColumnUpload(column_name="id", data_type="INT", column_comment="客户编号")],
                            ),
                        ]
                    ),
                )
        self.db_id = database["id"]

    def tearDown(self) -> None:
        self.settings_patch.stop()
        self.temp.cleanup()

    def test_metadata_change_refreshes_full_content_hash(self) -> None:
        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                before = {row["table_name"]: row["content_hash"] for row in _load_index_rows(connection, self.db_id)}
                connection.execute(
                    "UPDATE column_meta SET column_comment = '新订单编号' WHERE column_name = 'id' AND table_id = (SELECT id FROM table_meta WHERE db_id = ? AND table_name = 'orders')",
                    (self.db_id,),
                )
                bundle = get_schema_bundle(connection, self.db_id)
                sync_schema_rag_index(connection, schema_bundle=bundle)
                after = {row["table_name"]: row["content_hash"] for row in _load_index_rows(connection, self.db_id)}

        self.assertNotEqual(before["orders"], after["orders"])
        self.assertEqual(before["customers"], after["customers"])

    def test_keyword_threshold_allows_relevant_table_and_never_falls_back(self) -> None:
        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                bundle = get_schema_bundle(connection, self.db_id)
                relevant = retrieve_schema_context(connection, schema_bundle=bundle, question="列出订单")
                irrelevant = retrieve_schema_context(connection, schema_bundle=bundle, question="今天天气如何")

        self.assertTrue(relevant["has_strong_evidence"])
        self.assertIn("orders", relevant["strong_evidence_tables"])
        self.assertFalse(irrelevant["has_strong_evidence"])
        self.assertEqual(irrelevant["retrieved_tables"], [])
        self.assertNotEqual(irrelevant["retrieval_mode"], "schema_fallback")

    def test_keyword_evidence_score_preserves_rank_without_saturation(self) -> None:
        self.assertEqual(_keyword_evidence_score(0.0, 24.0), 0.0)
        self.assertLess(_keyword_evidence_score(12.0, 24.0), _keyword_evidence_score(24.0, 24.0))
        self.assertLess(_keyword_evidence_score(24.0, 24.0), 1.0)

    def test_vector_requires_absolute_similarity_and_leading_margin(self) -> None:
        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                bundle = get_schema_bundle(connection, self.db_id)
                rows = sync_schema_rag_index(connection, schema_bundle=bundle)
                by_name = {row["table_name"]: row for row in rows}
                with patch(
                    "backend.rag._vector_recall",
                    return_value=[
                        dict(by_name["orders"], _vector_similarity=0.70),
                        dict(by_name["customers"], _vector_similarity=0.61),
                    ],
                ), patch("backend.rag._keyword_recall", return_value=[]):
                    accepted = retrieve_schema_context(connection, schema_bundle=bundle, question="通用查询")
                with patch(
                    "backend.rag._vector_recall",
                    return_value=[
                        dict(by_name["orders"], _vector_similarity=0.70),
                        dict(by_name["customers"], _vector_similarity=0.66),
                    ],
                ), patch("backend.rag._keyword_recall", return_value=[]):
                    rejected = retrieve_schema_context(connection, schema_bundle=bundle, question="通用查询")

        self.assertEqual(accepted["strong_evidence_tables"], ["orders"])
        self.assertFalse(rejected["has_strong_evidence"])

    def test_cosine_distance_is_converted_to_similarity(self) -> None:
        class FakeCollection:
            metadata = _collection_metadata(
                source="sqlgenie",
                db_id=self.db_id,
                index_version="schema-v4-typed-cosine",
                content_fingerprint="dummy",
            )

            def query(self, **_kwargs):
                return {"metadatas": [[{"table_name": "orders"}]], "distances": [[0.25]]}

        class FakeClient:
            def get_collection(self, **_kwargs):
                return FakeCollection()

        rows = {"orders": {"table_name": "orders", "table_id": 1, "content_hash": "dummy"}}
        FakeCollection.metadata["content_fingerprint"] = _schema_rows_fingerprint(list(rows.values()))
        with patch("backend.rag._vector_search_available", return_value=True), patch(
            "backend.rag._get_chroma_client", return_value=FakeClient()
        ):
            hits = _vector_recall(rows, "订单", 1, self.db_id)
        self.assertAlmostEqual(hits[0]["_vector_similarity"], 0.75)

    def test_schema_vector_query_keeps_runner_up_for_margin_but_returns_limit(self) -> None:
        class FakeCollection:
            metadata = None

            def query(self, **kwargs):
                self.n_results = kwargs["n_results"]
                return {
                    "metadatas": [[
                        {"table_name": "orders", "table_id": 1, "content_hash": "a"},
                        {"table_name": "customers", "table_id": 2, "content_hash": "b"},
                    ]],
                    "distances": [[0.1, 0.4]],
                }

        rows = {
            "orders": {"table_name": "orders", "table_id": 1, "content_hash": "a"},
            "customers": {"table_name": "customers", "table_id": 2, "content_hash": "b"},
        }
        collection = FakeCollection()
        collection.metadata = _collection_metadata(
            source="sqlgenie", db_id=self.db_id, index_version="schema-v4-typed-cosine",
            content_fingerprint=_schema_rows_fingerprint(list(rows.values())),
        )
        with patch("backend.rag._vector_search_available", return_value=True), patch(
            "backend.rag._get_chroma_client", return_value=type("C", (), {"get_collection": lambda *_a, **_k: collection})()
        ):
            hits = _vector_recall(rows, "orders", 1, self.db_id)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0]["_vector_margin"], 0.3)
        self.assertEqual(collection.n_results, 2)

    def test_unicode_qualified_column_is_validated(self) -> None:
        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                bundle = get_schema_bundle(connection, self.db_id)
                context = retrieve_schema_context(
                    connection, schema_bundle=bundle, question="查询 orders.中文字段", explicit_tables=["orders"]
                )
        self.assertFalse(context["has_strong_evidence"])
        self.assertEqual(context["clarification_errors"][0]["code"], "UNKNOWN_EXPLICIT_COLUMN")

    def test_unicode_qualified_table_and_column_are_validated(self) -> None:
        _resolved, _columns, errors = _explicit_identifier_evidence(
            {"患者": {"table_name": "患者", "_schema_columns": [{"column_name": "姓名"}]}},
            "查询 患者.未知字段",
            ["患者"],
            [],
        )

        self.assertEqual(errors[0]["code"], "UNKNOWN_EXPLICIT_COLUMN")

    def test_bge_query_instruction_is_not_applied_to_documents(self) -> None:
        from backend.rag import _prepare_embedding_input

        with patch.object(settings, "rag_embedding_model", "BAAI/bge-small-zh-v1.5"):
            self.assertEqual(_prepare_embedding_input("schema text", kind="document"), "schema text")
            self.assertIn(
                "Represent this sentence",
                _prepare_embedding_input("question text", kind="query"),
            )

    def test_validated_term_binding_is_strong_evidence(self) -> None:
        with patch("backend.rag._vector_search_available", return_value=False), patch(
            "backend.rag._keyword_recall", return_value=[]
        ):
            with db_session() as connection:
                bundle = get_schema_bundle(connection, self.db_id)
                context = retrieve_schema_context(
                    connection,
                    schema_bundle=bundle,
                    question="统计业务量",
                    term_matches={"orders": ["门诊人次"]},
                )
        self.assertEqual(context["strong_evidence_tables"], ["orders"])
        self.assertEqual(context["retrieved_evidence"][0]["matched_terms"], ["门诊人次"])

    def test_his_term_arrays_and_validated_bindings_drive_retrieval(self) -> None:
        with db_session() as connection:
            bundle = get_schema_bundle(connection, self.db_id)
            created = create_his_semantic_term(
                connection,
                HisSemanticTermCreate(
                    db_id=self.db_id,
                    term="订单量",
                    synonyms=["订单数"],
                    definition="按订单记录计数。",
                    category="metric",
                    bindings=[HisSemanticBinding(table="orders", columns=["id"], role="count")],
                ),
                self.user["id"],
            )
            context = retrieve_his_semantics(
                connection,
                db_id=self.db_id,
                question="统计订单数",
                schema_bundle=bundle,
            )

        self.assertEqual(created["synonyms"], ["订单数"])
        self.assertEqual(created["bindings"][0]["columns"], ["id"])
        self.assertEqual(context["table_bindings"], {"orders": ["订单量"]})

    def test_latin_term_requires_identifier_boundaries(self) -> None:
        with db_session() as connection:
            bundle = get_schema_bundle(connection, self.db_id)
            create_his_semantic_term(
                connection,
                HisSemanticTermCreate(
                    db_id=self.db_id,
                    term="id",
                    synonyms=["order_id"],
                    definition="记录标识字段。",
                    category="entity",
                    bindings=[HisSemanticBinding(table="orders", columns=["id"], role="identifier")],
                ),
                self.user["id"],
            )
            false_match = retrieve_his_semantics(
                connection,
                db_id=self.db_id,
                question="provide order details",
                schema_bundle=bundle,
            )
            direct_match = retrieve_his_semantics(
                connection,
                db_id=self.db_id,
                question="show orders.id and order_id",
                schema_bundle=bundle,
            )

        self.assertEqual(false_match["terms"], [])
        self.assertEqual(false_match["table_bindings"], {})
        self.assertEqual(direct_match["table_bindings"], {"orders": ["id"]})

    def test_unknown_qualified_identifier_clears_other_strong_evidence(self) -> None:
        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                bundle = get_schema_bundle(connection, self.db_id)
                context = retrieve_schema_context(
                    connection,
                    schema_bundle=bundle,
                    question="查询 orders 和 missing_table.id",
                    explicit_tables=["orders"],
                )

        self.assertFalse(context["has_strong_evidence"])
        self.assertEqual(context["retrieved_tables"], [])
        self.assertEqual(context["clarification_errors"][0]["code"], "UNKNOWN_EXPLICIT_TABLE")

    def test_unknown_qualified_column_is_rejected(self) -> None:
        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                bundle = get_schema_bundle(connection, self.db_id)
                context = retrieve_schema_context(
                    connection,
                    schema_bundle=bundle,
                    question="查询 orders.bad_column",
                    explicit_tables=["orders"],
                )

        self.assertFalse(context["has_strong_evidence"])
        self.assertEqual(context["retrieved_tables"], [])
        self.assertEqual(context["clarification_errors"][0]["code"], "UNKNOWN_EXPLICIT_COLUMN")

    def test_explicit_column_is_recorded_without_false_dotted_identifier_matches(self) -> None:
        with patch("backend.rag._vector_search_available", return_value=False):
            with db_session() as connection:
                bundle = get_schema_bundle(connection, self.db_id)
                context = retrieve_schema_context(
                    connection,
                    schema_bundle=bundle,
                    question="show orders.status; ignore 3.14, https://example.com/a.b, user@example.com",
                    explicit_tables=["orders"],
                    explicit_columns=["orders.status"],
                )

        self.assertTrue(context["has_strong_evidence"])
        self.assertEqual(context["clarification_errors"], [])
        evidence = next(item for item in context["retrieved_evidence"] if item["table_name"] == "orders")
        self.assertEqual(evidence["matched_columns"], ["status"])

    def test_foreign_key_expansion_is_bidirectional_and_graph_bound(self) -> None:
        rows = {
            "parent": {"table_name": "parent", "foreign_keys_json": "[]"},
            "child": {"table_name": "child", "foreign_keys_json": "[]"},
            "unrelated": {
                "table_name": "unrelated",
                "foreign_keys_json": '[{"column_name":"id","references_table":"parent","references_column":"id"}]',
            },
        }
        expanded = _expand_by_foreign_keys(
            [dict(rows["parent"])],
            rows,
            depth=1,
            relations=[
                {
                    "from_table": "parent",
                    "from_column": "id",
                    "to_table": "child",
                    "to_column": "parent_id",
                    "relation_type": "one_to_many",
                }
            ],
        )

        self.assertEqual([row["table_name"] for row in expanded], ["parent", "child"])
        self.assertEqual(expanded[1]["_expanded_from"], "parent")
        self.assertEqual(expanded[1]["_join_relation"]["to_table"], "child")

    def test_feedback_vector_recall_rejects_low_similarity(self) -> None:
        rows = {
            1: {"id": 1, "natural_text": "find orders", "target_db_type": "mysql", "corrected_sql": "SELECT id FROM orders"},
            2: {"id": 2, "natural_text": "find customers", "target_db_type": "mysql", "corrected_sql": "SELECT id FROM customers"},
        }

        class FakeCollection:
            metadata = _collection_metadata(
                source="sqlgenie_feedback",
                db_id=self.db_id,
                index_version=FEEDBACK_INDEX_VERSION,
                content_fingerprint=_feedback_fingerprint(list(rows.values())),
            )

            def query(self, **_kwargs):
                return {
                    "metadatas": [[
                        {"feedback_id": 1, "content_hash": _feedback_content_hash(rows[1])},
                        {"feedback_id": 2, "content_hash": _feedback_content_hash(rows[2])},
                    ]],
                    "distances": [[0.20, 0.60]],
                }

        class FakeClient:
            def get_collection(self, **_kwargs):
                return FakeCollection()

        with patch("backend.rag._vector_search_available", return_value=True), patch(
            "backend.rag._get_chroma_client", return_value=FakeClient()
        ):
            hits = _vector_feedback_recall(rows, "find orders", "mysql", 2, self.db_id)

        self.assertEqual([item["id"] for item in hits], [1])

    def test_stale_his_binding_is_ignored_after_schema_change(self) -> None:
        with db_session() as connection:
            bundle = get_schema_bundle(connection, self.db_id)
            create_his_semantic_term(
                connection,
                HisSemanticTermCreate(
                    db_id=self.db_id,
                    term="订单量",
                    synonyms=[],
                    definition="按订单记录计数。",
                    category="metric",
                    bindings=[HisSemanticBinding(table="orders", columns=["id"], role="count")],
                ),
                self.user["id"],
            )
            replace_table_schema(
                connection,
                self.db_id,
                TableUploadRequest(
                    tables=[
                        TableUpload(
                            table_name="orders",
                            table_comment="订单",
                            columns=[ColumnUpload(column_name="order_code", data_type="VARCHAR(20)")],
                        )
                    ]
                ),
            )
            changed_bundle = get_schema_bundle(connection, self.db_id)
            context = retrieve_his_semantics(
                connection,
                db_id=self.db_id,
                question="统计订单量",
                schema_bundle=changed_bundle,
            )

        self.assertEqual(context["table_bindings"], {})
        self.assertEqual(context["terms"][0]["bindings"], [])

    def test_table_level_his_binding_is_preserved_after_normalization(self) -> None:
        with db_session() as connection:
            bundle = get_schema_bundle(connection, self.db_id)
            bindings = normalize_and_validate_bindings(
                db_id=self.db_id,
                bindings=[{"table": "orders", "columns": [], "role": "dimension"}],
                schema_bundle=bundle,
                strict=False,
            )

        self.assertEqual(
            bindings,
            [{"table": "orders", "columns": [], "role": "dimension"}],
        )

    def test_database_delete_commits_before_external_vector_cleanup(self) -> None:
        observed_transaction_states: list[bool] = []

        def observe_cleanup(_db_id: int) -> None:
            observed_transaction_states.append(connection.in_transaction)

        with patch("backend.crud.delete_schema_rag_collection", side_effect=observe_cleanup), patch(
            "backend.crud.delete_sql_feedback_rag_index"
        ):
            with db_session() as connection:
                self.assertTrue(delete_db_definition(connection, self.db_id))

        self.assertEqual(observed_transaction_states, [False])


if __name__ == "__main__":
    unittest.main()
