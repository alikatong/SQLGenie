# SQLGenie HIS Schema 远端大模型 SQL 生成优化开发设计

文档状态：二次技术审查完成，可进入编码
文档版本：v1.0
适用版本：SQLGenie 当前工作树
设计日期：2026-07-31
目标读者：后端、前端、测试与 HIS 元数据治理人员

## 1. 文档目的

本文定义 SQLGenie 面向医院信息系统（HIS）Schema 场景的近期开发方案。SQLGenie 的运行时输入边界只有表、字段、注释、关系、HIS 术语、SQL 反馈样例和用户的 SQL 设计意图。系统只使用本地 SQLite 保存这些元数据，不包含目标数据库驱动、连接配置或 SQL 执行入口。方案保留远端大模型，不引入本地生成模型；本地侧负责 HIS 语义检索、提示词编排、SQL 静态校验、反馈治理、审计与评测。

本文既描述目标架构，也给出本轮可直接编码和验收的交付范围。长期能力被明确标记为后续阶段，不能用尚未实现的长期设计替代本轮验收。

## 2. 核心结论

SQLGenie 不应继续把所有能力堆进一个系统提示词。HIS 场景的主要风险不是提示词数量不足，而是远端调用前后缺少确定性的本地治理层。

本轮采用以下设计决策：

1. 保留 OpenAI 兼容的远端大模型调用，不部署本地生成模型。
2. 只生成 `SELECT`、带 `WITH` 的查询和只读集合查询；不生成写操作，也不执行任何 SQL。
3. 产品中不新增目标数据库驱动、连接池、连接字符串、试连、`EXPLAIN` 或查询执行 API。
4. Schema 注释、历史问题和反馈样例均视为不可信上下文，不得改变系统指令。
5. 模型输出必须经过本地 SQL AST 校验；提示词中的“审核模型”只能作为质量辅助，不能代替策略校验。
6. 将“操作识别、Schema 候选排序、HIS 术语映射、SQL 策略检查”放在本地确定性代码中，以降低远端上下文和重复调用成本。
7. 提示词拆分为有版本的政策、HIS 语义、Schema 证据、任务和输出契约模块，禁止继续扩展单一超长字符串。
8. API Key 采用只写更新；读取接口只返回“是否已配置”和尾四位，不回显完整密钥或可再次提交的掩码字符串。
9. 每次生成记录请求 ID、提示词版本、模型、检索证据、策略结果、告警、耗时和令牌使用量（供应商返回时）。
10. HIS 语义词典与指标口径由本地维护，只向远端发送当前问题实际命中的最小片段。

## 3. 当前实现审计

### 3.1 当前调用链

现有生成流程为：

1. 已登录用户提交 `db_id`、自然语言和目标数据库类型。
2. 服务端按 `db_id` 读取完整 Schema，并执行向量与关键词混合检索。
3. 服务端从已批准反馈中检索历史问题和修正 SQL。
4. 第一轮远端模型生成候选 SQL。
5. 开启深度思考时，第二轮远端模型审核并改写候选 SQL。
6. 服务端直接保存自然语言、生成 SQL 和命中表，并把未经本地 AST 策略校验的 SQL 返回前端。

### 3.2 可保留的基础能力

- FastAPI 鉴权、JWT 失效版本和管理员角色已存在。
- Schema 已结构化存储，并建立表、字段与关系元数据。
- Schema RAG 已支持本地向量检索、关键词兜底和外键扩展。
- SQL 生成采用低温度，并已有“生成 + 审核”两阶段模型调用。
- 用户反馈需要管理员批准后才进入反馈 RAG。
- 已有请求体上限、网络启动安全检查和基础自动化测试。

### 3.3 主要问题与代码证据

| 优先级 | 当前问题 | 代码证据 | 直接风险 |
|---|---|---|---|
| P0 | 模型返回后没有本地 AST 策略校验 | `backend/llm.py:97-121`、`backend/main.py:396-421` | 多语句、写操作、越界表字段或危险函数被直接返回 |
| P0 | API Key 以普通配置值存储并通过配置接口完整返回 | `backend/schemas.py:227-244`、`backend/crud.py:654-728`、`backend/main.py:516-528` | 密钥被前端、日志、浏览器扩展或管理员会话暴露 |
| P1 | 操作类型依赖关键词推断 | `backend/rag.py:837-846` | 普通字段语义可能被误判，明确写请求也可能生成不符合只读边界的 SQL |
| P1 | RAG 缺少相关性阈值，未命中时会回退到前 N 张表 | `backend/rag.py:849-920` | 上下文过宽、成本升高、无关 Schema 被发送 |
| P1 | 反馈 RAG 只判断“已批准”，没有 SQL 合法性、Schema 范围和注入文本校验 | `backend/database.py:77-95`、`backend/rag.py:583-685` | 错误或恶意样例污染后续生成 |
| P1 | 历史清理只在读取历史列表时触发 | `backend/crud.py:731-739`、`backend/crud.py:945-946` | 无人访问列表时数据不会按期删除 |
| P2 | 提示词是代码常量，没有版本、发布、回滚和评测绑定 | `backend/llm.py:20-59`、`backend/llm.py:136-201` | 变更不可追溯，难以做灰度与回归测试 |

以上行号以本文设计日期的工作树为准。编码后应使用行为测试作为权威证据，而不是继续依赖静态行号。

## 4. 交付范围

### 4.1 本轮必须实现

- 模块化、可版本识别的 HIS 提示词编排与可配置 HIS 语义词典。
- 本地意图分析：只读操作判定、查询复杂度信号、时间与聚合意图提取。
- RAG 改进：相关性阈值、命中证据与得分、无证据时澄清而非全量回退。
- 本地 SQL AST 校验：单语句、只读、方言匹配、Schema 表字段范围、禁止注释和危险结构。
- API Key 非回显读取与“留空表示不修改”的更新语义。
- 生成追踪字段：请求 ID、提示词版本、模型、检索得分、策略结果、告警、耗时和令牌使用量。
- 已批准反馈进入 RAG 前执行 SQL 合法性、Schema 范围、查询意图和提示词边界校验。
- 管理员可维护 HIS 术语、同义词、定义、适用数据库和启用状态。
- 工作台展示校验状态、告警、请求 ID、命中表与命中术语。
- 自动化测试覆盖提示词注入、RAG 阈值、HIS 术语、SQL 策略、密钥不回显、追踪字段和兼容性。

### 4.2 本轮明确不实现

- 引入目标数据库驱动、连接配置、连接测试、执行计划或 SQL 执行能力。
- 生成 `INSERT`、`UPDATE`、`DELETE`、DDL 或存储过程。
- 本地部署生成式大模型。
- 在线提示词编辑器、提示词灰度发布平台和多供应商路由。

### 4.3 后续阶段

- HIS 术语批量导入导出、版本对比、指标口径生效日期和审批流。
- 固定评测集管理、提示词灰度发布和版本回滚界面。
- 多供应商路由、故障切换与按模型的能力配置。

## 5. 目标架构

### 5.1 组件边界

```text
浏览器
  -> FastAPI 身份认证
  -> 本地意图分析与请求规范化
  -> Schema/HIS 语义混合检索
  -> 版本化提示词编译器
  -> 远端模型网关（OpenAI 兼容）
  -> 本地 SQL AST 策略引擎
  -> 生成追踪与历史记录
  -> 浏览器展示 SQL、证据、告警和请求 ID
```

远端模型只接收最小上下文包：任务策略、命中的 Schema 片段、必要的 HIS 术语定义、用户请求、经过校验的反馈示例，以及严格输出契约。上下文不包含目标数据库凭据、数据库连接信息或查询结果行。

### 5.2 请求时序

1. API 生成不可预测的 `request_id`，校验数据库定义存在且请求方言与定义一致。
2. 本地意图分析器识别只读操作、聚合、分组、排序、时间范围和澄清信号。
3. Schema RAG 与 HIS 术语 RAG 并行检索；低于阈值时返回可解释的澄清请求。
4. 反馈 RAG 只使用已批准、同方言且重新通过 SQL 策略校验的示例。
5. 提示词编译器按固定模块顺序组装远端请求，并记录模板版本和上下文哈希。
6. 远端模型返回严格 JSON，服务端拒绝无法解析或不符合输出模式的响应。
7. SQL 策略引擎按目标方言解析 AST，执行只读、单语句、表字段范围和危险结构校验。
8. 校验失败时，允许一次受约束的修复调用；修复结果必须重新通过全部本地策略。
9. 服务端保存生成历史和追踪信息，返回 SQL、本地校验状态、检索证据、告警和请求 ID。

### 5.3 信任边界

| 区域 | 可信级别 | 允许包含的数据 | 禁止行为 |
|---|---|---|---|
| 本地控制平面 | 高 | 用户身份、Schema 元数据、HIS 术语、策略配置 | 在日志中记录密钥或完整远端请求 |
| 本地元数据与 RAG | 中 | 已审批的 Schema、HIS 术语、反馈 | 把注释或反馈当作系统指令 |
| 远端模型 | 不可信计算方 | 最小化 Schema、HIS 定义、SQL 设计意图和输出契约 | 接收 API Key、登录令牌、目标数据库凭据或结果行 |
| 模型输出 | 不可信输入 | 待解析 JSON 和候选 SQL | 未经本地校验直接展示为“安全”结果 |

## 6. 本地意图分析

### 6.1 目标

意图分析器不负责生成 SQL，只负责以确定性规则提取生成前可验证的信息，减少发送给模型的歧义和无效上下文。

输出结构：

```json
{
  "operation": "SELECT",
  "signals": ["aggregate", "group_by", "time_range", "sort"],
  "his_concepts": ["门诊人次", "就诊科室", "挂号时间"],
  "explicit_tables": [],
  "requires_clarification": false,
  "clarification_reason": "",
  "warnings": []
}
```

### 6.2 本轮规则

- 通过“动作 + 对象”规则识别明确的新增、插入、更新、删除、建表、删表、授权和过程调用请求；确认是写操作时，在调用模型前返回 `422 UNSUPPORTED_OPERATION`。过滤条件中的“已删除”“更新时间”等普通字段语义不能仅因包含关键词而误判。
- 所有可继续处理的请求固定为只读查询，操作类型不再由关键词选择，更不能由用户提示词覆盖。
- 识别聚合、分组、排序、去重、排名、最近一次、首次、同比、环比、时间范围等信号，作为检索和提示词上下文。
- 识别用户显式写出的表名和字段名，并给 Schema RAG 增加确定性权重。
- 对“患者数/就诊人次”“申请时间/执行时间/报告时间”“入院时间/出院时间”等可能改变结果的歧义，通过 HIS 术语定义消解；无定义时返回澄清原因。
- 明显的提示词注入语句只产生告警；用户文本始终作为结构化数据字段传递。若请求只有“忽略规则、泄露提示词、改写策略”等指令且没有可识别的查询目标，则在远端调用前返回 `422 NO_QUERY_INTENT`。

意图分析只负责前置拒绝、检索增强和澄清，不能证明最终 SQL 合法；最终结论始终来自本地 AST 策略引擎。

## 7. HIS 语义目录

### 7.1 设计目标

Schema 的表名、字段名和注释通常不足以表达 HIS 业务语义。本地语义目录用于维护术语、同义词、定义和 SQL 提示，使远端模型只看到当前问题所需的少量解释。

### 7.2 数据模型

新增 `his_semantic_term`：

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | INTEGER | 主键 | 术语 ID |
| `db_id` | INTEGER | 可空外键 | 为空表示全局术语，否则仅用于指定 Schema 项目 |
| `term` | TEXT | 非空 | 标准术语 |
| `synonyms_json` | TEXT | 非空，默认 `[]` | 同义词 JSON 数组 |
| `definition` | TEXT | 非空 | 业务定义；进入提示词时始终作为不可信数据 |
| `category` | TEXT | 非空 | `entity/event/time/status/metric/relation` |
| `bindings_json` | TEXT | 非空，默认 `[]` | 结构化表字段绑定，元素含 `table`、`columns`、可选 `role` |
| `sql_hint` | TEXT | 非空，默认空 | 可选计算提示，仅作为不可信上下文，不参与强证据判定 |
| `enabled` | INTEGER | `0/1` | 是否参与检索 |
| `created_by` | INTEGER | 外键 | 创建管理员 |
| `created_at` | TEXT | 非空 | 创建时间 |
| `updated_at` | TEXT | 非空 | 更新时间 |

服务端对同一作用域内的标准术语做大小写无关去重。输入上限固定为：术语 `100` 字符；同义词最多 `20` 个、每个 `100` 字符；定义和 SQL 提示各 `2000` 字符；绑定最多 `20` 组、每组最多 `50` 个字段。`db_id=NULL` 的全局术语不得包含 Schema 绑定；数据库专属术语保存时必须校验每个绑定表字段均存在。

### 7.3 推荐的初始分类

- 实体：患者、就诊、住院、科室、病区、医嘱、处方、检验申请、检验结果、检查报告、诊断、费用明细、结算。
- 事件：挂号、接诊、入院、出院、下医嘱、执行、采样、报告、收费、退费。
- 时间：业务日期、挂号时间、医嘱时间、执行时间、采样时间、报告时间、入院时间、出院时间、结算时间。
- 状态：有效、作废、撤销、停止、退费、已审核、已报告。
- 指标：患者数、就诊人次、住院人次、平均住院日、次均费用、药占比、阳性率。
- 关系：一次就诊对应多个诊断、一次申请对应多个项目、医嘱与执行记录的对应关系。

本文不预置任何医院特有口径。管理员必须根据已导入 Schema 的字段和注释维护实际定义。

### 7.4 管理 API

- `GET /api/his-terms?db_id={id}&enabled={bool}`：管理员分页查询；生成流程内部可读取启用项。
- `POST /api/his-terms`：创建术语。
- `PUT /api/his-terms/{id}`：更新术语。
- `DELETE /api/his-terms/{id}`：删除术语。

新增独立的“语义目录”管理页，使用检索、分页、启用开关和编辑对话框，不把长文本塞进表格。

## 8. Schema 与语义检索

### 8.1 Schema RAG 改造

现有向量 + 关键词 + 外键扩展框架保留，但返回统一证据对象：

```json
{
  "table_name": "visit_record",
  "reasons": ["keyword", "vector"],
  "keyword_score": 10.0,
  "vector_similarity": 0.72,
  "vector_margin": 0.11,
  "evidence_score": 0.83,
  "matched_terms": ["门诊", "就诊时间"],
  "expanded_from": null
}
```

改造要求：

- 关键词检索返回原始加权分数；默认强证据阈值为 `6.0`。
- Chroma Schema 集合统一使用 cosine 距离，并记录 `index_version`；旧集合的距离口径或版本不匹配时自动重建。服务端按 `similarity = 1 - distance` 转换。
- Schema RAG 同步比较由表名、表注释、全部字段、字段注释、类型和关系规范化计算的 `content_hash`，不能继续使用“表名集合相同即跳过”的快速路径；任何元数据变化都刷新 SQLite 索引行和对应向量集合。
- 同一 `db_id` 的索引刷新使用进程内锁去重；Schema 快照读取和 SQLite 索引写入使用短事务，耗时的本地 embedding/Chroma 重建不持有 SQLite 写事务。向量刷新失败时保留关键词检索并报告 `vector_enabled=false`，不能回退到任意表。
- 向量结果单独成为强证据时，必须同时满足绝对相似度和第一名领先幅度，默认分别为 `0.65` 与 `0.08`；避免“查询 id”等通用短句错误命中。
- 用户显式提及已存在的表名或 `表.字段` 时设为强证据；显式标识不存在时直接产生澄清原因，不把相近表当作替代。
- 命中 HIS 术语且其 `bindings_json` 已通过当前 Schema 校验时，可作为术语强证据；自由文本定义和 `sql_hint` 本身不能直接获得强证据资格。
- `evidence_score` 只用于排序和展示，不替代上述强证据门槛：显式标识为 `1.0`，已校验术语映射为 `0.9`，关键词分量为 `min(keyword_score / 12, 1)`，向量分量为 `[0,1]` 内的相似度，主表取各分量最大值；外键扩展表取来源表分数乘 `0.85`。各原始证据仍须保留，便于解释和调参。
- 外键扩展只从强证据表出发，默认深度为 `1`；扩展表记录 `expanded_from`，其本身不能反向证明原始问题相关。
- 无表达到强证据门槛时，不再回退到前 N 张表，不调用模型；仍创建历史与追踪记录，并以 `200` 返回 `sql="NO_SQL"`、`validation_status="not_run"`、`model_calls=0` 和具体澄清原因。
- 传给模型的表数量由 `RAG_TOP_K` 限制，默认 `8`、最大 `20`；完整 Schema 只留在本地供 AST 校验。

建议新增配置：

- `RAG_MIN_KEYWORD_SCORE`，默认 `6.0`。
- `RAG_MIN_VECTOR_SIMILARITY`，默认 `0.65`。
- `RAG_MIN_VECTOR_MARGIN`，默认 `0.08`。
- `HIS_TERM_TOP_K`，默认 `8`。

默认值已使用仓库示例 Schema 和当前 `BAAI/bge-small-zh-v1.5` 做基线检查：“列出订单”的关键词分数为 `6`；“查询 id”对两张示例表的向量相似度约为 `0.60` 且领先幅度不足 `0.02`，因此不会凭向量单独越过门槛。新增或更换嵌入模型时必须运行固定召回测试集，不得直接沿用旧阈值。

### 8.2 HIS 术语检索

本轮采用本地关键词匹配，不为术语单独建立远端服务：

- 标准术语完整命中权重最高。
- 同义词完整命中次之。
- 中文 2-4 gram 与定义命中作为补充。
- 优先返回当前 `db_id` 专属术语，再补全局术语。
- 只返回得分大于零的前 `HIS_TERM_TOP_K` 项。

### 8.3 反馈 RAG 准入

管理员点击批准时必须重新执行：

1. 反馈方言必须与 `db_id` 的数据库定义一致。
2. 修正 SQL 必须通过目标方言解析、单语句、只读和副作用节点检查。
3. 表和字段必须存在于对应 `db_id` 的完整 Schema。
4. 自然语言必须包含可识别的查询目标；其文本始终作为示例数据，不能成为提示词指令。
5. 管理员提交反馈时的“自动批准”路径和管理员批准 API 必须调用同一个准入函数，不能绕过校验。
6. 检索旧反馈时按当前策略版本惰性重验；Schema 已变化或策略不再通过的记录保留在管理页，但不进入 RAG。
7. 通过后才允许 `approved=1` 并同步索引；失败返回 `400 FEEDBACK_VALIDATION_FAILED` 和结构化原因。

## 9. 提示词编译器

### 9.1 模块顺序

提示词编译为两个角色边界清晰的消息：

1. `system` 消息只包含代码内常量：`POLICY`、目标方言约束和 `OUTPUT_CONTRACT`。任何来自数据库、管理员或用户的文本都不能进入该消息。
2. `user` 消息只包含一个 JSON 对象，键顺序固定为 `prompt_version`、`intent`、`his_semantics`、`schema_evidence`、`verified_examples`、`user_request`。Schema 注释、术语定义、反馈和用户请求分别作为 JSON 字符串值，不使用可被内容闭合的手写分隔符。

编译器移除不允许的控制字符，使用 `json.dumps(..., ensure_ascii=False)` 序列化。`PROMPT_MAX_CHARS` 绝对上限为 `120000`，无显式配置时默认 `60000`；用户请求沿用 API 的 `4000` 字符上限，单条 Schema/术语注释进入提示词时最多 `2000` 字符。超预算时依次移除反馈样例、低分术语、低分外键扩展表，再移除非命中列的注释，但始终保留强证据表的全部列名和类型；仍超限时不调用模型，返回 `NO_SQL/CONTEXT_TOO_LARGE`。绝不截断 JSON。修复调用沿用同一系统消息，并把候选输出和结构化校验错误放入新的 JSON 数据字段。

### 9.2 版本管理

本轮不建设在线提示词编辑器。提示词模板保存在独立模块中，使用常量版本，例如 `his-sql-v1`。任何政策、模块顺序或输出契约变化必须提升版本并增加回归用例。`generation_trace.prompt_version` 记录实际版本。

### 9.3 输出契约

模型必须返回严格 JSON 对象；为兼容不同 OpenAI 兼容供应商，本轮不依赖供应商专有的 structured-output 参数，服务端仍做严格本地解析：

```json
{
  "sql": "SELECT ...",
  "reason": "",
  "assumptions": []
}
```

完整模型消息最多 `30000` 字符；`sql` 最多 `20000` 字符，`reason` 最多 `2000` 字符，`assumptions` 最多 `20` 项、每项最多 `500` 字符。`sql`、`reason` 必须是字符串，`assumptions` 必须是字符串数组，禁止额外字段。无法生成时返回 `sql="NO_SQL"` 和具体 `reason`；成功时 `reason` 必须为空。表、字段和校验状态全部由服务端 AST 推导，不接受模型自报结果。Markdown 围栏、从正文中搜索 JSON、纯 SQL 回退和类型强制转换全部取消。

### 9.4 一次生成优先

现有“开启深度思考后固定调用两次模型”改为：

1. 调用一次生成模型。
2. 本地 AST 校验通过则立即返回。
3. JSON 契约或 SQL 策略校验失败且开启自动修复时，将结构化错误和原候选输出作为 JSON 数据发送给同一远端模型修复一次；网络错误、超时、无强检索证据和明确写操作不触发修复。
4. 修复结果重新走完整本地校验；仍失败则返回 `NO_SQL`。

该策略在正常请求上减少一次远端调用。现有 `enable_thinking` 配置字段为兼容数据库继续保留，前端文案改为“校验失败后自动修复”。

状态语义固定如下：远端调用前因无强证据或上下文超限返回 `NO_SQL` 时为 `not_run/0 次`；模型主动返回 `NO_SQL` 时为 `not_run/1 次`；候选 SQL 校验通过时为 `passed/1 次`；候选失败且修复后仍无法通过时为 `failed/2 次`，并返回最后一次结构化错误。连续两次都不符合 JSON 契约时返回 `502 MODEL_RESPONSE_INVALID`，只在 trace 中记录 `outcome=error`，不伪造成功响应。

## 10. SQL AST 策略引擎

### 10.1 解析与允许范围

使用当前已实测的 `sqlglot>=30.14,<31`，将产品方言 `pg` 映射为解析方言 `postgres`，其余使用 `mysql/oracle`。策略版本从 `sql-policy-v1` 开始，策略顺序如下：

1. 使用 `sqlglot` Tokenizer/AST 的 `comments` 属性识别真实注释；拒绝注释，但不能误伤字符串字面量中的 `--` 或 `/* */`。
2. `sqlglot.parse` 必须成功并得到恰好一个非空表达式；允许一个可选的结尾分号，不允许多语句。
3. 根节点必须满足 `isinstance(root, exp.Query)`，因此允许 `SELECT`、带 CTE 的查询、`UNION`、`INTERSECT`、`EXCEPT`，以及 Oracle 输入中的 `MINUS`。
4. 遍历整棵 AST，拒绝任意层级中的 `INSERT`、`UPDATE`、`DELETE`、`MERGE`、`CREATE`、`ALTER`、`DROP`、`TRUNCATE`、`COPY`、事务、过程调用和命令节点；这会拦截 PostgreSQL 数据修改 CTE 等“外层 SELECT、内层写入”结构。
5. 额外拒绝有副作用或锁行为的查询节点：`SELECT ... INTO`、`FOR UPDATE/SHARE`、会话变量赋值和文件输出；解析失败的供应商扩展语法同样不能通过。
6. 从当前 `db_id` 的完整 Schema 构造 `MappingSchema`，列类型统一使用 `UNKNOWN` 占位，只校验表字段存在性，避免医院厂商自定义类型导致类型解析误报。在 AST 副本上调用 `sqlglot.optimizer.qualify.qualify(..., expand_stars=False, identify=False, validate_qualify_columns=True)`，再使用 `traverse_scope` 区分物理表、CTE、派生表、表别名和关联子查询；校验副本绝不用于返回 SQL。每个物理表必须存在，CTE 名和派生表别名不能误当物理表。
7. 将 qualifier 错误稳定映射为 `UNKNOWN_TABLE`、`UNKNOWN_COLUMN`、`AMBIGUOUS_COLUMN` 或 `UNKNOWN_ALIAS`。限定字段必须解析到当前作用域中的来源；非限定字段零次命中为未知，多次命中为歧义；`alias.*` 的别名也必须存在。没有任何可见来源的裸 `*` 报 `UNBOUND_STAR`。`MappingSchema` 按目标方言规范化未加引号标识符，加引号标识符保持精确匹配。已实测该路径可处理普通 JOIN、CTE 投影、派生表和关联子查询。
8. 拒绝文件、网络、远端数据库、系统命令、锁、序列写入和延时函数，例如 `LOAD_FILE`、`PG_READ_FILE`、`DBLINK_CONNECT`、`UTL_HTTP.*`、`XP_CMDSHELL`、`NEXTVAL`、`SETVAL`、`PG_ADVISORY_LOCK`、`GET_LOCK`、`SLEEP`、`BENCHMARK`、`PG_SLEEP`。函数名按 AST 标准名、`Anonymous` 名称和 `Dot` 完整名检查；Oracle 物理表标识符包含 `@` 时按 database link 拒绝。Oracle `DUAL` 是唯一可配置的虚拟表例外，不作为 Schema 强证据。
9. AST 只用于判定和提取引用，校验通过后返回模型原始 SQL 的去首尾空白版本，不调用 `tree.sql(...)` 重写。实测 Oracle `MINUS` 会被 `sqlglot` 输出器改写为 `EXCEPT`，因此转译结果不能作为用户结果。

### 10.2 告警而非拒绝

以下情况返回告警，但允许 SQL：

- `SELECT *`。
- 没有筛选条件且任一物理表字段数不少于 `20` 的宽表查询。
- 未使用任何 RAG 强证据表，但表确实存在于完整 Schema。
- 模型声明的表字段与 AST 实际结果不一致。

由于系统不执行 SQL，本轮不强制添加 `LIMIT`、`TOP` 或 `FETCH`，避免改变用户要求。

### 10.3 校验结果

```json
{
  "status": "passed|failed",
  "errors": [{"code": "UNKNOWN_TABLE", "message": "..."}],
  "warnings": [{"code": "SELECT_STAR", "message": "..."}],
  "tables": ["visit_record"],
  "columns": ["visit_record.visit_time"],
  "validated_sql": "SELECT ..."
}
```

`validated_sql` 保留原方言写法，只移除首尾空白。前端只在 `status=passed` 时显示“本地校验通过”。任何模型自述都不能产生该状态。

## 11. 远端模型网关与配置

### 11.1 模型网关

保留 OpenAI 兼容 `/chat/completions` 接口，并集中处理：

- URL 规范化、认证头、超时和错误映射。
- 请求阶段名、模型名、耗时、HTTP 状态和供应商 request ID。
- 供应商返回的 usage 字段（可用时）。
- 严格禁止记录 Authorization、API Key、完整提示词和原始模型响应；供应商错误正文不直接返回浏览器，只返回稳定错误码、HTTP 状态和可用的供应商 request ID。
- 单次请求总超时由现有 `thinking_timeout_seconds` 控制，默认及最大值均为 600 秒；管理员可在 10-600 秒内调整。

网关每次调用返回内部 `ModelCallResult`：严格解析后的候选对象、供应商 request ID、HTTP 状态、耗时、`prompt_tokens`、`completion_tokens`。usage 缺失时令牌字段为 `None`，不能用字符数伪装为供应商令牌数。`model_calls` 在实际发起 HTTP 请求前递增，因此超时和供应商错误也计为一次。编排层汇总两次调用的 usage 和总耗时；原始响应仅在当前函数内完成解析，不写库、不向上层长期传递。

### 11.2 API Key 契约

`GET /api/config` 不返回完整 API Key，改为：

```json
{
  "api_key_configured": true,
  "api_key_last4": "abcd",
  "base_url": "https://example/v1",
  "model_name": "model-name",
  "enable_thinking": true,
  "thinking_timeout_seconds": 600,
  "rag_embedding_model": "BAAI/bge-small-zh-v1.5",
  "rag_top_k": 8,
  "rag_expand_depth": 1
}
```

`api_key_last4` 只用于帮助管理员确认当前配置，不得回填 API Key 输入框。`PUT /api/config` 中 `api_key: str | null` 可选：省略、`null` 或空字符串都表示保留已有值，非空才替换。后端拆分 `get_model_runtime_config()` 与 `get_model_config_view()`：前者仅供服务端远端调用，后者在类型层面不含完整 Key，避免依赖 Pydantic 忽略额外字段来防泄露。密钥内部仍可来自环境变量或现有 SQLite 配置。

## 12. 数据库变更

### 12.1 新增表 `his_semantic_term`

按第 7.2 节定义创建，并增加 `db_id`、`enabled` 索引。数据库迁移必须幂等，兼容现有 SQLite 文件。

### 12.2 新增表 `generation_trace`

| 字段 | 类型 | 说明 |
|---|---|---|
| `request_id` | TEXT 主键 | API 请求 ID |
| `history_id` | INTEGER 可空外键 | 成功写历史后关联 |
| `user_id` | INTEGER 外键 | 请求用户 |
| `db_id` | INTEGER 外键 | Schema 项目 |
| `prompt_version` | TEXT | 提示词版本 |
| `policy_version` | TEXT | SQL 策略版本 |
| `context_hash` | TEXT | 规范化动态上下文的 SHA-256，不含 API Key |
| `model_name` | TEXT | 远端模型 |
| `retrieval_mode` | TEXT | Schema 检索模式 |
| `retrieved_tables_json` | TEXT | 表、原因和分数 |
| `retrieved_terms_json` | TEXT | 术语 ID、词项和分数 |
| `policy_status` | TEXT | `passed/failed/not_run` |
| `validation_errors_json` | TEXT | 结构化校验错误 |
| `warnings_json` | TEXT | 结构化告警 |
| `model_calls` | INTEGER | `0-2` |
| `outcome` | TEXT | `passed/no_sql/error` |
| `error_code` | TEXT | 可空稳定错误码 |
| `duration_ms` | INTEGER | 总耗时 |
| `prompt_chars` | INTEGER | 发送字符数 |
| `prompt_tokens` | INTEGER 可空 | 供应商 usage |
| `completion_tokens` | INTEGER 可空 | 供应商 usage |
| `created_at` | TEXT | UTC 时间 |

追踪表不保存完整提示词和原始模型响应。`history_id` 使用 `ON DELETE SET NULL`；追踪记录与历史使用相同保留期。历史和追踪清理在应用启动及生成写入时节流执行，不再只依赖历史列表读取。

生成编排不能在远端 HTTP 等待期间持有 SQLite 事务。前置检索完成后关闭读连接；远端调用结束后用短事务写历史与追踪。对 200 `NO_SQL` 仍写历史并关联 trace；对 HTTP/网络异常至少写一条 `history_id=NULL`、`outcome=error` 的 trace。trace 写入失败只记录本地错误，不能覆盖原始业务异常。

### 12.3 API 响应扩展

`GenerateSqlResponse` 保留现有字段，并新增：

- `request_id: str`
- `prompt_version: str`
- `policy_version: str`
- `no_sql_code: str`，成功时为空；否则为 `LOW_SCHEMA_EVIDENCE`、`CONTEXT_TOO_LARGE`、`MODEL_DECLINED` 或 `VALIDATION_FAILED`
- `validation_status: passed|failed|not_run`
- `validation_errors: list[ValidationIssue]`
- `warnings: list[ValidationIssue]`
- `assumptions: list[str]`
- `retrieved_evidence: list[RetrievalEvidence]`
- `retrieved_terms: list[RetrievedTerm]`
- `model_calls: int`

原有前端和调用方只读取旧字段时仍可工作。

其中 `ValidationIssue` 至少包含 `code`、`message`；`RetrievalEvidence` 使用第 8.1 节结构；`RetrievedTerm` 至少包含 `id`、`term`、`category`、`scope`、`score`。低证据返回仍保留现有必填 `history_id: int`，避免把兼容字段改为可空。

## 13. 前端设计

### 13.1 SQL 工作台

- 目标方言由所选数据库定义自动确定，控件改为只读展示，避免前后端不一致。
- 结果区显示本地校验状态、请求 ID、提示词版本和模型调用次数。
- 告警使用列表展示；校验失败不提供复制按钮。
- 模型声明的假设单独展示为“生成假设”，不能与本地校验状态混在一起。
- 命中证据显示表名、来源和分数；命中术语使用紧凑标签或定义列表。
- 将“深度思考”相关提示改成“本地校验失败时最多自动修复一次”。

### 13.2 大模型配置

- API Key 输入框始终为空，旁边显示“已配置/未配置”和尾四位；尾四位仅作状态提示，不写入表单模型。
- 留空保存表示不更换密钥。
- “启用深度思考”改名为“校验失败后自动修复”。
- 保留最大等待秒数 `10-600`。

### 13.3 HIS 语义目录

- 新增管理员路由和导航入口。
- 页面提供数据库范围、类别、启用状态和关键词筛选。
- 列表只显示短字段；定义、同义词、结构化表字段绑定和 SQL 提示在编辑对话框中完整展示。数据库专属术语的绑定使用表/字段选择器，不要求管理员手写 JSON。
- 删除需要二次确认；启用状态可直接切换并显示失败原因。

## 14. 错误语义

| HTTP | 场景 | 示例错误码 |
|---|---|---|
| 400 | 方言不匹配、反馈批准校验失败、配置非法 | `DIALECT_MISMATCH` |
| 401 | 未登录或令牌失效 | `UNAUTHENTICATED` |
| 403 | 非管理员操作语义目录或配置 | `FORBIDDEN` |
| 404 | 数据库定义或术语不存在 | `NOT_FOUND` |
| 422 | 明确写操作、无查询意图或请求无法规范化 | `UNSUPPORTED_OPERATION`、`NO_QUERY_INTENT` |
| 502 | 远端返回无法解析的结构 | `MODEL_RESPONSE_INVALID` |
| 504 | 总等待时间耗尽 | `MODEL_TIMEOUT` |

本轮可继续使用 FastAPI `detail` 字符串保持兼容，但新增逻辑内部应使用稳定错误码；前端优先显示可读消息。

## 15. 自动化测试设计

### 15.1 单元测试

- `test_intent.py`：写操作拒绝、只读信号、歧义与注入告警。
- `test_his_semantics.py`：作用域、同义词、得分、去重和禁用项。
- `test_prompting.py`：角色边界、模块顺序、版本、JSON 转义、裁剪顺序、`CONTEXT_TOO_LARGE` 和恶意注释隔离。
- `test_sql_policy.py`：三个方言、CTE、集合查询、嵌套 DML、`SELECT INTO`、锁、变量赋值、真实注释与字符串伪注释、未知/歧义字段、裸星号、自定义字段类型、database link、危险函数、别名和告警；Oracle `MINUS` 通过后仍保留原文本。
- `test_rag_evidence.py`：完整内容哈希刷新、关键词阈值、cosine 转换、向量绝对值与领先幅度、显式标识、术语映射、外键扩展和零证据拒绝。
- `test_model_config_secret.py`：读取模型在类型上不含完整密钥、仅返回状态与尾四位、留空保留、非空替换。

### 15.2 API 与流程测试

- `test_generate_sql_api.py` 使用 mock 远端网关覆盖完整编排，不访问外部网络。
- 低相关性请求不调用远端模型并返回具体原因。
- 合法 SQL 一次模型调用后返回 `passed`。
- 首次结果非法时最多执行一次修复；修复仍失败返回 `NO_SQL`。
- 首次 JSON 契约非法时最多执行一次修复；第二次仍无法解析则返回 `502 MODEL_RESPONSE_INVALID`。
- 模型声称“已验证”但 AST 非法时仍被拒绝。
- 目标方言与数据库定义不一致时不调用模型。
- 非法反馈不能批准或同步到反馈 RAG。
- `generation_trace` 覆盖通过、`NO_SQL` 和远端异常，写入请求 ID、版本、证据、调用次数、状态和耗时，不写完整提示词或原始响应。
- 现有认证、反馈、请求上限测试继续通过。

### 15.3 前端验证

- `pnpm build` 必须通过。
- 增加纯函数单元测试，覆盖配置响应映射和保存载荷生成；测试必须证明尾四位不会进入 `api_key`。
- 配置页不会把尾四位或任何状态文本作为新密钥提交。
- 校验失败时复制按钮禁用。
- 语义目录增删改、筛选、启停和错误状态可用。
- 桌面与窄屏下文字、按钮和表格不重叠。

## 16. 迁移与兼容策略

1. 新表和索引同时加入 `SCHEMA_SQL`，利用现有 `CREATE ... IF NOT EXISTS` 路径完成幂等 SQLite 迁移，不修改旧记录含义。
2. 新响应字段全部提供默认值，保持旧前端可读取。
3. 旧 `enable_thinking` 键保留，只改变运行语义和 UI 文案。
4. 旧反馈在被检索或批准时执行惰性重新校验；不合法记录保留但不进入 RAG。
5. 首次没有 HIS 术语时，系统仍可使用 Schema RAG；界面提示管理员补充口径。
6. RAG 阈值上线初期记录拒绝率，根据测试 Schema 调整，不允许以恢复“全量表回退”解决召回问题。
7. Schema 向量集合增加版本和 cosine 距离元数据；现有集合版本不匹配时重建，SQLite Schema 元数据不受影响。
8. 配置接口移除完整 `api_key` 是有意的安全契约变更，前后端必须同批发布；旧客户端的非空 Key 更新请求仍兼容。

## 17. 实施分解

| 阶段 | 主要产物 | 完成条件 |
|---|---|---|
| A. 本地生成内核 | 意图分析、提示词编译、SQL 策略、一次生成优先 | 单元测试覆盖主要方言和攻击用例 |
| B. 语义与检索 | 语义表/API、术语检索、Schema 阈值和证据 | 低证据不外呼，术语可影响上下文 |
| C. 配置与追踪 | Key 非回显、trace 表、响应扩展、历史清理 | 响应无完整 Key，每次生成可追踪 |
| D. 前端体验 | 工作台校验状态、配置改造、语义目录页 | 构建通过，核心流程可操作 |
| E. 集成与回归 | API 流程测试、旧测试回归、文档同步 | 全部自动化检查通过 |

## 18. 上线验收门槛

必须同时满足：

| 门槛 | 验收要求 | 直接证据 |
|---|---|---|
| G1 | DML、DDL、多语句、数据修改 CTE、副作用 SELECT 均不能获得 `passed` | `tests/test_sql_policy.py` |
| G2 | 未知表、未知字段、歧义字段均不能获得 `passed` | `tests/test_sql_policy.py` |
| G3 | 合法 SQL 在自动修复开启时仍只调用一次模型；任何请求最多两次 | `tests/test_generation_flow.py` 的调用计数断言 |
| G4 | 无强 Schema 证据时不调用模型，返回可操作原因、`history_id` 和 `model_calls=0` | `tests/test_rag_evidence.py`、API 流程测试 |
| G5 | 恶意表注释、术语定义、反馈和用户文本不能改变 system 消息或 JSON 字段边界 | `tests/test_prompting.py` 的消息快照和反序列化断言 |
| G6 | 管理员自动批准、批准 API、旧反馈检索三条路径都不能让非法 SQL 进入 RAG | `tests/test_feedback_policy.py` |
| G7 | 配置 API、浏览器状态和前端表单均无法读取或回提交完整 API Key | `tests/test_model_config_secret.py`、前端构建与组件测试 |
| G8 | 通过、`NO_SQL` 和异常请求都有 request ID/版本/调用次数 trace；trace 无完整提示词和原始响应 | `tests/test_generation_trace.py` |
| G9 | Oracle `MINUS` 等合法方言语法经校验后不被转译重写 | `tests/test_sql_policy.py` 原文相等断言 |
| G10 | 项目只连接本地 SQLite 元数据库；无目标 DB 驱动、连接/试连/执行路由 | `tests/test_architecture_boundary.py` 检查依赖和 FastAPI 路由，最终 `rg` 人工复核 |
| G11 | 现有 Python 测试、新增测试和前端生产构建全部通过 | 完整测试命令与构建日志 |

## 19. 代码变更地图

预计新增：

- `backend/intent.py`
- `backend/his_semantics.py`
- `backend/prompting.py`
- `backend/sql_policy.py`
- `backend/generation.py`（生成编排，可按实现复杂度合并）
- `tests/test_intent.py`
- `tests/test_his_semantics.py`
- `tests/test_prompting.py`
- `tests/test_sql_policy.py`
- `tests/test_model_config_secret.py`
- `tests/test_generation_flow.py`
- `tests/test_generate_sql_api.py`
- `tests/test_rag_evidence.py`
- `tests/test_feedback_policy.py`
- `tests/test_generation_trace.py`
- `tests/test_architecture_boundary.py`
- `frontend/src/views/admin/AdminHisSemanticsView.vue`
- `frontend/src/utils/modelConfig.js`
- `frontend/tests/modelConfig.test.js`

预计修改：

- `backend/database.py`：新增表和迁移。
- `backend/schemas.py`：术语、追踪、校验和配置响应模型。
- `backend/crud.py`：术语 CRUD、追踪、Key 非回显视图和历史清理。
- `backend/rag.py`：证据得分、阈值、术语与反馈准入。
- `backend/llm.py`：使用提示词包，返回 usage，移除固定双调用编排。
- `backend/main.py`：生成编排、语义 API 和方言一致性。
- `frontend/src/api/index.js`、路由、导航、工作台、配置页与 `package.json` 测试脚本。

现有 `build.cmd`、`start.cmd` 和 `scripts/sqlgenie_runtime.cmd` 含用户未提交改动，除非实现确实需要，否则不得修改或覆盖。

## 20. 设计假设与二次评审结论

已确认假设：

- SQLGenie 只管理 Schema 元数据和关系，不连接真实数据库。
- 本地 SQLite 只保存 SQLGenie 自身元数据；目标数据库连接和 SQL 执行能力永久不在本轮架构中。
- 生成结果供人工复制和进一步验证，界面不能标记为已在目标数据库执行。
- 远端模型必须继续使用 OpenAI 兼容接口。

二次审查已确认：

- `sqlglot 30.14.0` 已实测 Oracle `MINUS`、PostgreSQL DML CTE、`Into`、`Lock`、MySQL 变量赋值、注释识别和作用域 qualifier；结论已写入策略与测试清单。
- RAG 默认关键词阈值、向量绝对值和领先幅度已有仓库示例正负例基线；更换 embedding 模型必须重新跑固定召回集。
- HIS 语义目录保留为本轮核心能力，但使用结构化绑定，不能让自由文本提示直接成为强证据。
- API Key 接口变更采用前后端同批发布；旧反馈惰性重验，旧 Schema 元数据原样保留。
- G1-G11 每个验收门槛均有直接自动化测试或明确的可复现人工证据。
- 编码可以开始；二次审查记录见 `docs/HIS_REMOTE_LLM_DEVELOPMENT_DESIGN_REVIEW.md`。
