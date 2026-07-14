from __future__ import annotations

import mimetypes
import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, Response

from .auth import create_access_token, get_current_user, require_admin
from .config import settings, validate_security_configuration
from .crud import (
    authenticate_user,
    create_sql_feedback,
    create_sql_history,
    create_user,
    create_db_definition,
    delete_user,
    delete_db_definition,
    delete_sql_feedback,
    approve_sql_feedback,
    delete_single_table_schema,
    get_db_definition,
    get_feedback_rag_config,
    get_model_config,
    get_schema_bundle,
    get_table_schema,
    list_sql_history_for_user,
    list_sql_history,
    list_sql_feedback,
    list_users,
    list_db_definitions,
    reset_user_password,
    replace_table_schema,
    update_user_role,
    validate_persisted_admin_password,
    upsert_single_table_schema,
    update_db_definition,
    update_feedback_rag_config,
    update_model_config,
)
from .database import db_session, init_db
from .llm import generate_sql_with_llm
from .models import AuthenticatedUser
from .rag import retrieve_schema_context, retrieve_sql_feedback_context, sync_sql_feedback_rag_index
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
    GenerateSqlRequest,
    GenerateSqlResponse,
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
    with db_session() as connection:
        schema_bundle = get_schema_bundle(connection, payload.db_id)
        model_config = get_model_config(connection)

        if schema_bundle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="数据库定义不存在。",
            )
        if not schema_bundle["tables"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="当前数据库定义尚未导入表结构，无法生成 SQL。",
            )

        rag_context = retrieve_schema_context(
            connection,
            schema_bundle=schema_bundle,
            question=payload.natural_text,
        )
        feedback_context = retrieve_sql_feedback_context(
            connection,
            db_id=payload.db_id,
            question=payload.natural_text,
            target_db_type=payload.target_db_type,
            top_k=int(model_config["feedback_rag_top_k"]),
        )

    llm_result = await generate_sql_with_llm(
        model_config=model_config,
        target_db_type=payload.target_db_type,
        natural_text=payload.natural_text,
        retrieved_tables_ddl=rag_context["retrieved_tables_ddl"],
        operation=rag_context["operation"],
        feedback_examples=feedback_context["examples"],
    )
    sql = llm_result["sql"]

    with db_session() as connection:
        history = create_sql_history(
            connection,
            user_id=current_user.id,
            db_id=payload.db_id,
            natural_text=payload.natural_text,
            target_db_type=payload.target_db_type,
            generated_sql=sql,
            retrieved_tables=rag_context["retrieved_tables"],
        )

    return GenerateSqlResponse(
        sql=sql,
        no_sql_reason=llm_result["reason"],
        retrieved_tables=rag_context["retrieved_tables"],
        retrieval_mode=rag_context["retrieval_mode"],
        history_id=history["id"],
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
    with db_session() as connection:
        db_id = approve_sql_feedback(connection, feedback_id)
        if db_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feedback example not found.")
        sync_sql_feedback_rag_index(connection, db_id)


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
        return get_model_config(connection)


@app.put("/api/config", response_model=ConfigView)
def save_config(
    payload: ConfigUpdate,
    current_user: AuthenticatedUser = Depends(require_admin),
) -> dict[str, str]:
    with db_session() as connection:
        return update_model_config(connection, payload)


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
