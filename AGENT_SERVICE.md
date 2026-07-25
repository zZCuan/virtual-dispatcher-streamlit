# 三级智能体服务接入

现有 Streamlit 页面默认继续使用内置 SQLite，页面布局和交互不变。

启动独立智能体 API：

```powershell
uvicorn agent_service:app --host 0.0.0.0 --port 8000
```

让 Streamlit 切换到 API 模式：

```text
DISPATCH_AGENT_API_URL=http://127.0.0.1:8000
DISPATCH_AGENT_API_TOKEN=<与服务端相同的令牌>
```

这两个值既可以作为环境变量，也可以写入 Streamlit Cloud 的 Secrets。

服务端配置：

```text
AGENT_SERVICE_DB_PATH=/tmp/dispatch_agent_service.db
AGENT_SERVICE_TOKEN=<令牌>
```

未配置 `DISPATCH_AGENT_API_URL` 时，系统自动使用原有本地模式，可随时回退。

当前统一接口包括：

- `POST /v1/agents/heartbeat`
- `GET /v1/agents/online`
- `POST /v1/tickets`
- `GET /v1/tickets`
- `POST /v1/tickets/{id}/ack`
- `POST /v1/tickets/{id}/forward`
- `POST /v1/tickets/{id}/execute`

下一阶段可在这些接口后接入 Redis Streams、PostgreSQL 和真正的大模型智能体，
Streamlit 前端无需改版。
