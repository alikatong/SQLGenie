from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

import sqlglot
from sqlglot import Dialect, exp
from sqlglot.errors import OptimizeError, ParseError, TokenError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema
from sqlglot.tokens import TokenType, Tokenizer


POLICY_VERSION = "sql-policy-v1"

_DIALECTS = {"mysql": "mysql", "pg": "postgres", "postgres": "postgres", "oracle": "oracle"}

_SIDE_EFFECT_TYPES = tuple(
    expression_type
    for name in (
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Alter",
        "Drop",
        "TruncateTable",
        "Copy",
        "LoadData",
        "Transaction",
        "Commit",
        "Rollback",
        "Command",
        "Execute",
        "Grant",
        "Revoke",
        "Use",
        "Set",
        "Kill",
        "Cache",
        "Uncache",
        "Pragma",
    )
    if (expression_type := getattr(exp, name, None)) is not None
)

_DANGEROUS_FUNCTIONS = {
    "DBMS_PIPE",
    "DBMS_PIPE.RECEIVE_MESSAGE",
    "LOAD_FILE",
    "LO_IMPORT",
    "PG_READ_FILE",
    "PG_READ_BINARY_FILE",
    "PG_TERMINATE_BACKEND",
    "DBLINK",
    "DBLINK_CONNECT",
    "DBLINK_EXEC",
    "UTL_HTTP",
    "UTL_HTTP.REQUEST",
    "UTL_HTTP.BEGIN_REQUEST",
    "UTL_FILE",
    "XP_CMDSHELL",
    "NEXTVAL",
    "SETVAL",
    "PG_ADVISORY_LOCK",
    "PG_TRY_ADVISORY_LOCK",
    "GET_LOCK",
    "RELEASE_LOCK",
    "SLEEP",
    "BENCHMARK",
    "PG_SLEEP",
    "SYS_EXEC",
    "NEXT_VALUE_FOR",
}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SqlValidationResult:
    status: str
    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    validated_sql: str
    policy_version: str = POLICY_VERSION

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
            "tables": list(self.tables),
            "columns": list(self.columns),
            "validated_sql": self.validated_sql,
            "policy_version": self.policy_version,
        }


def _failure(
    code: str,
    message: str,
    *,
    warnings: Iterable[ValidationIssue] = (),
    tables: Iterable[str] = (),
    columns: Iterable[str] = (),
) -> SqlValidationResult:
    return SqlValidationResult(
        status="failed",
        errors=(ValidationIssue(code, message),),
        warnings=tuple(warnings),
        tables=tuple(dict.fromkeys(tables)),
        columns=tuple(dict.fromkeys(columns)),
        validated_sql="",
    )


def _schema_map(schema_bundle: Mapping[str, Any]) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, Any]]]:
    mapping: dict[str, dict[str, str]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for table in schema_bundle.get("tables", ()) or ():
        if not isinstance(table, Mapping):
            continue
        table_name = str(table.get("table_name", "")).strip()
        if not table_name:
            continue
        columns: dict[str, str] = {}
        raw_columns: list[dict[str, Any]] = []
        for column in table.get("columns", ()) or ():
            if not isinstance(column, Mapping):
                continue
            column_name = str(column.get("column_name", "")).strip()
            if column_name:
                columns[column_name] = "UNKNOWN"
                raw_columns.append(dict(column))
        mapping[table_name] = columns
        metadata[table_name] = {"table": dict(table), "columns": raw_columns}
    return mapping, metadata


def _match_identifier(identifier: exp.Identifier | None, candidates: Iterable[str]) -> str | None:
    if identifier is None:
        return None
    value = identifier.name
    values = list(candidates)
    if bool(identifier.args.get("quoted")):
        return value if value in values else None
    folded = value.casefold()
    return next((candidate for candidate in values if candidate.casefold() == folded), None)


def _table_identifier(table: exp.Table) -> exp.Identifier | None:
    return table.this if isinstance(table.this, exp.Identifier) else None


def _physical_sources(root: exp.Expression) -> list[tuple[Scope, str, exp.Table]]:
    sources: list[tuple[Scope, str, exp.Table]] = []
    for scope in traverse_scope(root):
        for alias, source in scope.sources.items():
            if isinstance(source, exp.Table):
                sources.append((scope, alias, source))
    return sources


def _source_for_alias(scope: Scope, alias: str) -> exp.Table | Scope | None:
    current: Scope | None = scope
    folded = alias.casefold()
    while current is not None:
        for source_alias, source in current.sources.items():
            if source_alias.casefold() == folded:
                return source
        current = current.parent
    return None


def _has_real_comments(parsed: Iterable[exp.Expression | None]) -> bool:
    return any(node.comments for root in parsed if root is not None for node in root.walk())


def _has_mysql_file_output(sql: str) -> bool:
    try:
        tokens = Tokenizer(dialect="mysql").tokenize(sql)
    except (TokenError, ValueError):
        return False
    for index, token in enumerate(tokens[:-1]):
        if token.token_type == TokenType.INTO and tokens[index + 1].text.upper() in {"OUTFILE", "DUMPFILE"}:
            return True
    return False


def _function_names(node: exp.Expression) -> set[str]:
    names: set[str] = set()
    next_value_for_type = getattr(exp, "NextValueFor", None)
    if next_value_for_type is not None and isinstance(node, next_value_for_type):
        names.add("NEXT_VALUE_FOR")
    if isinstance(node, exp.Anonymous):
        names.add(node.name.upper())
    elif isinstance(node, exp.Func):
        try:
            names.add(node.sql_name().upper())
        except (AttributeError, TypeError):
            if node.name:
                names.add(node.name.upper())
    if isinstance(node, exp.Dot):
        left = node.this
        right = node.expression
        left_name = left.name if isinstance(left, (exp.Identifier, exp.Column)) else ""
        right_name = right.name if isinstance(right, (exp.Identifier, exp.Func)) else ""
        if left_name and right_name:
            names.add(f"{left_name}.{right_name}".upper())
            names.add(left_name.upper())
    if isinstance(node, exp.Column) and node.name.upper() in {"NEXTVAL", "CURRVAL"}:
        names.add(node.name.upper())
    return names


def _side_effect_issue(root: exp.Expression) -> ValidationIssue | None:
    for node in root.walk():
        if isinstance(node, _SIDE_EFFECT_TYPES):
            return ValidationIssue("SIDE_EFFECT_STATEMENT", f"禁止包含 {type(node).__name__} 节点。")
        if isinstance(node, exp.Into):
            return ValidationIssue("SELECT_INTO", "禁止 SELECT INTO 或文件输出。")
        if isinstance(node, exp.Lock):
            return ValidationIssue("LOCKING_QUERY", "禁止 FOR UPDATE/SHARE 等锁查询。")
        if isinstance(node, exp.PropertyEQ) and isinstance(node.this, (exp.Parameter, exp.Var)):
            return ValidationIssue("VARIABLE_ASSIGNMENT", "禁止会话变量赋值。")
        dangerous = _function_names(node) & _DANGEROUS_FUNCTIONS
        if dangerous:
            return ValidationIssue("DANGEROUS_FUNCTION", f"禁止函数：{sorted(dangerous)[0]}。")
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            return ValidationIssue("UNVERIFIED_FUNCTION", "禁止调用未验证的包函数。")
        if isinstance(node, exp.Anonymous):
            return ValidationIssue("UNVERIFIED_FUNCTION", f"禁止调用未验证的函数：{node.name.upper()}。")
    return None


def _qualifier_issue(root: exp.Expression, message: str, schema_mapping: Mapping[str, Mapping[str, str]]) -> ValidationIssue:
    table_match = re.search(r"for table:\s*['\"]([^'\"]+)['\"]", message, re.I)
    column_match = re.search(r"Column\s+['\"]([^'\"]+)['\"]", message, re.I)
    if table_match:
        alias = table_match.group(1)
        if not any(
            _source_for_alias(scope, alias) is not None
            for scope in traverse_scope(root)
        ):
            return ValidationIssue("UNKNOWN_ALIAS", f"未知表别名：{alias}。")
        return ValidationIssue("UNKNOWN_COLUMN", f"字段无法在别名 {alias} 对应来源中解析。")

    column_name = column_match.group(1) if column_match else ""
    if column_name:
        match_count = sum(
            1
            for columns in schema_mapping.values()
            if any(name.casefold() == column_name.casefold() for name in columns)
        )
        if match_count > 1 and "could not be resolved." in message:
            return ValidationIssue("AMBIGUOUS_COLUMN", f"未限定字段存在多个来源：{column_name}。")
        return ValidationIssue("UNKNOWN_COLUMN", f"未知字段：{column_name}。")
    if "ambiguous" in message.casefold():
        return ValidationIssue("AMBIGUOUS_COLUMN", "字段引用存在歧义。")
    return ValidationIssue("UNKNOWN_COLUMN", "字段无法在当前查询作用域中解析。")


def _column_identifier_matches(column: exp.Column, candidates: Iterable[str]) -> str | None:
    identifier = column.this if isinstance(column.this, exp.Identifier) else None
    return _match_identifier(identifier, candidates)


def _qualifier_schema(
    schema_mapping: Mapping[str, Mapping[str, str]],
    read_dialect: str,
) -> MappingSchema:
    """Expose exact and unquoted-normalized names without losing exact checks.

    MappingSchema normally normalizes user-provided keys and therefore cannot
    distinguish a quoted mixed-case identifier. Exact quoted references are
    checked separately below; this mapping supplies both lookup spellings to
    sqlglot's qualifier.
    """

    dialect = Dialect.get_or_raise(read_dialect)
    mapping: dict[str, dict[str, str]] = {}
    for table_name, columns in schema_mapping.items():
        normalized_table = dialect.normalize_identifier(exp.to_identifier(table_name, quoted=False)).name
        column_mapping: dict[str, str] = {}
        for column_name, column_type in columns.items():
            column_mapping[column_name] = column_type
            normalized_column = dialect.normalize_identifier(exp.to_identifier(column_name, quoted=False)).name
            column_mapping.setdefault(normalized_column, column_type)
        mapping[table_name] = dict(column_mapping)
        mapping.setdefault(normalized_table, dict(column_mapping))
    return MappingSchema(mapping, dialect=read_dialect, normalize=False)


def _quoted_column_issue(
    root: exp.Expression,
    schema_mapping: Mapping[str, Mapping[str, str]],
) -> ValidationIssue | None:
    for scope in traverse_scope(root):
        for column in scope.columns:
            identifier = column.this if isinstance(column.this, exp.Identifier) else None
            if identifier is None or not identifier.args.get("quoted"):
                continue
            if column.table:
                source = _source_for_alias(scope, column.table)
                if not isinstance(source, exp.Table):
                    # CTE/derived output names are validated by qualify.
                    continue
                actual_table = _match_identifier(_table_identifier(source), schema_mapping)
                if actual_table and identifier.name not in schema_mapping[actual_table]:
                    return ValidationIssue("UNKNOWN_COLUMN", f"未知精确字段：{identifier.name}。")
                continue
            physical = [source for source in scope.sources.values() if isinstance(source, exp.Table)]
            if len(physical) != len(scope.sources):
                continue
            exact_matches = 0
            for source in physical:
                actual_table = _match_identifier(_table_identifier(source), schema_mapping)
                if actual_table and identifier.name in schema_mapping[actual_table]:
                    exact_matches += 1
            if exact_matches == 0:
                return ValidationIssue("UNKNOWN_COLUMN", f"未知精确字段：{identifier.name}。")
            if exact_matches > 1:
                return ValidationIssue("AMBIGUOUS_COLUMN", f"精确字段存在多个来源：{identifier.name}。")
    return None


def _collect_references(
    qualified_root: exp.Expression,
    schema_mapping: Mapping[str, Mapping[str, str]],
) -> tuple[list[str], list[str]]:
    tables: list[str] = []
    columns: list[str] = []
    for scope, alias, source in _physical_sources(qualified_root):
        actual_table = _match_identifier(_table_identifier(source), schema_mapping)
        if not actual_table:
            continue
        tables.append(actual_table)
        for column in scope.columns:
            if column.table.casefold() != alias.casefold():
                continue
            actual_column = _column_identifier_matches(column, schema_mapping[actual_table])
            if actual_column:
                columns.append(f"{actual_table}.{actual_column}")
    return list(dict.fromkeys(tables)), list(dict.fromkeys(columns))


def _star_issues_and_warnings(root: exp.Expression) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    for scope in traverse_scope(root):
        for star in scope.stars:
            alias = star.table
            if alias and _source_for_alias(scope, alias) is None:
                errors.append(ValidationIssue("UNKNOWN_ALIAS", f"未知星号限定别名：{alias}。"))
            else:
                warnings.append(ValidationIssue("SELECT_STAR", "查询使用了 SELECT *。"))
        selects = getattr(scope.expression, "selects", ()) or ()
        for select_expression in selects:
            unwrapped = select_expression.this if isinstance(select_expression, exp.Alias) else select_expression
            if isinstance(unwrapped, exp.Star):
                if not scope.sources:
                    errors.append(ValidationIssue("UNBOUND_STAR", "裸 * 没有任何可见数据来源。"))
                else:
                    warnings.append(ValidationIssue("SELECT_STAR", "查询使用了 SELECT *。"))
    return errors, warnings


def _warning_once(warnings: list[ValidationIssue], issue: ValidationIssue) -> None:
    if issue.code not in {item.code for item in warnings}:
        warnings.append(issue)


def _intent_mapping(intent: Any) -> Mapping[str, Any] | None:
    """Return a defensive mapping for either IntentAnalysis or a test fixture."""
    if intent is None:
        return None
    if isinstance(intent, Mapping):
        return intent
    to_dict = getattr(intent, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        return value if isinstance(value, Mapping) else None
    return None


def _outer_query_nodes(root: exp.Expression) -> Iterable[exp.Expression]:
    """Yield nodes owned by the root result query, excluding nested queries."""

    yield root
    for child in root.iter_expressions():
        if isinstance(child, exp.Query):
            continue
        yield from _outer_query_nodes(child)


def _outer_contains_expression(root: exp.Expression, expression_type: type[exp.Expression]) -> bool:
    return any(isinstance(node, expression_type) for node in _outer_query_nodes(root))


def _root_clause(root: exp.Expression, name: str, expression_type: type[exp.Expression]) -> exp.Expression | None:
    value = root.args.get(name)
    return value if isinstance(value, expression_type) else None


def _root_has_order(root: exp.Expression) -> bool:
    return _root_clause(root, "order", exp.Order) is not None


def _root_has_group(root: exp.Expression) -> bool:
    return _root_clause(root, "group", exp.Group) is not None


def _expression_mentions_temporal_column(node: exp.Expression | None, temporal_names: set[str]) -> bool:
    return node is not None and any(
        isinstance(item, exp.Column) and item.name.casefold() in temporal_names
        for item in _outer_query_nodes(node)
    )


def _expression_matches_requested_column(node: exp.Expression | None, expected_columns: set[str]) -> bool:
    if node is None or not expected_columns:
        return True
    return any(
        isinstance(item, exp.Column) and item.name.casefold() in expected_columns
        for item in _outer_query_nodes(node)
    )


def _is_temporal_bound(node: exp.Expression | None) -> bool:
    """A date boundary cannot be supplied solely by another query column."""

    return node is not None and not any(
        isinstance(item, (exp.Column, exp.Query, exp.Null))
        for item in _outer_query_nodes(node)
    )


def _root_has_single_row_limit(root: exp.Expression) -> bool:
    limit = _root_clause(root, "limit", exp.Limit)
    if limit is None:
        return False
    expression = limit.expression
    if not (isinstance(expression, exp.Literal) and not expression.is_string and expression.this == "1"):
        return False
    offset = _root_clause(root, "offset", exp.Offset)
    return offset is None


def _root_has_directional_single_order(
    root: exp.Expression,
    *,
    descending: bool,
    expected_columns: set[str] | None = None,
) -> bool:
    order = _root_clause(root, "order", exp.Order)
    if order is None or not order.expressions or not _root_has_single_row_limit(root):
        return False
    first_ordered = order.expressions[0]
    return (
        isinstance(first_ordered, exp.Ordered)
        and bool(first_ordered.args.get("desc")) is descending
        and _expression_matches_requested_column(first_ordered.this, expected_columns or set())
    )


def _outer_extreme_aggregate(
    root: exp.Expression,
    *,
    expression_type: type[exp.Expression],
    temporal_names: set[str] | None = None,
    expected_columns: set[str] | None = None,
) -> bool:
    for node in _outer_query_nodes(root):
        if not isinstance(node, expression_type):
            continue
        if temporal_names is not None and not _expression_mentions_temporal_column(node, temporal_names):
            continue
        if not _expression_matches_requested_column(node.this, expected_columns or set()):
            continue
        return True
    return False


def _root_has_directional_temporal_order(
    root: exp.Expression,
    *,
    descending: bool,
    temporal_names: set[str],
) -> bool:
    order = _root_clause(root, "order", exp.Order)
    if order is None or not _root_has_directional_single_order(root, descending=descending):
        return False
    first_ordered = order.expressions[0]
    return isinstance(first_ordered, exp.Ordered) and _expression_mentions_temporal_column(
        first_ordered.this,
        temporal_names,
    )


def _recency_requirement_satisfied(
    root: exp.Expression,
    *,
    latest: bool,
    temporal_names: set[str],
) -> bool:
    if latest:
        return _outer_extreme_aggregate(
            root,
            expression_type=exp.Max,
            temporal_names=temporal_names,
        ) or _root_has_directional_temporal_order(
            root,
            descending=True,
            temporal_names=temporal_names,
        )
    return _outer_extreme_aggregate(
        root,
        expression_type=exp.Min,
        temporal_names=temporal_names,
    ) or _root_has_directional_temporal_order(
        root,
        descending=False,
        temporal_names=temporal_names,
    )


def _schema_temporal_names(metadata: Mapping[str, Mapping[str, Any]]) -> set[str]:
    names: set[str] = set()
    temporal_type = re.compile(r"(?:DATE|TIME|YEAR|TIMESTAMP|DATETIME|INTERVAL)", re.I)
    temporal_name = re.compile(
        r"(?:^|_)(?:date|time|day|month|year|period|created|updated|admission|discharge|visit)(?:$|_)",
        re.I,
    )
    for item in metadata.values():
        for column in item.get("columns", ()) or ():
            if not isinstance(column, Mapping):
                continue
            name = str(column.get("column_name", "")).strip()
            if not name:
                continue
            data_type = str(column.get("data_type", ""))
            comment = str(column.get("column_comment", ""))
            if temporal_type.search(data_type) or temporal_name.search(name) or re.search(
                r"日期|时间|年月|季度|时刻", comment
            ):
                names.add(name.casefold())
    return names


def _is_temporal_filter_predicate(node: exp.Expression, temporal_names: set[str]) -> bool:
    comparison_types = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
    if isinstance(node, comparison_types):
        return (
            _expression_mentions_temporal_column(node.this, temporal_names)
            and _is_temporal_bound(node.expression)
        ) or (
            _expression_mentions_temporal_column(node.expression, temporal_names)
            and _is_temporal_bound(node.this)
        )
    if isinstance(node, exp.Between):
        return (
            _expression_mentions_temporal_column(node.this, temporal_names)
            and _is_temporal_bound(node.args.get("low"))
            and _is_temporal_bound(node.args.get("high"))
        )
    if isinstance(node, exp.In):
        return _expression_mentions_temporal_column(node.this, temporal_names) and any(
            _is_temporal_bound(value) for value in node.expressions
        )
    return False


def _root_has_temporal_filter(root: exp.Expression, temporal_names: set[str]) -> bool:
    predicates: list[exp.Expression] = []
    where = _root_clause(root, "where", exp.Where)
    if where is not None:
        predicates.append(where.this)
    for join in root.args.get("joins", ()) or ():
        if not isinstance(join, exp.Join):
            continue
        side = str(join.args.get("side") or "").upper()
        kind = str(join.args.get("kind") or "").upper()
        if side in {"LEFT", "RIGHT", "FULL"} or kind not in {"", "INNER"}:
            continue
        if isinstance(join.args.get("on"), exp.Expression):
            predicates.append(join.args["on"])

    return any(
        _is_temporal_filter_predicate(node, temporal_names)
        for predicate in predicates
        for node in _outer_query_nodes(predicate)
    )


def _intent_ast_issue(
    root: exp.Expression,
    intent: Any,
    *,
    tables: Iterable[str],
    columns: Iterable[str],
    schema_metadata: Mapping[str, Mapping[str, Any]],
    his_semantics: Iterable[Mapping[str, Any]],
) -> ValidationIssue | None:
    """Check deterministic intent requirements against the qualified AST.

    This intentionally rejects only requirements that can be observed locally;
    vague business language remains a clarification concern instead of being
    guessed from model prose.
    """
    data = _intent_mapping(intent)
    if data is None:
        return None

    table_names = {str(item).casefold() for item in tables}
    column_names = {str(item).casefold() for item in columns}
    explicit_tables = [str(item).strip() for item in data.get("explicit_tables", ()) or () if str(item).strip()]
    for table in explicit_tables:
        if table.casefold() not in table_names:
            return ValidationIssue("INTENT_TABLE_MISSING", f"Intent explicit table is absent from SQL: {table}")

    explicit_columns = [str(item).strip() for item in data.get("explicit_columns", ()) or () if str(item).strip()]
    for column in explicit_columns:
        key = column.casefold()
        if "." in key:
            if key not in column_names:
                return ValidationIssue("INTENT_COLUMN_MISSING", f"Intent explicit column is absent from SQL: {column}")
        elif not any(item.rsplit(".", 1)[-1] == key for item in column_names):
            return ValidationIssue("INTENT_COLUMN_MISSING", f"Intent explicit column is absent from SQL: {column}")

    signals = {str(item).casefold() for item in data.get("signals", ()) or ()}
    temporal_names = _schema_temporal_names(schema_metadata)
    extreme_target_columns = {item.rsplit(".", 1)[-1].casefold() for item in explicit_columns}
    if "aggregate" in signals and not _outer_contains_expression(root, exp.AggFunc):
        return ValidationIssue("INTENT_AGGREGATE_MISSING", "Intent requests an aggregate but SQL has none")
    if "group_by" in signals and not _root_has_group(root):
        return ValidationIssue("INTENT_GROUP_BY_MISSING", "Intent requests grouping but SQL has no GROUP BY")
    extreme_sort_satisfied = (
        "maximum" in signals
        and _outer_extreme_aggregate(
            root,
            expression_type=exp.Max,
            expected_columns=extreme_target_columns,
        )
    ) or (
        "minimum" in signals
        and "first" not in signals
        and _outer_extreme_aggregate(
            root,
            expression_type=exp.Min,
            expected_columns=extreme_target_columns,
        )
    )
    if "sort" in signals and not _root_has_order(root) and not extreme_sort_satisfied:
        return ValidationIssue("INTENT_SORT_MISSING", "Intent requests sorting but SQL has no ORDER BY")
    if "maximum" in signals and not (
        _outer_extreme_aggregate(
            root,
            expression_type=exp.Max,
            expected_columns=extreme_target_columns,
        )
        or _root_has_directional_single_order(
            root,
            descending=True,
            expected_columns=extreme_target_columns,
        )
    ):
        return ValidationIssue("INTENT_EXTREME_MISSING", "Intent requests a maximum but SQL has no maximum result")
    if "minimum" in signals and "first" not in signals and not (
        _outer_extreme_aggregate(
            root,
            expression_type=exp.Min,
            expected_columns=extreme_target_columns,
        )
        or _root_has_directional_single_order(
            root,
            descending=False,
            expected_columns=extreme_target_columns,
        )
    ):
        return ValidationIssue("INTENT_EXTREME_MISSING", "Intent requests a minimum but SQL has no minimum result")
    if "distinct" in signals and not _outer_contains_expression(root, exp.Distinct):
        return ValidationIssue("INTENT_DISTINCT_MISSING", "Intent requests distinct results but SQL has no DISTINCT")
    if "ranking" in signals:
        has_window_rank = any(
            isinstance(node, exp.Window)
            and any(token in node.sql().upper() for token in ("RANK", "ROW_NUMBER", "DENSE_RANK"))
            for node in _outer_query_nodes(root)
        )
        if not _root_has_order(root) and not has_window_rank:
            return ValidationIssue("INTENT_RANKING_MISSING", "Intent requests ranking but SQL has no ordering")
    if "latest" in signals and not _recency_requirement_satisfied(
        root,
        latest=True,
        temporal_names=temporal_names,
    ):
        return ValidationIssue("INTENT_RECENCY_MISSING", "Intent requests latest but SQL has no latest temporal result")
    if "first" in signals and not _recency_requirement_satisfied(
        root,
        latest=False,
        temporal_names=temporal_names,
    ):
        return ValidationIssue("INTENT_RECENCY_MISSING", "Intent requests first but SQL has no first temporal result")
    if "time_range" in signals and not _root_has_temporal_filter(root, temporal_names):
        return ValidationIssue("INTENT_TIME_RANGE_MISSING", "Intent requests a time range but SQL has no temporal filter")

    concepts = {str(item).casefold() for item in data.get("his_concepts", ()) or () if str(item).strip()}
    if concepts:
        bound_tables: set[str] = set()
        for item in his_semantics:
            if not isinstance(item, Mapping):
                continue
            names = {str(item.get("term", "")).casefold()}
            names.update(str(value).casefold() for value in item.get("synonyms", ()) or ())
            if not concepts.intersection(names):
                continue
            for binding in item.get("bindings", ()) or ():
                if isinstance(binding, Mapping):
                    table = str(binding.get("table", "")).strip()
                    if table:
                        bound_tables.add(table.casefold())
        if bound_tables and not bound_tables.intersection(table_names):
            return ValidationIssue("HIS_BINDING_NOT_USED", "SQL does not use a table bound to the requested HIS term")
    return None


def validate_sql(
    sql: str,
    *,
    dialect: str,
    schema_bundle: Mapping[str, Any],
    strong_evidence_tables: Iterable[str] = (),
    declared_tables: Iterable[str] = (),
    declared_columns: Iterable[str] = (),
    intent: Any | None = None,
    his_semantics: Iterable[Mapping[str, Any]] = (),
    strict_evidence: bool = False,
    allow_oracle_dual: bool = True,
) -> SqlValidationResult:
    """Validate a candidate without executing it or rewriting its dialect text."""

    dialect_key = dialect.strip().lower()
    if dialect_key not in _DIALECTS:
        raise ValueError(f"不支持的 SQL 方言：{dialect}")
    read_dialect = _DIALECTS[dialect_key]
    original_sql = (sql or "").strip()
    if not original_sql:
        return _failure("EMPTY_SQL", "SQL 不能为空。")
    schema_mapping, metadata = _schema_map(schema_bundle)

    if read_dialect == "mysql" and _has_mysql_file_output(original_sql):
        return _failure("SELECT_INTO", "禁止 SELECT INTO OUTFILE/DUMPFILE 文件输出。")

    try:
        parsed = sqlglot.parse(original_sql, read=read_dialect)
    except (ParseError, TokenError, ValueError) as exc:
        return _failure("SQL_PARSE_ERROR", f"SQL 无法按 {read_dialect} 方言解析：{exc}。")

    if _has_real_comments(parsed):
        return _failure("SQL_COMMENT", "SQL 中禁止真实注释。")
    if len(parsed) != 1 or parsed[0] is None:
        return _failure("MULTIPLE_STATEMENTS", "只允许恰好一条非空 SQL 语句。")
    root = parsed[0]
    assert root is not None
    if not isinstance(root, exp.Query):
        return _failure("READ_ONLY_REQUIRED", "根节点必须是只读查询。")

    side_effect = _side_effect_issue(root)
    if side_effect:
        return _failure(side_effect.code, side_effect.message)

    physical_sources = _physical_sources(root)
    physical_tables: list[str] = []
    for _, _, source in physical_sources:
        table_name = source.name
        if "@" in table_name:
            return _failure("DATABASE_LINK", f"禁止 Oracle database link：{table_name}。")
        if source.args.get("db") is not None or source.args.get("catalog") is not None:
            return _failure("QUALIFIED_TABLE_REFERENCE", f"Schema 元数据无法验证限定表引用：{source.sql()}。")
        matched_table = _match_identifier(_table_identifier(source), schema_mapping)
        if matched_table:
            physical_tables.append(matched_table)
            continue
        if read_dialect == "oracle" and allow_oracle_dual and table_name.casefold() == "dual":
            continue
        return _failure("UNKNOWN_TABLE", f"未知物理表：{table_name}。", tables=physical_tables)

    star_errors, warnings = _star_issues_and_warnings(root)
    if star_errors:
        return SqlValidationResult(
            status="failed",
            errors=tuple(star_errors),
            warnings=tuple(warnings),
            tables=tuple(dict.fromkeys(physical_tables)),
            columns=(),
            validated_sql="",
        )

    quoted_issue = _quoted_column_issue(root, schema_mapping)
    if quoted_issue:
        return _failure(quoted_issue.code, quoted_issue.message, warnings=warnings, tables=physical_tables)

    mapping_schema = _qualifier_schema(schema_mapping, read_dialect)
    try:
        qualified = qualify(
            copy.deepcopy(root),
            dialect=read_dialect,
            schema=mapping_schema,
            expand_stars=False,
            identify=False,
            quote_identifiers=False,
            validate_qualify_columns=True,
        )
        # traverse_scope is deliberately run on qualifier output: it is the
        # authoritative alias/CTE/derived-table scope model for reference extraction.
        list(traverse_scope(qualified))
    except OptimizeError as exc:
        issue = _qualifier_issue(root, str(exc), schema_mapping)
        return _failure(issue.code, issue.message, warnings=warnings, tables=physical_tables)
    except (ParseError, ValueError) as exc:
        return _failure("QUALIFICATION_ERROR", f"SQL 作用域校验失败：{exc}。", warnings=warnings, tables=physical_tables)

    tables, columns = _collect_references(qualified, schema_mapping)

    if not any(isinstance(node, exp.Where) for node in root.walk()):
        wide_tables = [table for table in tables if len(metadata.get(table, {}).get("columns", ())) >= 20]
        if wide_tables:
            _warning_once(
                warnings,
                ValidationIssue("UNFILTERED_WIDE_TABLE", f"未筛选宽表查询：{', '.join(wide_tables)}。"),
            )

    evidence = {name.casefold() for name in strong_evidence_tables}
    if evidence and not evidence.intersection(name.casefold() for name in tables):
        evidence_issue = ValidationIssue(
            "OUTSIDE_RETRIEVED_EVIDENCE",
            "SQL does not use any retrieved strong-evidence table",
        )
        if strict_evidence:
            return _failure(
                evidence_issue.code,
                evidence_issue.message,
                warnings=warnings,
                tables=tables,
                columns=columns,
            )
        _warning_once(warnings, evidence_issue)

    intent_issue = _intent_ast_issue(
        root,
        intent,
        tables=tables,
        columns=columns,
        schema_metadata=metadata,
        his_semantics=his_semantics,
    )
    if intent_issue is not None:
        return _failure(
            intent_issue.code,
            intent_issue.message,
            warnings=warnings,
            tables=tables,
            columns=columns,
        )

    declared_table_set = {str(name).casefold() for name in declared_tables if str(name).strip()}
    if declared_table_set and declared_table_set != {name.casefold() for name in tables}:
        _warning_once(warnings, ValidationIssue("DECLARED_TABLES_MISMATCH", "模型声明的表与 AST 实际引用不一致。"))
    declared_column_set = {str(name).casefold() for name in declared_columns if str(name).strip()}
    if declared_column_set and declared_column_set != {name.casefold() for name in columns}:
        _warning_once(warnings, ValidationIssue("DECLARED_COLUMNS_MISMATCH", "模型声明的字段与 AST 实际引用不一致。"))

    return SqlValidationResult(
        status="passed",
        errors=(),
        warnings=tuple(warnings),
        tables=tuple(tables),
        columns=tuple(columns),
        validated_sql=original_sql,
    )


__all__ = ["POLICY_VERSION", "SqlValidationResult", "ValidationIssue", "validate_sql"]
