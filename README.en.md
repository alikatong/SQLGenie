# SQLGenie

SQLGenie is a local natural-language-to-SQL workbench. Administrators maintain database metadata, users describe queries in natural language, and RAG supplies relevant schema context and approved SQL feedback examples.

[中文 README](README.md)

## Interface preview

![SQL workbench](docs/images/sql-workbench.png)
![User management](docs/images/user-management.png)
![Schema management](docs/images/schema-management.png)
![Schema preview](docs/images/schema-preview.png)
![Query history](docs/images/query-history.png)
![Model configuration](docs/images/model-configuration.png)
![Feedback RAG management](docs/images/feedback-rag-management.png)

## Features

- JWT authentication with administrator and regular-user roles.
- Database definitions and JSON schema metadata management.
- SQL generation for MySQL, PostgreSQL, and Oracle.
- ChromaDB and `BAAI/bge-small-zh-v1.5` schema retrieval with foreign-key expansion and keyword fallback.
- Feedback is indexed for cross-user RAG only after administrator approval.
- Seven-day SQL history retention with administrator filters.

## Requirements and configuration

- Python 3.10+
- Node.js 20 LTS+
- pnpm 9+ (`corepack enable` can activate it)
- An OpenAI-compatible Chat Completions endpoint and API key

```bash
cp .env.example .env
```

Set `APP_HOST=127.0.0.1`, a unique long `SECRET_KEY`, a strong `ADMIN_PASSWORD`, `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` in `.env`. The configured administrator is created on first startup.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\\Scripts\\Activate.ps1
pip install -r backend/requirements.txt
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm dev
```

Open <http://127.0.0.1:5173>. Vite proxies `/api` to the backend. API docs are at <http://127.0.0.1:8000/docs>.

## Windows scripts

```bat
build.cmd
start.cmd --no-browser
restart.cmd rebuild --no-browser
stop.cmd
```

After a production build, FastAPI serves `frontend/dist` at <http://127.0.0.1:8000/>.

## Production deployment

Keep Uvicorn on localhost and put Nginx or another TLS reverse proxy in front:

```bash
git clone https://github.com/alikatong/SQLGenie.git /opt/sqlgenie
cd /opt/sqlgenie
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm build
cd ..
PYTHONPATH=/opt/sqlgenie .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

For LAN exposure, set `APP_HOST=0.0.0.0` only after setting strong credentials. Startup rejects known default credentials on a non-loopback listener. Set `CORS_ORIGINS` to exact trusted origins; do not use wildcard origins with credentialed requests.

## Local data and backups

Do not commit `.env`, `sqlgenie.db`, `.chroma/`, `models/`, logs, Python packages, Node modules, or `frontend/dist/`. These are excluded by `.gitignore`.

```bash
tar -czf sqlgenie-backup-$(date +%F).tgz sqlgenie.db .chroma .env
```

Back up these files before upgrades, then pull the source, update dependencies, rebuild the frontend, and restart the service.

## Examples and tests

- [Full schema upload](examples/schema-upload.json)
- [Single-table upload](examples/single-table-upload-orders.json)

```bash
PYTHONPATH=. python -m unittest discover -s tests -v
cd frontend && pnpm build
```
