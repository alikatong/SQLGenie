# 模型思考强度与上下文上限设计

## 目标

在系统配置中提供独立的模型思考强度设置，并确保每次后端模型调用都使用该设置；同时将模型提示上下文的最大允许长度统一设为 120000 个字符。

## 约束与兼容性

- 保留现有 `enable_thinking` 语义。它表示本地校验失败后是否允许一次自动修复调用，不改名、不复用。
- 新增可选 `reasoning_effort`，取值为 `low`、`medium`、`high`、`xhigh`、`max`；默认不发送该参数（界面显示“跟随模型/未设置”），以免升级后破坏不支持该扩展参数的既有通用模型。管理员显式选择后才启用强度控制。
- 配置更新区分“字段未提供”和显式选择 `medium`：旧版客户端省略该字段时保留数据库中的既有值，避免无感覆盖 `high`。
- 设置写入现有 SQLite `app_config`，环境变量 `LLM_REASONING_EFFORT` 只提供默认值。
- 发往 OpenAI-compatible Chat Completions 的请求在显式设置时增加 `reasoning_effort`；旧数据库缺少该键时保持未设置。服务商不支持该参数时返回明确的上游配置错误，不自动重试或静默丢弃，以免用户误以为强度设置已生效。推理模型若不接受 `temperature`，由请求构造按模型能力省略该字段。
- 上下文默认值仍为 60000，最大值统一为 120000；`MODEL_MESSAGE_MAX_CHARS` 继续表示无显式配置时的 60000 默认值，`PROMPT_MAX_CHARS` 表示绝对上限 120000。后端 schema、运行时读取/裁剪、prompting 常量、前端归一化和表单校验必须一致。
- 不在响应中暴露 API Key；新增字段只属于非敏感配置。

## 数据流

```text
.env -> Settings -> default_model_config
                    |
SQLite app_config <- ConfigUpdate <- AdminConfigView
                    |
              get_model_runtime_config
                    |
          request_model_candidate -> reasoning_effort
```

## 失败策略

- Pydantic 对思考强度做枚举校验，非法值在配置更新阶段拒绝。
- 运行时读取历史非法值时回退到默认值，不让坏配置进入模型请求。
- 运行时读取历史非法 `reasoning_effort` 时回退到未设置；历史越界 `prompt_max_chars` 按默认值/上限规范化后再返回配置视图。
- `prompt_max_chars` 在所有入口限制为 `1000..120000`，超限仍按已有逻辑裁剪或返回 `CONTEXT_TOO_LARGE`。
- 不做远端探测或自动重试；只有管理员显式启用后才依赖服务商的 `reasoning_effort` 扩展。配置说明标注该兼容要求，未启用时保持原有请求格式。
- 后端实际服务页面时若使用 `frontend/dist`，构建产物必须同步更新并通过页面/构建验证。

## 验收标准

1. GET/PUT `/api/config` 能读写并返回 `reasoning_effort`。
2. 请求模型时 JSON payload 包含选定的 `reasoning_effort`。
3. 默认配置和旧数据库兼容，默认不发送思考强度参数；显式保存后按选择值发送。
4. 120000 在后端和前端所有校验及归一化路径生效。
5. 新增单元测试覆盖五档枚举（含 `xhigh`、`max`）、持久化、请求 payload、上下限和前端映射；既有测试保持通过。
