from __future__ import annotations

import mimetypes
import logging
import sqlite3
import uuid
from dataclasses import asdict, is_dataclass, replace
from pathlib import Path
from time import monotonic

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, Response

from .auth import create_access_token, get_current_user, require_admin
from .config import settings, validate_security_configuration
from .config import validate_qwen_embedding_model_path
from .crud import (
    FeedbackValidationError,
    authenticate_user,
    create_his_semantic_term,
    create_generation_trace,
    create_sql_feedback,
    create_sql_history,
    create_user,
    create_db_definition,
    delete_user,
    delete_his_semantic_term,
    delete_db_definition,
    delete_sql_feedback,
    approve_sql_feedback,
    delete_single_table_schema,
    get_db_definition,
    get_feedback_rag_config,
    get_model_config_view,
    get_model_runtime_config,
    get_schema_bundle,
    get_table_schema,
    list_sql_history_for_user,
    list_sql_history,
    list_sql_feedback,
    list_his_semantic_terms,
    list_users,
    list_db_definitions,
    reset_user_password,
    purge_expired_generation_data,
    replace_table_schema,
    update_user_role,
    validate_persisted_admin_password,
    upsert_single_table_schema,
    update_db_definition,
    update_feedback_rag_config,
    update_his_semantic_term,
    update_model_config,
)
from .database import db_session, init_db
from .embedding_model_picker import pick_qwen_embedding_model_path
from .generation import GenerationError, orchestrate_sql_generation
from .his_semantics import retrieve_his_semantics
from .intent import analyze_intent
from .models import AuthenticatedUser
from .rag import (
    initialize_database_rag,
    retrieve_schema_context,
    retrieve_sql_feedback_context,
    sync_sql_feedback_rag_index,
)
from .schemas import (
    ConfigUpdate,
    ConfigView,
    DbDefinitionCreate,
    DbDefinitionDeleteRequest,
    DbDefinitionOut,
    DbDefinitionUpdate,
    FeedbackRagConfigUpdate,
    FeedbackRagConfigView,
    FeedbackRagExamplePageResponse,
    FeedbackRagExampleQuery,
    EmbeddingRagInitializationResponse,
    EmbeddingModelDirectorySelectionResponse,
    GenerateSqlRequest,
    GenerateSqlResponse,
    HisSemanticTermCreate,
    HisSemanticTermOut,
    HisSemanticTermPageResponse,
    HisSemanticTermQuery,
    HisSemanticTermUpdate,
    LoginRequest,
    LoginResponse,
    SqlHistoryPageResponse,
    SqlHistoryQuery,
    SqlHistoryOut,
    SqlFeedbackOut,
    SqlFeedbackSubmit,
    SingleTableDeleteRequest,
    SingleTableUploadRequest,
    TableStructureResponse,
    TableUploadRequest,
    UserCreateRequest,
    UserRoleUpdateRequest,
    UserOut,
    UserPasswordResetRequest,
)

# Windows 上 .js 可能被错误映射成 text/plain，导致前端模块脚本不执行。
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
logger = logging.getLogger(__name__)


def _structured_item(value) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    return {"code": "UNKNOWN", "message": str(value)}


def _persist_trace_safely(trace: dict) -> None:
    try:
        with db_session() as connection:
            purge_expired_generation_data(connection)
            create_generation_trace(connection, trace)
    except Exception:
        logger.exception("Failed to persist generation trace request_id=%s", trace.get("request_id"))


def _apply_no_cache(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


class NoCacheStaticFiles(StaticFiles):
    def file_response(
        self,
        full_path: str | Path,
        stat_result,
        scope,
        status_code: int = 200,
    ) -> Response:
        return _apply_no_cache(FileResponse(full_path, status_code=status_code, stat_result=stat_result))


app = FastAPI(title="sqlGenie API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    validate_security_configuration()
    init_db()
    with db_session() as connection:
        validate_persisted_admin_password(connection)
        purge_expired_generation_data(connection, force=True)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    with db_session() as connection:
        user = authenticate_user(connection, payload.username.strip(), payload.password)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误。",
        )

    access_token = create_access_token(
        {
            "sub": str(user["id"]),
            "username": user["username"],
            "role": user["role"],
            "token_version": str(user["token_version"]),
        }
    )
    return LoginResponse(access_token=access_token, role=user["role"])


@app.get("/api/users", response_model=list[UserOut])
def get_users(current_user: AuthenticatedUser = Depends(require_admin)) -> list[dict]:
    with db_session() as connection:
        return list_users(connection)


@app.post("/api/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user_account(
    payload: UserCreateRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict:
    if payload.role != "user":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only ordinary user accounts can be created through this endpoint.",
        )
    try:
        with db_session() as connection:
            return create_user(connection, payload)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在，请更换后重试。",
        ) from exc


@app.put("/api/users/{user_id}/password", response_model=UserOut)
def reset_password(
    user_id: int,
    payload: UserPasswordResetRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict:
    with db_session() as connection:
        updated = reset_user_password(connection, user_id, payload)

    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="账号不存在，或该账号不允许重置密码。",
        )
    return updated


@app.put("/api/users/{user_id}/role", response_model=UserOut)
def update_user_account_role(
    user_id: int,
    payload: UserRoleUpdateRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict:
    with db_session() as connection:
        updated = update_user_role(connection, user_id, payload.role)

    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="账号不存在，或该账号不允许修改角色。",
        )
    return updated


@app.delete("/api/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_user(
    user_id: int,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> None:
    with db_session() as connection:
        deleted = delete_user(connection, user_id)

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="账号不存在，或该账号不允许删除。",
        )


@app.get("/api/db-defs", response_model=list[DbDefinitionOut])
def get_db_definitions(current_user: AuthenticatedUser = Depends(get_current_user)) -> list[dict]:
    with db_session() as connection:
        return list_db_definitions(connection)


@app.post("/api/db-defs", response_model=DbDefinitionOut, status_code=status.HTTP_201_CREATED)
def create_db_def(
    payload: DbDefinitionCreate,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict:
    try:
        with db_session() as connection:
            return create_db_definition(connection, payload, current_user.id)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="数据库定义名称已存在，请更换后重试。",
        ) from exc


@app.put("/api/db-defs/{db_id}", response_model=DbDefinitionOut)
def update_db_def(
    db_id: int,
    payload: DbDefinitionUpdate,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict:
    try:
        with db_session() as connection:
            updated = update_db_definition(connection, db_id, payload)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="数据库定义名称已存在，请更换后重试。",
        ) from exc

    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="数据库定义不存在。",
        )
    return updated


@app.delete("/api/db-defs/{db_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_db_def(
    db_id: int,
    payload: DbDefinitionDeleteRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> None:
    with db_session() as connection:
        db_definition = get_db_definition(connection, db_id)
        if db_definition is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="数据库定义不存在。",
            )
        if payload.confirm_name.strip() != db_definition["name"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="删除确认失败：请输入完全一致的数据库定义名称。",
            )
        deleted = delete_db_definition(connection, db_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="数据库定义不存在。",
        )


@app.post("/api/db-defs/{db_id}/tables", response_model=TableStructureResponse)
def upload_table_schema(
    db_id: int,
    payload: TableUploadRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict:
    try:
        with db_session() as connection:
            return replace_table_schema(connection, db_id, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@app.get("/api/db-defs/{db_id}/tables", response_model=TableStructureResponse)
def fetch_table_schema(
    db_id: int,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    with db_session() as connection:
        db_definition = get_db_definition(connection, db_id)
        if db_definition is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="数据库定义不存在。",
            )
        return get_table_schema(connection, db_id)


@app.post("/api/db-defs/{db_id}/single-table", response_model=TableStructureResponse)
def upload_single_table_schema(
    db_id: int,
    payload: SingleTableUploadRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict:
    try:
        with db_session() as connection:
            return upsert_single_table_schema(connection, db_id, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@app.delete("/api/db-defs/{db_id}/single-table/{table_name}", response_model=TableStructureResponse)
def remove_single_table_schema(
    db_id: int,
    table_name: str,
    payload: SingleTableDeleteRequest,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict:
    if payload.confirm_name.strip() != table_name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="删除确认失败：请输入完全一致的数据表名称。",
        )

    try:
        with db_session() as connection:
            return delete_single_table_schema(connection, db_id, table_name)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@app.post("/api/generate-sql", response_model=GenerateSqlResponse)
async def generate_sql(
    payload: GenerateSqlRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> GenerateSqlResponse:
    request_id = str(uuid.uuid4())
    request_started_at = monotonic()
    dialect_mismatch = False
    with db_session() as connection:
        schema_bundle = get_schema_bundle(connection, payload.db_id)
        model_config = get_model_runtime_config(connection)

        if schema_bundle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="数据库定义不存在。",
            )
        dialect_mismatch = payload.target_db_type != schema_bundle["db_definition"]["db_type"]
        if not dialect_mismatch:
            semantic_context = retrieve_his_semantics(
                connection,
                db_id=payload.db_id,
                question=payload.natural_text,
                schema_bundle=schema_bundle,
            )
            intent_result = analyze_intent(
                payload.natural_text,
                schema_bundle=schema_bundle,
                his_semantics=semantic_context["terms"],
            )
            rag_context = retrieve_schema_context(
                connection,
                schema_bundle=schema_bundle,
                question=payload.natural_text,
                term_matches=semantic_context["table_bindings"],
                term_columns=semantic_context["table_binding_columns"],
                explicit_tables=intent_result.explicit_tables,
                explicit_columns=intent_result.explicit_columns,
                top_k=int(model_config["rag_top_k"]),
            )
            feedback_context = retrieve_sql_feedback_context(
                connection,
                db_id=payload.db_id,
                question=payload.natural_text,
                target_db_type=payload.target_db_type,
                top_k=int(model_config["feedback_rag_top_k"]),
            )

    if dialect_mismatch:
        _persist_trace_safely(
            {
                "request_id": request_id,
                "user_id": current_user.id,
                "db_id": payload.db_id,
                "prompt_version": "his-sql-v1",
                "policy_version": "sql-policy-v1",
                "model_name": str(model_config["model_name"]),
                "policy_status": "not_run",
                "model_calls": 0,
                "outcome": "error",
                "error_code": "DIALECT_MISMATCH",
            }
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "DIALECT_MISMATCH",
                "message": "目标方言与数据库定义不一致。",
                "request_id": request_id,
            },
        )

    # No SQLite connection or transaction exists during remote model calls.
    try:
        generation_result = await orchestrate_sql_generation(
            model_config,
            target_db_type=payload.target_db_type,
            natural_text=payload.natural_text,
            schema_bundle=schema_bundle,
            schema_evidence=rag_context["retrieved_evidence"],
            his_semantics=semantic_context["terms"],
            verified_examples=feedback_context["examples"],
            intent_result=intent_result,
            request_started_at=request_started_at,
        )
        if (
            generation_result.no_sql_code == "LOW_SCHEMA_EVIDENCE"
            and rag_context.get("clarification_reason")
        ):
            generation_result = replace(
                generation_result,
                reason=str(rag_context["clarification_reason"]),
            )
    except GenerationError as exc:
        _persist_trace_safely(
            {
                "request_id": request_id,
                "user_id": current_user.id,
                "db_id": payload.db_id,
                "prompt_version": exc.prompt_version,
                "policy_version": exc.policy_version,
                "context_hash": exc.context_hash,
                "model_name": str(model_config["model_name"]),
                "retrieval_mode": rag_context["retrieval_mode"],
                "retrieved_tables_json": rag_context["retrieved_evidence"],
                "retrieved_terms_json": semantic_context["retrieved_terms"],
                "policy_status": getattr(exc, "validation_status", "not_run"),
                "validation_errors_json": [
                    _structured_item(item)
                    for item in getattr(exc, "validation_errors", ())
                ],
                "warnings_json": [
                    _structured_item(item)
                    for item in (
                        getattr(exc, "warnings", ())
                        or tuple(_structured_item(item) for item in intent_result.warnings)
                    )
                ],
                "model_calls": exc.model_calls,
                "outcome": "error",
                "error_code": exc.error_code,
                "duration_ms": exc.duration_ms,
                "prompt_chars": exc.prompt_chars,
                "prompt_tokens": exc.prompt_tokens,
                "completion_tokens": exc.completion_tokens,
            }
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.error_code, "message": exc.message, "request_id": request_id},
        ) from exc

    result = generation_result.to_dict()
    sql = generation_result.sql

    with db_session() as connection:
        purge_expired_generation_data(connection)
        history = create_sql_history(
            connection,
            user_id=current_user.id,
            db_id=payload.db_id,
            natural_text=payload.natural_text,
            target_db_type=payload.target_db_type,
            generated_sql=sql,
            retrieved_tables=rag_context["retrieved_tables"],
        )

    _persist_trace_safely(
        {
            "request_id": request_id,
            "history_id": history["id"],
            "user_id": current_user.id,
            "db_id": payload.db_id,
            "prompt_version": generation_result.prompt_version,
            "policy_version": generation_result.policy_version,
            "context_hash": generation_result.context_hash,
            "model_name": str(model_config["model_name"]),
            "retrieval_mode": rag_context["retrieval_mode"],
            "retrieved_tables_json": rag_context["retrieved_evidence"],
            "retrieved_terms_json": semantic_context["retrieved_terms"],
            "policy_status": generation_result.validation_status,
            "validation_errors_json": result["validation_errors"],
            "warnings_json": result["warnings"],
            "model_calls": generation_result.model_calls,
            "outcome": "passed" if generation_result.validation_status == "passed" else "no_sql",
            "error_code": generation_result.no_sql_code or None,
            "duration_ms": generation_result.duration_ms,
            "prompt_chars": generation_result.prompt_chars,
            "prompt_tokens": generation_result.prompt_tokens,
            "completion_tokens": generation_result.completion_tokens,
        }
    )

    return GenerateSqlResponse(
        sql=sql,
        no_sql_reason=generation_result.reason,
        retrieved_tables=rag_context["retrieved_tables"],
        retrieval_mode=rag_context["retrieval_mode"],
        history_id=history["id"],
        request_id=request_id,
        prompt_version=generation_result.prompt_version,
        policy_version=generation_result.policy_version,
        no_sql_code=generation_result.no_sql_code,
        validation_status=generation_result.validation_status,
        validation_errors=result["validation_errors"],
        warnings=result["warnings"],
        assumptions=list(generation_result.assumptions),
        retrieved_evidence=rag_context["retrieved_evidence"],
        retrieved_terms=semantic_context["retrieved_terms"],
        model_calls=generation_result.model_calls,
    )


@app.post("/api/sql-feedback", response_model=SqlFeedbackOut, status_code=status.HTTP_201_CREATED)
def submit_sql_feedback(
    payload: SqlFeedbackSubmit,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    try:
        with db_session() as connection:
            feedback = create_sql_feedback(
                connection,
                history_id=payload.history_id,
                user_id=current_user.id,
                feedback_type=payload.feedback_type,
                corrected_sql=payload.corrected_sql,
                approved=current_user.role == "admin",
            )
            if feedback is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="未找到对应的 SQL 生成记录。",
                )
            if feedback["approved"]:
                sync_sql_feedback_rag_index(connection, feedback["db_id"])
            return feedback
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该 SQL 生成记录已提交过反馈。",
        ) from exc
    except FeedbackValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "FEEDBACK_VALIDATION_FAILED", "issues": exc.issues},
        ) from exc


@app.get("/api/sql-history", response_model=SqlHistoryPageResponse)
def fetch_sql_history(
    query: SqlHistoryQuery = Depends(),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    with db_session() as connection:
        if current_user.role == "admin":
            return list_sql_history(connection, query)
        return list_sql_history_for_user(connection, query, current_user_id=current_user.id)


@app.get("/api/feedback-rag/examples", response_model=FeedbackRagExamplePageResponse)
def fetch_feedback_rag_examples(
    query: FeedbackRagExampleQuery = Depends(),
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict:
    with db_session() as connection:
        return list_sql_feedback(connection, query)


@app.delete("/api/feedback-rag/examples/{feedback_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_feedback_rag_example(
    feedback_id: int,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> None:
    with db_session() as connection:
        db_id = delete_sql_feedback(connection, feedback_id)
        if db_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="未找到反馈示例。")
        sync_sql_feedback_rag_index(connection, db_id)


@app.post("/api/feedback-rag/examples/{feedback_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
def approve_feedback_rag_example(
    feedback_id: int,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> None:
    try:
        with db_session() as connection:
            db_id = approve_sql_feedback(connection, feedback_id)
            if db_id is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback example not found.")
            sync_sql_feedback_rag_index(connection, db_id)
    except FeedbackValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "FEEDBACK_VALIDATION_FAILED", "issues": exc.issues},
        ) from exc


@app.get("/api/his-terms", response_model=HisSemanticTermPageResponse)
def fetch_his_terms(
    query: HisSemanticTermQuery = Depends(),
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict:
    with db_session() as connection:
        return list_his_semantic_terms(connection, query)


@app.post("/api/his-terms", response_model=HisSemanticTermOut, status_code=status.HTTP_201_CREATED)
def create_his_term(
    payload: HisSemanticTermCreate,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict:
    try:
        with db_session() as connection:
            return create_his_semantic_term(connection, payload, current_user.id)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="同一作用域内术语名称已存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.put("/api/his-terms/{term_id}", response_model=HisSemanticTermOut)
def save_his_term(
    term_id: int,
    payload: HisSemanticTermUpdate,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict:
    try:
        with db_session() as connection:
            updated = update_his_semantic_term(connection, term_id, payload)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="同一作用域内术语名称已存在。") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HIS 术语不存在。")
    return updated


@app.delete("/api/his-terms/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_his_term(
    term_id: int,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> None:
    with db_session() as connection:
        deleted = delete_his_semantic_term(connection, term_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HIS 术语不存在。")


@app.get("/api/feedback-rag/config", response_model=FeedbackRagConfigView)
def fetch_feedback_rag_config(
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict[str, int]:
    with db_session() as connection:
        return get_feedback_rag_config(connection)


@app.put("/api/feedback-rag/config", response_model=FeedbackRagConfigView)
def save_feedback_rag_config(
    payload: FeedbackRagConfigUpdate,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict[str, int]:
    with db_session() as connection:
        return update_feedback_rag_config(connection, payload.top_k)


@app.get("/api/config", response_model=ConfigView)
def fetch_config(current_user: AuthenticatedUser = Depends(require_admin)) -> dict[str, str]:
    with db_session() as connection:
        return get_model_config_view(connection)


@app.put("/api/config", response_model=ConfigView)
def save_config(
    payload: ConfigUpdate,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict[str, str]:
    try:
        with db_session() as connection:
            return update_model_config(connection, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_EMBEDDING_MODEL", "message": str(exc)},
        ) from exc


@app.post(
    "/api/embedding-models/pick-directory",
    response_model=EmbeddingModelDirectorySelectionResponse,
)
def pick_embedding_model_directory(
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict[str, str | bool]:
    with db_session() as connection:
        runtime = get_model_runtime_config(connection)
    current_path = str(runtime.get("embedding_model_path") or "")

    try:
        selected_path = pick_qwen_embedding_model_path(current_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_EMBEDDING_MODEL", "message": str(exc)},
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail={"code": "MODEL_DIRECTORY_PICKER_UNAVAILABLE", "message": str(exc)},
        ) from exc

    return {
        "selected": selected_path is not None,
        "embedding_model_path": selected_path or current_path,
        "embedding_model_family": "Qwen",
    }


@app.post(
    "/api/embedding-rag/initialize",
    response_model=EmbeddingRagInitializationResponse,
)
def initialize_embedding_rag(
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict:
    started_at = monotonic()
    with db_session() as connection:
        runtime = get_model_runtime_config(connection)
        try:
            model_path = validate_qwen_embedding_model_path(runtime["embedding_model_path"])
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_EMBEDDING_MODEL", "message": str(exc)},
            ) from exc

        settings.rag_embedding_model = model_path
        database_definitions = list_db_definitions(connection)
        initialized_databases: list[dict] = []
        failed_databases: list[dict] = []
        schema_table_count = 0
        feedback_example_count = 0

        for database in database_definitions:
            result = {
                "db_id": int(database["id"]),
                "name": str(database["name"]),
                "table_count": 0,
                "feedback_example_count": 0,
                "error": "",
            }
            try:
                schema_bundle = get_schema_bundle(connection, int(database["id"]))
                if schema_bundle is None:
                    raise ValueError("数据库定义不存在。")
                stats = initialize_database_rag(connection, schema_bundle)
                result["table_count"] = int(stats["table_count"])
                result["feedback_example_count"] = int(stats["feedback_example_count"])
                schema_table_count += result["table_count"]
                feedback_example_count += result["feedback_example_count"]
                initialized_databases.append(result)
            except Exception as exc:
                logger.exception("Embedding RAG initialization failed for db_id=%s", database["id"])
                result["error"] = str(exc)
                failed_databases.append(result)

    return {
        "embedding_model_path": model_path,
        "embedding_model_family": "Qwen",
        "database_count": len(database_definitions),
        "schema_table_count": schema_table_count,
        "feedback_example_count": feedback_example_count,
        "initialized_databases": initialized_databases,
        "failed_databases": failed_databases,
        "duration_ms": int((monotonic() - started_at) * 1000),
    }


frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _frontend_file_response(path: Path) -> Response:
    return _apply_no_cache(FileResponse(path))


if frontend_dist.exists():
    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", NoCacheStaticFiles(directory=assets_dir), name="assets")

    @app.get("/", include_in_schema=False)
    def serve_frontend_root() -> Response:
        return _frontend_file_response(frontend_dist / "index.html")


    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend_spa(full_path: str) -> Response:
        if full_path.startswith("api/"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="接口不存在。",
            )

        requested_path = frontend_dist / full_path
        if requested_path.exists() and requested_path.is_file():
            return _frontend_file_response(requested_path)
        return _frontend_file_response(frontend_dist / "index.html")
