# SQLGenie

本地化自然语言转 SQL 工作台。管理员维护数据库结构元数据，普通用户用自然语言生成 MySQL、PostgreSQL 或 Oracle SQL；RAG 会提供相关表结构和已审核反馈示例。

[English README](README.en.md)

## 界面预览

### SQL 生成工作台
![SQL 生成工作台](docs/images/sql-workbench.png)

### 用户管理
![用户管理](docs/images/user-management.png)

### 表结构管理
![表结构管理](docs/images/schema-management.png)

### 表结构预览
![表结构预览](docs/images/schema-preview.png)

### 提问历史
![提问历史](docs/images/query-history.png)

### 大模型配置
![大模型配置](docs/images/model-configuration.png)

### 反馈 RAG 管理
![反馈 RAG 管理](docs/images/feedback-rag-management.png)

## 功能

- JWT 登录、管理员和普通用户角色。
- 管理员创建普通用户、维护数据库定义和表结构 JSON。
- SQL 生成支持 MySQL、PostgreSQL 和 Oracle。
- ChromaDB + `BAAI/bge-small-zh-v1.5` 表结构检索，支持外键扩展和关键词回退。
- SQL 反馈需要管理员审核后，才会进入跨用户 RAG 示例库。
- 提问历史保留最近 7 天，管理员可按用户和日期查询。
- SQLite 保存应用数据，Chroma 保存向量索引。

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 20 LTS+
- pnpm 9+（可使用 `corepack enable`）
- OpenAI 兼容的 Chat Completions 接口和 API Key

### 配置环境变量

```powershell
Copy-Item .env.example .env
```

编辑 `.env`：

```dotenv
APP_HOST=127.0.0.1
APP_PORT=8000
SECRET_KEY=<唯一且足够长的随机字符串>
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<强密码>
LLM_API_KEY=<模型服务 API Key>
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

首次启动会创建配置的管理员账号。生产环境不要使用示例默认密码。

### 本地开发

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
```

终端一启动后端：

```powershell
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

终端二启动前端：

```powershell
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm dev
```

打开 <http://127.0.0.1:5173>；Vite 会将 `/api` 转发到后端。API 文档位于 <http://127.0.0.1:8000/docs>。

### Windows 一键脚本

```bat
build.cmd
start.cmd --no-browser
restart.cmd rebuild --no-browser
stop.cmd
```

生产前端构建后由 FastAPI 提供 <http://127.0.0.1:8000/>。

## 局域网和生产部署

默认只监听 `127.0.0.1`。只有设置强随机 `SECRET_KEY` 和 `ADMIN_PASSWORD` 后，才应设置 `APP_HOST=0.0.0.0`；应用会拒绝已知默认凭据的网络启动。

Linux 部署示例：

```bash
git clone https://github.com/alikatong/SQLGenie.git /opt/sqlgenie
cd /opt/sqlgenie
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
# 编辑 .env 后构建前端
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm build
cd ..
PYTHONPATH=/opt/sqlgenie .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

生产环境建议让 Uvicorn 继续监听 localhost，再由 Nginx 或其他反向代理负责 HTTPS：

```nginx
server {
    listen 443 ssl;
    server_name sqlgenie.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

将 `CORS_ORIGINS` 配置为明确的前端来源，不要对带凭据的请求使用通配符来源。

## 数据、备份和升级

以下是本地运行数据，不会提交到 Git：`.env`、`sqlgenie.db`、`.chroma/`、`models/`、`*.log`、`.python_packages/`、`frontend/node_modules/` 和 `frontend/dist/`。

停止服务后备份：

```bash
tar -czf sqlgenie-backup-$(date +%F).tgz sqlgenie.db .chroma .env
```

升级前先备份，再拉取代码、更新依赖、构建前端并重启。不要用 Git 文件覆盖 `.env`、数据库或向量索引。

## 示例和测试

- [完整表结构导入](examples/schema-upload.json)
- [单表导入](examples/single-table-upload-orders.json)

```powershell
$env:PYTHONPATH = "$PWD\.python_packages"
python -m unittest discover -s tests -v
cd frontend
pnpm build
```
