from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=128)


class LoginResponse(BaseModel):
    access_token: str
    role: Literal["admin", "user"]


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=72)
    role: Literal["admin", "user"] = "user"


class UserOut(BaseModel):
    id: int
    username: str
    role: Literal["admin", "user"]
    created_at: str


class UserPasswordResetRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=72)


class UserRoleUpdateRequest(BaseModel):
    role: Literal["user"]


class DbDefinitionBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    db_type: Literal["mysql", "pg", "oracle"]


class DbDefinitionCreate(DbDefinitionBase):
    pass


class DbDefinitionUpdate(DbDefinitionBase):
    pass


class DbDefinitionOut(DbDefinitionBase):
    id: int
    created_by: int


class DbDefinitionDeleteRequest(BaseModel):
    confirm_name: str = Field(..., min_length=1, max_length=100)
    confirm_phrase: Literal["DELETE"]


class ColumnUpload(BaseModel):
    column_name: str = Field(..., min_length=1, max_length=100)
    data_type: str = Field(..., min_length=1, max_length=100)
    column_comment: str = Field(default="", max_length=2000)


class TableUpload(BaseModel):
    table_name: str = Field(..., min_length=1, max_length=100)
    table_comment: str = Field(default="", max_length=2000)
    columns: list[ColumnUpload] = Field(default_factory=list, max_length=200)


class RelationUpload(BaseModel):
    from_table: str = Field(..., min_length=1, max_length=100)
    from_column: str = Field(..., min_length=1, max_length=100)
    to_table: str = Field(..., min_length=1, max_length=100)
    to_column: str = Field(..., min_length=1, max_length=100)
    relation_type: str = Field(default="one_to_many", min_length=1, max_length=50)


class TableUploadRequest(BaseModel):
    tables: list[TableUpload] = Field(default_factory=list, max_length=100)
    relations: list[RelationUpload] = Field(default_factory=list, max_length=500)


class SingleTableUploadRequest(BaseModel):
    table: TableUpload
    relations: list[RelationUpload] = Field(default_factory=list, max_length=500)


class SingleTableDeleteRequest(BaseModel):
    confirm_name: str = Field(..., min_length=1, max_length=100)


class ColumnMetaOut(BaseModel):
    id: int
    column_name: str
    data_type: str
    column_comment: str


class TableMetaOut(BaseModel):
    id: int
    table_name: str
    table_comment: str
    columns: list[ColumnMetaOut] = Field(default_factory=list)


class TableRelationOut(BaseModel):
    id: int
    from_table_id: int
    from_table: str
    from_column: str
    to_table_id: int
    to_table: str
    to_column: str
    relation_type: str


class TableStructureResponse(BaseModel):
    db_id: int
    tables: list[TableMetaOut] = Field(default_factory=list)
    relations: list[TableRelationOut] = Field(default_factory=list)


class GenerateSqlRequest(BaseModel):
    db_id: int = Field(..., gt=0)
    natural_text: str = Field(..., min_length=1, max_length=4000)
    target_db_type: Literal["mysql", "pg", "oracle"]


class GenerateSqlResponse(BaseModel):
    sql: str
    no_sql_reason: str = ""
    retrieved_tables: list[str] = Field(default_factory=list)
    retrieval_mode: str = "unknown"
    history_id: int
    request_id: str = ""
    prompt_version: str = ""
    policy_version: str = ""
    no_sql_code: str = ""
    validation_status: Literal["passed", "failed", "not_run"] = "not_run"
    validation_errors: list["ValidationIssue"] = Field(default_factory=list)
    warnings: list["ValidationIssue"] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    retrieved_evidence: list["RetrievalEvidence"] = Field(default_factory=list)
    retrieved_terms: list["RetrievedTerm"] = Field(default_factory=list)
    model_calls: int = Field(default=0, ge=0, le=2)


class ValidationIssue(BaseModel):
    code: str
    message: str


class RetrievalEvidence(BaseModel):
    table_name: str
    reasons: list[str] = Field(default_factory=list)
    keyword_score: float = 0.0
    vector_similarity: float | None = None
    vector_margin: float | None = None
    evidence_score: float = 0.0
    matched_columns: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    join_path: list[dict[str, str]] = Field(default_factory=list)
    expanded_from: str | None = None


class RetrievedTerm(BaseModel):
    id: int
    term: str
    category: str
    scope: str
    score: float


class HisSemanticBinding(BaseModel):
    table: str = Field(..., min_length=1, max_length=100)
    columns: list[str] = Field(default_factory=list, max_length=50)
    role: str | None = Field(default=None, max_length=100)


class HisSemanticTermBase(BaseModel):
    db_id: int | None = Field(default=None, gt=0)
    term: str = Field(..., min_length=1, max_length=100)
    synonyms: list[str] = Field(default_factory=list, max_length=20)
    definition: str = Field(..., min_length=1, max_length=2000)
    category: Literal["entity", "event", "time", "status", "metric", "relation"]
    bindings: list[HisSemanticBinding] = Field(default_factory=list, max_length=20)
    sql_hint: str = Field(default="", max_length=2000)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_synonyms(self) -> "HisSemanticTermBase":
        normalized: set[str] = set()
        for synonym in self.synonyms:
            value = synonym.strip()
            if not value or len(value) > 100:
                raise ValueError("同义词必须为 1-100 个字符。")
            key = value.casefold()
            if key in normalized:
                raise ValueError("同义词不能重复。")
            normalized.add(key)
        return self


class HisSemanticTermCreate(HisSemanticTermBase):
    pass


class HisSemanticTermUpdate(HisSemanticTermBase):
    pass


class HisSemanticTermOut(HisSemanticTermBase):
    id: int
    created_by: int
    created_at: str
    updated_at: str


class HisSemanticTermQuery(BaseModel):
    db_id: int | None = Field(default=None, gt=0)
    enabled: bool | None = None
    category: Literal["entity", "event", "time", "status", "metric", "relation"] | None = None
    search: str = Field(default="", max_length=100)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class HisSemanticTermPageResponse(BaseModel):
    items: list[HisSemanticTermOut] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class SqlFeedbackSubmit(BaseModel):
    history_id: int = Field(..., gt=0)
    feedback_type: Literal["correct", "modified"]
    corrected_sql: str | None = Field(default=None, max_length=20000)

    @model_validator(mode="after")
    def validate_correction(self) -> "SqlFeedbackSubmit":
        if self.feedback_type == "modified" and not (self.corrected_sql or "").strip():
            raise ValueError("反馈类型为修改时，必须填写修正后的 SQL。")
        return self


class SqlFeedbackOut(BaseModel):
    id: int
    history_id: int | None
    db_id: int
    feedback_type: Literal["correct", "modified"]
    generated_sql: str
    corrected_sql: str
    approved: bool
    created_at: str


class FeedbackRagConfigView(BaseModel):
    top_k: int


class FeedbackRagConfigUpdate(BaseModel):
    top_k: int = Field(..., ge=1, le=20)


class FeedbackRagExampleOut(SqlFeedbackOut):
    username: str
    db_name: str
    natural_text: str
    target_db_type: Literal["mysql", "pg", "oracle"]


class FeedbackRagExampleQuery(BaseModel):
    db_id: int | None = Field(default=None, gt=0)
    approved: bool | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=50)


class FeedbackRagExamplePageResponse(BaseModel):
    items: list[FeedbackRagExampleOut] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class SqlHistoryOut(BaseModel):
    id: int
    user_id: int
    username: str
    db_id: int
    db_name: str
    target_db_type: Literal["mysql", "pg", "oracle"]
    natural_text: str
    generated_sql: str
    retrieved_tables_json: str = "[]"
    created_at: str


class SqlHistoryQuery(BaseModel):
    user_id: int | None = Field(default=None, gt=0)
    date_from: str | None = None
    date_to: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=20)

    @model_validator(mode="after")
    def validate_date_range(self) -> "SqlHistoryQuery":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from 不能晚于 date_to。")
        return self


class SqlHistoryPageResponse(BaseModel):
    items: list[SqlHistoryOut] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class ConfigView(BaseModel):
    api_key_configured: bool = False
    api_key_last4: str = ""
    base_url: str = ""
    model_name: str = ""
    enable_thinking: bool = True
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    thinking_timeout_seconds: int = 600
    prompt_max_chars: int = 60_000
    rag_embedding_model: str = ""
    embedding_model_path: str = ""
    embedding_model_family: Literal["Qwen"] = "Qwen"
    rag_top_k: int = 8
    rag_expand_depth: int = 1


class ConfigUpdate(BaseModel):
    api_key: str | None = Field(default=None, max_length=10000)
    base_url: str = Field(..., min_length=1)
    model_name: str = Field(..., min_length=1)
    enable_thinking: bool = True
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    thinking_timeout_seconds: int = Field(default=600, ge=10, le=600)
    prompt_max_chars: int = Field(default=60_000, ge=1_000, le=120_000)
    rag_top_k: int = Field(default=8, ge=1, le=20)
    embedding_model_path: str | None = Field(default=None, max_length=2000)


class EmbeddingRagDatabaseResult(BaseModel):
    db_id: int
    name: str
    table_count: int = Field(default=0, ge=0)
    feedback_example_count: int = Field(default=0, ge=0)
    error: str = ""


class EmbeddingRagInitializationResponse(BaseModel):
    embedding_model_path: str
    embedding_model_family: Literal["Qwen"] = "Qwen"
    database_count: int = Field(default=0, ge=0)
    schema_table_count: int = Field(default=0, ge=0)
    feedback_example_count: int = Field(default=0, ge=0)
    initialized_databases: list[EmbeddingRagDatabaseResult] = Field(default_factory=list)
    failed_databases: list[EmbeddingRagDatabaseResult] = Field(default_factory=list)
    duration_ms: int = Field(default=0, ge=0)


GenerateSqlResponse.model_rebuild()
