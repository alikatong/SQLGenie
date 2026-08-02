from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_WRITE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:^|请|帮我|需要|执行|立即|批量|全部|把|将|并|然后)\s*"
        r"(?<!已)(?:新增|插入|写入|更新|修改|变更|删除|移除|清空|创建|新建|建立|删除|删掉|授权|撤销授权|调用)"
        r"[^，。,.!?\n]{0,8}?"
        r"(?:数据|记录|行|表|字段|列|索引|视图|用户|角色|权限|过程|存储过程|函数|触发器|对象|schema|数据库)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:please\s+)?(?:insert|update|delete|remove|add|modify|merge|create|alter|drop|truncate|grant|revoke|call|execute)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:insert\s+into|update\s+[\w\"`\[]+\s+set|delete\s+from|merge\s+into|"
        r"create\s+(?:table|view|index)|alter\s+table|drop\s+(?:table|view|index)|"
        r"truncate\s+table|grant\s+.+\s+to|revoke\s+.+\s+from|call\s+[\w.]+|exec(?:ute)?\s+[\w.]+)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"(?:请|帮我|需要|执行|立即|批量|把|将|并|然后)"
        r"[^，。,.!?\n]{0,24}?"
        r"(?<!已)(?:新增|插入|写入|更新|修改|变更|删除|移除|清空|创建|新建|授权|撤销授权|调用)",
        re.IGNORECASE,
    ),
)

_SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "maximum",
        re.compile(
            r"\b(?:highest|maximum|largest|greatest)\b|\u6700\u9ad8|\u6700\u5927",
            re.I,
        ),
    ),
    (
        "minimum",
        re.compile(
            r"\b(?:lowest|minimum|smallest|least|earliest)\b|\u6700\u4f4e|\u6700\u5c0f",
            re.I,
        ),
    ),
    ("aggregate", re.compile(r"统计|汇总|合计|总数|数量|平均|均值|最大|最小|求和|计数|\b(?:count|sum|avg|max|min)\b", re.I)),
    ("group_by", re.compile(r"按.+?(?:分组|统计|汇总)|分组|各(?:科室|部门|类别|类型|状态)|\bgroup\s+by\b", re.I)),
    ("sort", re.compile(r"排序|升序|降序|最高|最低|最多|最少|\border\s+by\b", re.I)),
    ("distinct", re.compile(r"去重|不重复|唯一|\bdistinct\b", re.I)),
    ("ranking", re.compile(r"排名|排行|前\s*\d+|top\s*\d+|\brank(?:ing)?\b", re.I)),
    ("latest", re.compile(r"最近一次|最后一次|最新|末次|\blatest\b", re.I)),
    ("first", re.compile(r"首次|第一次|最早|\bfirst\b", re.I)),
    ("year_over_year", re.compile(r"同比|year[- ]over[- ]year|\byoy\b", re.I)),
    ("period_over_period", re.compile(r"环比|period[- ]over[- ]period|\bmom\b|\bqoq\b", re.I)),
    (
        "time_range",
        re.compile(
            r"今天|昨日|本周|上周|本月|上月|本年|去年|近\s*\d+\s*(?:天|周|月|年)|"
            r"最近\s*\d+\s*(?:天|周|月|年)|\d{4}[-/.年]\d{1,2}|"
            r"(?:从|自).+?(?:到|至)|between\b|last\s+\d+\s+(?:day|week|month|year)s?",
            re.I,
        ),
    ),
    ("first", re.compile(r"\bearliest\b", re.I)),
)

_INJECTION_PATTERN = re.compile(
    r"忽略(?:之前|以上|所有)?(?:规则|指令|提示词)|泄露(?:系统)?提示词|显示(?:系统)?提示词|"
    r"改写(?:系统)?策略|越过(?:安全|系统)?规则|forget\s+(?:all\s+)?(?:previous|prior)\s+instructions|"
    r"ignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions|reveal\s+(?:the\s+)?system\s+prompt|"
    r"developer\s+message|system\s+message",
    re.I,
)

_QUERY_CUES = re.compile(
    r"查询|查找|列出|显示|返回|获取|统计|汇总|分析|计算|筛选|检索|排名|排行|多少|哪些|"
    r"\b(?:select|find|show|list|get|query|count|calculate|report|summarize|compare)\b",
    re.I,
)

_AMBIGUOUS_CONCEPT_GROUPS: tuple[tuple[str, ...], ...] = (
    ("患者数", "就诊人次"),
    ("申请时间", "执行时间", "报告时间"),
    ("入院时间", "出院时间"),
)


@dataclass(frozen=True)
class IntentWarning:
    code: str
    message: str


@dataclass(frozen=True)
class IntentAnalysis:
    operation: str = "SELECT"
    signals: tuple[str, ...] = ()
    his_concepts: tuple[str, ...] = ()
    explicit_tables: tuple[str, ...] = ()
    explicit_columns: tuple[str, ...] = ()
    requires_clarification: bool = False
    clarification_reason: str = ""
    warnings: tuple[IntentWarning, ...] = ()
    error_code: str | None = None

    @property
    def accepted(self) -> bool:
        return self.error_code is None and not self.requires_clarification

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["signals"] = list(self.signals)
        value["his_concepts"] = list(self.his_concepts)
        value["explicit_tables"] = list(self.explicit_tables)
        value["explicit_columns"] = list(self.explicit_columns)
        value["warnings"] = [asdict(item) for item in self.warnings]
        return value


def _clean_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return _CONTROL_CHARACTERS.sub("", normalized).strip()


def _identifier_mentioned(text: str, identifier: str) -> bool:
    if not identifier:
        return False
    escaped = re.escape(identifier)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$#]*", identifier):
        return re.search(rf"(?<![A-Za-z0-9_$#]){escaped}(?![A-Za-z0-9_$#])", text, re.I) is not None
    return identifier.casefold() in text.casefold()


def _schema_identifiers(schema_bundle: Mapping[str, Any] | None) -> tuple[list[str], list[tuple[str, str]]]:
    tables: list[str] = []
    columns: list[tuple[str, str]] = []
    if not schema_bundle:
        return tables, columns
    for table in schema_bundle.get("tables", ()) or ():
        if not isinstance(table, Mapping):
            continue
        table_name = str(table.get("table_name", "")).strip()
        if not table_name:
            continue
        tables.append(table_name)
        for column in table.get("columns", ()) or ():
            if not isinstance(column, Mapping):
                continue
            column_name = str(column.get("column_name", "")).strip()
            if column_name:
                columns.append((table_name, column_name))
    return tables, columns


def _matched_semantics(text: str, semantics: Sequence[Mapping[str, Any]]) -> tuple[list[str], set[str]]:
    concepts: list[str] = []
    defined_names: set[str] = set()
    for item in semantics:
        if not isinstance(item, Mapping) or item.get("enabled", True) in (False, 0):
            continue
        term = str(item.get("term", "")).strip()
        synonyms = item.get("synonyms", item.get("synonyms_json", ())) or ()
        if isinstance(synonyms, str):
            # Persistence layer should normally decode JSON; a plain string is still safe data.
            synonyms = (synonyms,)
        names = [term, *(str(value).strip() for value in synonyms if str(value).strip())]
        definition = str(item.get("definition", "")).strip()
        if definition:
            defined_names.update(name.casefold() for name in names if name)
        if term and any(_identifier_mentioned(text, name) for name in names if name):
            concepts.append(term)
    return list(dict.fromkeys(concepts)), defined_names


def _clarification_for_ambiguity(text: str, defined_names: set[str]) -> str:
    for concepts in _AMBIGUOUS_CONCEPT_GROUPS:
        mentioned = [concept for concept in concepts if concept in text]
        if not mentioned:
            continue
        if any(concept.casefold() in defined_names for concept in mentioned):
            continue
        if len(mentioned) > 1:
            return f"请求同时涉及 {'、'.join(mentioned)}，但本地 HIS 语义目录没有可用于消歧的定义。"
        if mentioned[0] == "患者数":
            return "请确认“患者数”是按患者去重计数，还是按就诊人次计数；本地 HIS 语义目录尚无定义。"
    return ""


def analyze_intent(
    text: str,
    *,
    schema_bundle: Mapping[str, Any] | None = None,
    his_semantics: Sequence[Mapping[str, Any]] = (),
) -> IntentAnalysis:
    """Analyze request with deterministic, local-only rules.

    This function never generates SQL and never accesses a database.  Callers can
    reuse it for generation and feedback admission.
    """

    cleaned = _clean_text(text)
    warnings: list[IntentWarning] = []
    injection_matches = list(_INJECTION_PATTERN.finditer(cleaned))
    if injection_matches:
        warnings.append(
            IntentWarning(
                code="PROMPT_INJECTION_TEXT",
                message="请求包含试图改变系统规则或读取提示词的文本；该文本仅按用户数据处理。",
            )
        )

    tables, columns = _schema_identifiers(schema_bundle)
    explicit_tables = [name for name in tables if _identifier_mentioned(cleaned, name)]
    explicit_columns: list[str] = []
    for table_name, column_name in columns:
        qualified = f"{table_name}.{column_name}"
        if _identifier_mentioned(cleaned, qualified):
            explicit_columns.append(qualified)
        elif _identifier_mentioned(cleaned, column_name):
            explicit_columns.append(column_name)

    concepts, defined_names = _matched_semantics(cleaned, his_semantics)
    signals = list(dict.fromkeys(name for name, pattern in _SIGNAL_PATTERNS if pattern.search(cleaned)))

    if any(pattern.search(cleaned) for pattern in _WRITE_PATTERNS):
        return IntentAnalysis(
            signals=tuple(signals),
            his_concepts=tuple(concepts),
            explicit_tables=tuple(explicit_tables),
            explicit_columns=tuple(dict.fromkeys(explicit_columns)),
            requires_clarification=True,
            clarification_reason="SQLGenie 只生成只读查询，不能处理新增、更新、删除、DDL、授权或过程调用请求。",
            warnings=tuple(warnings),
            error_code="UNSUPPORTED_OPERATION",
        )

    text_without_injection = _INJECTION_PATTERN.sub(" ", cleaned)
    has_query_target = bool(
        _QUERY_CUES.search(text_without_injection)
        or explicit_tables
        or explicit_columns
        or concepts
        or signals
        or len(re.sub(r"\W+", "", text_without_injection, flags=re.UNICODE)) >= 4
    )
    if not has_query_target:
        return IntentAnalysis(
            signals=tuple(signals),
            his_concepts=tuple(concepts),
            explicit_tables=tuple(explicit_tables),
            explicit_columns=tuple(dict.fromkeys(explicit_columns)),
            requires_clarification=True,
            clarification_reason="请求中没有可识别的只读查询目标。",
            warnings=tuple(warnings),
            error_code="NO_QUERY_INTENT",
        )

    clarification = _clarification_for_ambiguity(cleaned, defined_names)
    return IntentAnalysis(
        signals=tuple(signals),
        his_concepts=tuple(concepts),
        explicit_tables=tuple(explicit_tables),
        explicit_columns=tuple(dict.fromkeys(explicit_columns)),
        requires_clarification=bool(clarification),
        clarification_reason=clarification,
        warnings=tuple(warnings),
    )


__all__ = ["IntentAnalysis", "IntentWarning", "analyze_intent"]
