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

## 7. 对抗性审查加固（G1/G2，2026-08-25）

模拟用户 + 攻击者视角的对抗性审查（启动真实网关，`/health` 13 岗位 / 4 MCP / 真实对话正常、guard 拦截 `rm -rf /`）发现并修复两个 API 健壮性问题：

- **G1 worker 参数**：未知 / 大小写 / 带空白的 worker 名原样透传，`dispatch_to` 抛 `KeyError` 被包成 **500**。修复：新增 `_normalize_worker`（去空白 + 大小写归一化，roster key 全小写），未知或纯空白 worker → **400**（客户端错误语义，不再污染 500 告警）。
- **G2 长度上限**：`ChatRequest.message` 只有 `min_length=1` 无上限，实测 50k 字符直通模型并触发知识沉淀。修复：加 `max_length=8000`，超长 → **422**（pydantic 校验阶段拒绝，不碰模型不烧 token）。

测试：`tests/test_api.py` 由 8 例扩到 **12 例**（新增：超长 422 / 未知 worker 400 / 大小写·空白归一化 200 / 纯空白 worker 400），全部通过。

实测对比（真实网关 `/api/chat`，2026-08-25）：

| 测试项 | 优化前 | 优化后 |
|---|---|---|
| 未知 worker `ghost_role` | HTTP 500 | **HTTP 400**（已修）|
| worker 大小写/空白 `Researcher` / `' researcher '` | HTTP 500 | **HTTP 200 · 归一化**（已修）|
| 纯空白 worker | HTTP 500 | **HTTP 400**（已修）|
| 超长 message 50k | HTTP 200 · 烧 token · 沉淀垃圾 | **HTTP 422 · 拒绝**（已修）|
| 危险命令 `rm -rf /` | 安全拦截 | 拦截（不变）|
| 真实对话冒烟 | HTTP 200 正常 | HTTP 200 正常（不变）|
| 测试覆盖 | api 8 例 · 全量 711 | **api 12 例 · 全量 717**（已修）|

**遗留观察（未修，取决于部署形态）**：
- G4 无 token 默认全放行——设计如此（本地/局域网），暴露公网必须设 `CHUAN_API_TOKEN`；
- G5 bus/queue 在 Redis 不可达时降级 `backend: memory`——符合「协调层可降级」设计，但跨进程队列/总线实际不生效，需运维确认 Redis 可达性。