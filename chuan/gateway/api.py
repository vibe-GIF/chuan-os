"""HTTP API / FastAPI Gateway —— 客户端/服务器解耦接入层（ROADMAP P3 / 接入层）。

把 RuntimeSupervisor 暴露为 HTTP 服务：``/health`` 健康检查 + ``/api/chat`` 聊天，
让脚本 / 网页 / PWA 等客户端通过 HTTP 与 chuan-os 交互，与 CLI / TUI / 语音解耦。

鉴权从简（本地/局域网）：
- 设置环境变量 ``CHUAN_API_TOKEN``（或 ``config.yaml`` 的 ``api.token``）后，
  要求请求头 ``X-API-Key``（或 ``Authorization: Bearer``）匹配才放行；
- 未设置任何 token → 不鉴权，方便本地/局域网直接使用。

用法（在项目根目录）：
    python -m chuan.gateway.api                 # 启动 uvicorn，默认 0.0.0.0:8010
    uvicorn chuan.gateway.api:app --host 0.0.0.0 --port 8010
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from chuan.runtime_supervisor import RuntimeSupervisor

# 默认配置路径（相对项目根目录；uvicorn 需从项目根目录启动）
DEFAULT_CONFIG_PATH = "config/config.yaml"
# 默认监听地址 / 端口（可用环境变量覆盖）
DEFAULT_HOST = os.environ.get("CHUAN_API_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.environ.get("CHUAN_API_PORT", "8010"))


# ---------------------------------------------------------------------------- #
# 请求 / 响应模型
# ---------------------------------------------------------------------------- #
class ChatRequest(BaseModel):
    """``/api/chat`` 请求体。"""

    message: str = Field(..., min_length=1, description="用户输入文本")
    session_id: str = Field("default", description="会话 ID，用于会话隔离")
    history: list[dict[str, str]] | None = Field(
        None,
        description="可选历史消息，如 [{'role': 'user', 'content': '...'}]",
    )
    worker: str | None = Field(
        None, description="可选：直接派发给指定岗位（跳过自动路由）"
    )


class ChatResponse(BaseModel):
    """``/api/chat`` 响应体。"""

    reply: str
    route: str | None = None
    route_method: str | None = None
    session_id: str


# ---------------------------------------------------------------------------- #
# 鉴权（从简，仅本地/局域网）
# ---------------------------------------------------------------------------- #
def _load_token(config_path: str) -> str:
    """读取访问令牌：优先环境变量 ``CHUAN_API_TOKEN``，其次 ``config.yaml`` 的 ``api.token``。"""
    token = os.environ.get("CHUAN_API_TOKEN", "")
    if token:
        return token.strip()
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        token = (cfg.get("api") or {}).get("token", "") or ""
    except Exception:  # noqa: BLE001 - 读不到配置视为不鉴权
        token = ""
    return (token or "").strip()


def _require_auth_factory(config_path: str) -> Callable[..., None]:
    """生成鉴权依赖。token 为空 → 不鉴权；否则要求请求头匹配。"""
    expect = _load_token(config_path)

    def require_auth(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> None:
        if not expect:
            return  # 未配置 token：本地/局域网默认放行
        provided = x_api_key or ""
        if authorization and authorization.lower().startswith("bearer "):
            provided = authorization[len("bearer "):]
        if provided.strip() != expect:
            raise HTTPException(status_code=401, detail="unauthorized")

    return require_auth


# ---------------------------------------------------------------------------- #
# 应用工厂
# ---------------------------------------------------------------------------- #
def _last_message(result: dict[str, Any]) -> str:
    """从 dispatch 结果里取末条回复文本（与 main.py 一致）。"""
    messages = result.get("messages", [])
    if not messages:
        return "（没有返回内容）"
    last = messages[-1]
    content = getattr(last, "content", None)
    if content is None and isinstance(last, dict):
        content = last.get("content")
    return str(content or "（没有返回内容）")


def create_app(
    *,
    supervisor: RuntimeSupervisor | None = None,
    supervisor_factory: Callable[..., RuntimeSupervisor] | None = None,
    config_path: str = DEFAULT_CONFIG_PATH,
) -> FastAPI:
    """构建 FastAPI 应用。

    Args:
        supervisor: 传入则**复用**该实例（测试用），不管理其生命周期；
            否则由本应用在 lifespan 里用 ``supervisor_factory`` 创建并
            ``wake_up()``，关闭时 ``shutdown()``。
        supervisor_factory: 默认 ``RuntimeSupervisor``（懒导入，保持模块导入轻量）；
            自定义需支持 ``config_path=`` 关键字参数。
        config_path: 配置路径（决定 token 与 MCP/persona 等运行时配置）。

    Returns:
        FastAPI 应用（含 /health、/api/chat）。
    """
    require_auth = _require_auth_factory(config_path)
    _shared = supervisor is not None

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        nonlocal supervisor
        if supervisor is None:
            factory = supervisor_factory
            if factory is None:
                from chuan.runtime_supervisor import RuntimeSupervisor

                factory = RuntimeSupervisor
            supervisor = factory(config_path=config_path)
            supervisor.wake_up()
        app.state.supervisor = supervisor
        yield
        if not _shared:
            supervisor.shutdown()

    app = FastAPI(title="chuan-os HTTP Gateway", version="0.1.0", lifespan=_lifespan)

    @app.get("/health", tags=["system"], dependencies=[Depends(require_auth)])
    def health() -> dict[str, Any]:
        """健康检查：汇总幕僚长/大脑/MCP/岗位/记忆状态（复用 Heartbeat）。"""
        sup = app.state.supervisor
        report = sup.heartbeat.check()
        ok = bool(report.get("healthy")) and bool(sup.is_awake)
        return {"status": "ok" if ok else "degraded", "awake": sup.is_awake, "report": report}

    @app.post("/api/chat", tags=["chat"], dependencies=[Depends(require_auth)])
    def chat(req: ChatRequest) -> ChatResponse:
        """Chat 入口：分发用户消息到幕僚长，返回末条回复与路由去向。"""
        sup = app.state.supervisor
        if not sup.is_awake:
            raise HTTPException(status_code=503, detail="幕僚长尚未就绪")
        session_id = req.session_id or "default"
        try:
            if req.worker:
                result = sup.dispatch_to(
                    req.worker, req.message, session_id=session_id
                )
                route, method = req.worker, "worker"
            else:
                result = sup.dispatch(
                    req.message, history=req.history, session_id=session_id
                )
                route = result.get("route")
                method = result.get("route_method")
            reply = _last_message(result)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - 串成可读的 500，别让请求崩溃
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ChatResponse(
            reply=reply, route=route, route_method=method, session_id=session_id
        )

    return app


# 模块级默认应用：uvicorn chuan.gateway.api:app
app = create_app()


# ---------------------------------------------------------------------------- #
# 直接运行入口
# ---------------------------------------------------------------------------- #
def main() -> None:
    """启动 uvicorn 服务（从项目根目录运行）。"""
    import uvicorn

    print(f"川流 chuan-os HTTP Gateway · http://{DEFAULT_HOST}:{DEFAULT_PORT}")
    uvicorn.run("chuan.gateway.api:app", host=DEFAULT_HOST, port=DEFAULT_PORT)