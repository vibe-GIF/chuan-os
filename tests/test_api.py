"""N46 HTTP API / FastAPI Gateway（客户端/服务器解耦）测试。"""
from fastapi.testclient import TestClient

from chuan.gateway.api import create_app


class FakeHeartbeat:
    """假 Heartbeat：健康报告固定为健康。"""

    def check(self):
        return {"healthy": True, "awake": True}


class FakeSupervisor:
    """假 RuntimeSupervisor：不触 LLM/DB，验证路由与响应契约。"""

    def __init__(self, is_awake: bool = True):
        self.is_awake = is_awake
        self.heartbeat = FakeHeartbeat()

    def dispatch(self, message, history=None, session_id="default"):
        route = "programmer" if "程序" in message else "chief_of_staff"
        return {
            "messages": [{"role": "assistant", "content": f"回：{message}"}],
            "route": route,
            "route_method": "keyword",
        }

    def dispatch_to(self, worker, message, session_id="default"):
        return {
            "messages": [
                {"role": "assistant", "content": f"{worker}处理：{message}"}
            ],
            "route": worker,
            "route_method": "worker",
        }


def _app(supervisor=None):
    """构建复用假 supervisor 的应用（不动生命周期）。"""
    sup = supervisor or FakeSupervisor()
    return create_app(supervisor=sup), sup


def test_health_ok():
    app, _ = _app()
    with TestClient(app) as c:
        r = c.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["awake"] is True
    assert data["report"]["healthy"] is True


def test_health_degraded_when_not_awake():
    app, _ = _app(FakeSupervisor(is_awake=False))
    with TestClient(app) as c:
        r = c.get("/health")
    assert r.json()["status"] == "degraded"


def test_chat_returns_reply_and_route():
    app, _ = _app()
    with TestClient(app) as c:
        r = c.post("/api/chat", json={"message": "帮我写个程序"})
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] == "回：帮我写个程序"
    assert data["route"] == "programmer"
    assert data["route_method"] == "keyword"
    assert data["session_id"] == "default"


def test_chat_worker_direct_dispatch():
    app, _ = _app()
    with TestClient(app) as c:
        r = c.post(
            "/api/chat",
            json={
                "message": "帮我看看",
                "worker": "lawyer",
                "session_id": "s1",
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] == "lawyer处理：帮我看看"
    assert data["route"] == "lawyer"
    assert data["route_method"] == "worker"
    assert data["session_id"] == "s1"


def test_chat_requires_nonempty_message():
    app, _ = _app()
    with TestClient(app) as c:
        r = c.post("/api/chat", json={"message": ""})
    assert r.status_code == 422


def test_chat_503_when_not_awake():
    app, _ = _app(FakeSupervisor(is_awake=False))
    with TestClient(app) as c:
        r = c.post("/api/chat", json={"message": "你好"})
    assert r.status_code == 503


def test_auth_enforced_when_token_set(monkeypatch):
    monkeypatch.setenv("CHUAN_API_TOKEN", "secret")
    app = create_app(supervisor=FakeSupervisor())
    with TestClient(app) as c:
        assert c.get("/health").status_code == 401
        assert c.get("/health", headers={"X-API-Key": "secret"}).status_code == 200
        assert (
            c.get("/health", headers={"Authorization": "Bearer secret"}).status_code
            == 200
        )


def test_auth_open_when_no_token():
    app, _ = _app()
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200