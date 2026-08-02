# 模型思考强度与上下文上限实现计划

> **面向 AI 代理的工作者：** 使用 `superpowers:subagent-driven-development` 并行执行下列互不重叠的任务；每项完成后运行对应测试。

**目标：** 在管理员系统配置中增加 `reasoning_effort`，贯通 SQLite、运行时模型请求和前端表单，并把上下文最大值统一设为 120000。

**技术栈：** FastAPI、Pydantic、SQLite、Vue 3、Element Plus、pytest、Node test。

### 任务 1：后端配置与模型请求

**文件：** `backend/config.py`、`backend/schemas.py`、`backend/crud.py`、`backend/llm.py`、`backend/generation.py`、`backend/prompting.py`、`.env.example`。

- 增加可选 `reasoning_effort` 的环境默认、枚举校验、SQLite 读写和配置视图返回；默认不发送该参数，旧版 PUT 省略字段时保留已有数据库值，脏值读取时回退到未设置。
- 请求 payload 发送规范化后的 `reasoning_effort`。
- 将 prompt 绝对上限与运行时裁剪上限设为 120000，保留无配置默认 60000。
- 用回归测试验证默认值、持久化、非法值和 payload。

### 任务 2：前端配置表单与映射

**文件：** `frontend/src/utils/modelConfig.js`、`frontend/src/views/admin/AdminConfigView.vue`、`frontend/tests/modelConfig.test.js`。

- 增加思考强度选择控件，默认跟随模型（未设置），支持 `low`、`medium`、`high`、`xhigh`、`max`。
- 将映射、payload 和输入校验上限统一到 120000。
- 若后端通过静态目录提供前端，重新构建 `frontend/dist` 并验证产物包含新控件和 120000 上限。
- 更新 Node 测试覆盖五档枚举（含 `xhigh`、`max`）和上下限归一化。

### 任务 3：集成复审与验证

**文件：** 不预先修改；审查任务 1/2 的 diff 和测试结果。

- 检查旧配置回退、敏感字段边界、OpenAI payload 和所有旧上限残留。
- 运行后端定向测试、前端测试和必要的全量回归。
