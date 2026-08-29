# 川流 · chuan-os

> 你的本地多智能体 AI 班底（A9 家族办公室版）。单入口「幕僚长」（总公司 CEO）按意图路由到 10 个事业部/部门（财富幕僚：秘书 / 律师 / IT / 研究 / 投资 / 财务 / 税务 / 幕僚长；生活贴身：管家 / 保镖），每个部门下挂具体岗位（agent 实例）干活。

**川流 (chuan-os)** 是在你自己电脑上跑的个人 AI 公司：大脑分三档（本地推理不出机 / 云端通用脑 / 云端编码脑），由一位「幕僚长」任 CEO 统筹 N 个事业部（每个部门配岗位/agent 实例干活），配三层记忆、语音交互、Flutter 全息 HUD、微信通道与后台委派。它不是单个聊天机器人，而是一家为「顶级富豪配置的各类人员」建模的虚拟公司。

关键词：`ai-agent` `llm` `ollama` `mcp` `local-ai` `multi-agent` `jarvis` `assistant` `rag` `agent-orchestration`

## 命名模型（三层解耦）

| 槽位 | 值 | 说明 |
|---|---|---|
| 代码名 (repo/path/CLI) | `chuan-os` | ASCII，给机器看 |
| 展示名 (品牌/UI/自媒体) | 川流 | 中文，给人看 |
| 唤醒词 (语音) | 小川小川 | 嘴上喊的，自由设置 |

## 快速开始

```bash
# 1. 安装依赖
pip install -e .

# 2. 配置大脑（config/config.yaml 中选 local / cloud_general / cloud_coding）
#    本地推理需安装 Ollama 并拉取模型：ollama pull <模型名>
#    云端推理需在 config/secrets.yaml 配置对应 API Key

# 3. 启动（任选入口）
python -m chuan.main      # CLI 对话
python -m chuan.voice     # 免提语音（唤醒词）
python -m chuan.tui       # TUI 调试界面
```

> 你说「帮我看下合同」→ 幕僚长（总公司）自动路由到律师部门 → 部门调度岗位 agent 调用工具分析 → 回复经 guard 安全闸后返回。交互中可输入 `/help` 查看全部命令。

## 核心特性

| 特性 | 说明 | 详情 |
|---|---|---|
| 六层架构 | 底座→大脑→工具→编排→记忆→接入，幕僚长 L3 为唯一入口 | [架构](docs/diagrams/architecture.svg) |
| 10 个事业部班底（A9） | SOUL.md 驱动的部门（财富幕僚+生活贴身），独立人设/大脑/工具权限，下挂岗位 agent 实例 | [家办建制](docs/plan/FAMILY-OFFICE.md) |
| 三层记忆 | 短期会话（SqliteSaver）+ 长期检索（FTS5 + sqlite-vec 向量双路）+ 共享黑板（Obsidian） | [开发指南](docs/guide/DEVELOPMENT.md) |
| 技能即记忆 | 干完活自动提炼技能（N30），做方案/需求分析/复杂任务按触发词注入协作纪律 | [ADR-025](docs/plan/DECISIONS.md) |
| 多端接入 | CLI / TUI / 语音（免提）/ 微信 / HUD 悬浮层 / HTTP API / 手机 PWA（HTTPS） | [开发指南](docs/guide/DEVELOPMENT.md) |
| GUI 自动化 | 桌面元素定位/点击/输入 + 元素记忆库自愈（真机全链路验证通过） | [ADR-054/055](docs/plan/DECISIONS.md) |
| 后台委派 | fire-and-forget 委派 pi/opencode/claude_code 黑盒跑长任务 | [ADR-016](docs/plan/DECISIONS.md) |
| 监督者 | 全程监控执行轨迹，死胡同检测 + redirect，HUD 实时可视化 | [ADR-018](docs/plan/DECISIONS.md) |
| 媒体生成 | 音乐程序化合成 + 视频/图片配置化 HTTP 后端（未配端点时返回可读提示） | [ADR-058](docs/plan/DECISIONS.md) |
| 安全增强 | 机器绑定加密（换机不可读）/ 陌生人识别 / 自动锁屏（声纹防欺骗） | [ADR-057](docs/plan/DECISIONS.md) |

## 架构一览

![六层架构](docs/diagrams/architecture.svg)

![一次请求的生命周期](docs/diagrams/lifecycle.svg)

## 文档导航

所有文档集中在 [docs/](docs/README.md)（按类别分目录）：

- **开发指南** → [docs/guide/DEVELOPMENT.md](docs/guide/DEVELOPMENT.md)
- **开发路线（N0–N59 状态）** → [docs/plan/ROADMAP.md](docs/plan/ROADMAP.md)
- **架构决策（ADR-001~059）** → [docs/plan/DECISIONS.md](docs/plan/DECISIONS.md)
- **借鉴来源** → [docs/reference/REFERENCES.md](docs/reference/REFERENCES.md)
- **HUD 悬浮层** → [hud_overlay/README.md](hud_overlay/README.md)

## 项目结构

```
chuan-os/
├── chuan/          # 核心引擎（runtime_supervisor / gateway / voice / tui / channels…）
├── personas/       # 10 个事业部（SOUL.md 目录驱动，A9 家办版）
├── agents/         # 外来 agent（pi / prime_agent / claude_code / opencode）
├── skills/         # 技能定义 + handlers
├── mcp_servers/    # 自定义 MCP 服务端
├── config/         # 全局配置（大脑路由 / MCP / 音色 / 密钥）
├── docs/           # 文档中心（详见上方导航）
├── hud_overlay/    # Flutter 全息 HUD 悬浮层
└── tests/          # 单元测试（875 passed / 2 skipped）
```

完整目录说明见 [开发指南 · 目录结构](docs/guide/DEVELOPMENT.md#4-目录结构)。

---

*LICENSE: 待定*
