# 摘要：HTTP API / FastAPI Gateway（ADR-042 / ROADMAP N47）

> 客户端/服务器解耦接入层。落地 ROADMAP P3 待办「HTTP API / FastAPI Gateway（客户端/服务器解耦，接入层扩展）」，接入层策略沿用 ADR-011。

## 1. 背景与目标

chuan-os 此前接入层有 CLI / TUI / 语音 / 微信，缺一条**不依赖终端/语音的编程式通道**。本改动新增 FastAPI 网关，让脚本 / 网页 / PWA 等客户端通过 HTTP 与 RuntimeSupervisor 对话，与既有交互方式解耦。

**硬约束**：只新增 `chuan/gateway/api.py` 相关新文件；不动现有 core（orchestrator / runtime_supervisor）。

## 2. ADR-042 决策点

- **生命周期**：`create_app()` 工厂 + FastAPI lifespan。复用传入的 `supervisor`（测试用，不管理生命周期）或默认用 `RuntimeSupervisor(config_path=...)` 创建并 `wake_up()`，关闭时 `shutdown()`。模块级 `app = create_app()` 供 `uvicorn chuan.gateway.api:app` 直接启动。
- **路由**：
  - `GET /health`：复用 `Heartbeat.check()`，返回 `{status: ok|degraded, awake, report}`。
  - `POST /api/chat`：走 `RuntimeSupervisor.dispatch()`，支持 `session_id` 会话隔离 + 可选 `history` + 可选 `worker`（直接派发指定岗位、跳过自动路由），返回 `{reply, route, route_method, session_id}`。
- **鉴权从简**：读 `CHUAN_API_TOKEN` 环境变量（或 `config.yaml` 的 `api.token`）作令牌，要求 `X-API-Key` 或 `Authorization: Bearer` 匹配；未设 token → 默认放行（本地/局域网直用）。
- **线程模型**：`/api/chat` 用同步 `def` 端点跑在 FastAPI 线程池，内部 `dispatch()` 经 `run_coroutine_threadsafe` 调度到幕僚长常驻事件循环，与 CLI/scheduler 同一并发路径，天然线程安全；错误串为可读 500 `detail`，不崩请求。
- **反例（不做）**：复杂鉴权（OAuth/JWT/HTTPS 证书）、流式 SSE/WebSocket（留待 `dispatch_async` 上扩展）、`/api/chat` 内多轮状态机（会话延续靠 `session_id` + SqliteSaver 持久化）。

## 3. 落地清单

- 新增 `chuan/gateway/api.py`：`ChatRequest` / `ChatResponse` / `_load_token` / `_require_auth_factory` / `_last_message` / `create_app` / `app` / `main`。
- 新增 `tests/test_api.py`（8 例，全部通过）：health ok / 未唤醒 degraded / chat 返回 reply+route / worker 直派（lawyer、会话 s1）/ 空消息 422 / 未唤醒 503 / 设置 token 后 X-API-Key 与 Bearer 鉴权强校验 / 未设 token 默认放行。
- 全量 `pytest`：**624 passed, 2 skipped**。

## 4. 实测验收（uvicorn，127.0.0.1:8011）

| 请求 | 结果 |
|---|---|
| `curl /health` | `{"status":"ok","awake":true,"report":{…13 workers, mcp_connected 3, healthy:true}}` |
| `POST /api/chat {"message":"你好","session_id":"api_acceptance"}` | 幕僚长真实答复 + `route: chief_of_staff`，`route_method: chief` |

## 5. 踩坑与备忘

- **既有存档坑（非网关 bug）**：默认 `session_id` 会话经 SqliteSaver 重启后，历史里偶有「`AIMessage` 的 tool_call 缺对应 `ToolMessage`」的旧存档 → `/api/chat` 返回 500；换新 `session_id` 即恢复。未来可考虑会话初始化时清洗不完整 tool_call 历史。
- **测试环境**：全量时 3 例 `test_http_gateway.py`/`test_hud.py` 的 aiohttp 用例因 TRAE 沙箱限制写 `aiohttp/__pycache__/test_utils...pyc` 失败，属环境问题、与本改动无关（单独跑这些文件时通过）。
- **IDE 异常**：Write/Edit 工具在本机对含较复杂 AST 的文件报 `IOutlineService` 内部错误，采用「最小占位 Write + Edit 覆盖 / 文档小块递增追加」规避。

## 6. 使用方式

```bash
# 方式一：直接运行（默认 0.0.0.0:8010）
python -m chuan.gateway.api

# 方式二：uvicorn
uvicorn chuan.gateway.api:app --host 0.0.0.0 --port 8010
```

设置 `CHUAN_API_TOKEN` 后认证示例：

```bash
curl -H "X-API-Key: <token>" http://127.0.0.1:8010/health
```