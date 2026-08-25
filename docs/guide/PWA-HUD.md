# PWA 手机 HUD 联调说明（N48 / ADR-043）

让手机在**同一局域网**经 HTTPS 访问川流，实时接收/下发 HUD 命令。这是一个 Web 旁路：
在既有 HUD（TCP → Flutter 悬浮层）之上叠加 `chuan/gateway/http_gateway.py` + `web/` PWA，
SCENE 协议同一套帧只换传输层（TCP → WebSocket）。

```
手机 PWA (web/)
   │  HTTPS / (静态页)   ·  /ws (SCENE WebSocket)
   ▼
HttpGateway (aiohttp, :8443)
   │  POST /api/message → RuntimeSupervisor.dispatch
   │  POST /api/hud     → HudChannel(→ Flutter 悬浮层, TCP :17889)
```

## 目录 / 文件

| 路径 | 作用 |
|---|---|
| `chuan/gateway/http_gateway.py` | HTTPS 静态服务 + SCENE WebSocket + `/api/*`（旁路） |
| `web/` | PWA 外壳：`index.html`/`style.css`/`app.js`/`manifest.webmanifest`/`sw.js`/`icon.svg` |
| `scripts/gen_https_cert.py` | 自签证书生成（openssl → Git openssl → cryptography 三档） |
| `certs/https_cert.pem` + `certs/https_key.pem` | 生成的证书（**gitignored，私钥勿提交**） |
| `config/config.yaml -> http:` | 网关配置（仅该段为 N48 新增） |

## 一、生成自签证书（一次性）

```powershell
.venv\Scripts\python.exe scripts\gen_https_cert.py        # 默认输出到 certs/
.venv\Scripts\python.exe scripts\gen_https_cert.py --dir certs --days 825
```

- 证书 SAN 自动含 `localhost` + 本机所有局域网 IPv4；
- 本机没有 openssl / cryptography 时脚本明确报错，网关会**静默退回纯 HTTP**（见「故障排查」）。

## 二、启动网关

```powershell
# 只起静态 PWA + WS + API（不接 supervisor/hud，/api/message、/api/hud 返回 503）
.venv\Scripts\python.exe -m chuan.gateway.http_gateway

# 拉起完整栈（RuntimeSupervisor + HUD + wake_up），手机真正能对话
.venv\Scripts\python.exe -m chuan.gateway.http_gateway --supervisor
```

启动后打印 `[HTTP] 网关已启动：https://<host>:8443/`。

## 三、手机接入

1. 手机连接**同一路由器/网段** Wi-Fi；
2. 浏览器打开 `https://<电脑局域网IP>:8443/`
   （IP 用 `ipconfig` 查，或看启动日志 SAN 里的 IP）；
3. **首次访问需信任自签证书**：
   - **Android / Chrome**：进高级继续访问；要消除警告，可把 `https_cert.pem` 发到手机并安装为 CA（设置→安全→从存储安装）。
   - **iOS / Safari**：访问后进「设置 → 通用 → 关于本机 → 证书信任设置」开启信任。
4. 页面右上角状态点变绿=已连接 WebSocket。

## 四、PWA 可安装

- 手机浏览器菜单→「添加到主屏幕 / 安装应用」（Android Chrome）/ Safari「添加到主屏」；用了 `manifest.webmanifest` + `sw.js` 离线外壳，可作为独立应用打开。

## 五、协议 / API

**`/ws`（SCENE WebSocket，与 N34 同帧）**
- 订阅：`hello:{json}`（caps 协商）→ `scene:{json}`（全量）→ `patch:{json}`（增量，agent/effect/user/ai/monitor）；
- 发消息：`message:{文本}`（后端经 supervisor 路由，回复经 patch 广播回前端）。

**`POST /api/message`**  `{"message":"...","session_id":"default"}` → `{"ok":true,"reply":"..."}`

**`POST /api/hud`**  显式下发 HUD 命令，同时广播 SCENE 帧给所有手机：
`{"command":"wake"|"hide"|"agent"|"effect"|"user"|"ai"|"monitor", ...}`

**`GET /api/health`**  `{"ok":true,"tls":true,"supervisor":true,"hud":true,"ws_clients":N,"scene":{...}}`

## 六、验收清单

- [ ] `pytest tests/test_http_gateway.py` → 13 passed
- [ ] 手机同局域网 `https://<IP>:8443/` 打开且显示「川流 HUD」
- [ ] 可添加到主屏幕（PWA 安装）
- [ ] 输入消息 → 收到 AI 回复，orb 状态 / 终端文本实时更新
- [ ] 切换角色 / 点按钮 → `/api/hud` 生效（若 Flutter 悬浮层在线则同步生效）

## 七、故障排查

| 现象 | 排查 |
|---|---|
| 启动日志是 `http://` 非 `https://` | `certs/` 缺证书或加载失败，网关**静默降级纯 HTTP**（旁路设计）；跑第 1 步生成证书后重启 |
| 手机打不开 | 同一网段？电脑 `ipconfig` IP 对不对？防火墙是否放行 8443（TCP） |
| 证书警告 | 自签属预期，按第 3 步信任或装 CA |
| `/api/message` 返回 503 | 网关是**独立模式**启动（未 `--supervisor`） |
| WebSocket 反复重连 | 网关未绑定 supervisor 时 `message:` 无回复属正常；后台未起来时 WS 仍可按 hello/scene 收帧 |

> 注：`tests/test_hud.py` 个别 TCP 用例是该模块**历史记录的网络时序 flaky**，与 PWA 无关；`tests/test_api.py` 属 N47 FastAPI 通道（需 `fastapi`），与 N48 aiohttp 通道相互独立。