# 企业级 chuan 演进蓝图（Enterprise Vision）

> 定位：把 chuan-os 从「个人单机版」演进到「企业级分布式」的架构设想。
> 本文是设想与演进路径，不是已落地的方案；每一项待决策后再进 DECISIONS/ROADMAP。
> 2026-08-25

---

## 背景：为什么写这份文档

面试中被评价为「半个企业级项目」，且因「没用 RAG / 用 SQLite+FTS5」被质疑技术水平。
复盘结论：技术判断本身没问题，问题出在**叙事方式**——把「有意选型」讲成了「能力不足」。

这份文档的价值有三层：
1. 厘清个人版与企业版在每个层面的**假设差异**（换掉的不是技术，是单机假设）；
2. 作为面试叙事的底稿：证明每一步都清楚「在换什么、为什么换、放弃了什么」；
3. 若未来真做企业级，这是现成的演进地图。

---

## 一、个人版现状盘点（单机假设）

| 层 | 现状实现 | 单机假设 |
|---|---|---|
| 接入 | FastAPI 网关 `从简鉴权`（`CHUAN_API_TOKEN` / `api.token`，未设则放行） | 可信内网，单用户 |
| 编排 | `dispatch` 直接派发；`team_orchestrator` 固定 600s；黑板写失败静默 | 一人可控，无需审批/SLA |
| 运行时 | `RuntimeSupervisor` 单进程 + 常驻事件循环；agent 池动态扩缩容（进程内） | 单实例够用 |
| 数据 | `data/sessions.db` + `memory_fts.db`；RAG = FTS5 词法 + sqlite-vec 语义双路；N55 闸门按库规模开关向量 | SQLite 本地够用，零外部依赖 |
| 可观测 | `heartbeat.py` 健康报告 + HUD 投影 | 本人可看即可 |

**实测基线（2026-08-25 对抗性审查）**：起真实网关 → `/health` 返回 `13 岗位 / 4 MCP 连接 / memory_ready / healthy:true`；guard 拦截 `rm -rf /`（安全层生效）；全量回归 **717 passed, 2 skipped**。对抗审查发现并修复 G1（worker 大小写/未知 → 500，改 400 + 归一化）、G2（message 无长度上限，加 `max_length=8000` → 422）；G4（无 token 全放行）、G5（Redis 不可达降级 `backend: memory`）留待部署形态确认。

实测对比（真实网关 `/api/chat`）：

| 测试项 | 优化前 | 优化后 |
|---|---|---|
| 未知 worker `ghost_role` | HTTP 500 | **HTTP 400**（已修）|
| worker 大小写/空白 `Researcher` / `' researcher '` | HTTP 500 | **HTTP 200 · 归一化**（已修）|
| 纯空白 worker | HTTP 500 | **HTTP 400**（已修）|
| 超长 message 50k | HTTP 200 · 烧 token · 沉淀垃圾 | **HTTP 422 · 拒绝**（已修）|
| 危险命令 `rm -rf /` | 安全拦截 | 拦截（不变）|
| 真实对话冒烟 | HTTP 200 正常 | HTTP 200 正常（不变）|
| 测试覆盖 | api 8 例 · 全量 711 | **api 12 例 · 全量 717**（已修）|

关键点：个人版已经埋好了大部分企业级接口——FastAPI 网关（`gateway/api.py`）、外部 vault 隔离（`memory.py`）、任务 DAG（`agent_harness.py` 终态裁剪 + `depends_on`）、「失败静默降级」哲学。企业级不是重写，是**把「单机假设」逐层替换成「分布式假设」**。

---

## 二、企业级五层目标形态

### 1. 接入与身份 —— 从「谁都能聊」到「谁在什么权限下做什么」

- SSO 接入（企业微信 / OIDC / SAML），替代 `从简鉴权`
- RBAC：区分「谁能让 agent 写文件」「谁能触发 /team」「谁能看某岗位产出」
- 审计日志记录每次 agent 动作
- 多租户隔离：`wechat:<id>` 会话隔离升级为**租户级命名空间**
- 落地选型：网关（FastAPI/Ory Oathkeeper）+ IdP（Keycloak/企业微信）+ 网关级 RBAC 中间件
- 代价：引入 IdP 运维；`/api/chat` 需透传身份上下文给编排层

### 2. 编排与控制 —— 从「自由派发」到「有 SLA 的受控执行」

- 任务进**审批流**：高危操作（写文件/跑 bash/删数据）人工确认
- **SLA 分级**：普通 vs 紧急任务不同超时与优先级（替换固定 600s）
- **死信队列**：重试 N 次失败进 DLQ 人工介入
- **沙箱隔离**：agent 的 bash/文件写限在容器内，而非项目根目录
- 基础：`agent_harness.py` 已有「终态裁剪 + 依赖 DAG + `_dep_ok`」，企业级是包装成可配置、可审计、可限额的服务
- 代价：审批流增加任务延迟；沙箱需维护镜像/资源配额

### 3. 运行时 —— 从「单进程常驻」到「多副本 + 弹性」

- 容器化多副本 + 负载均衡
- 无状态 API 层（FastAPI 网关）可随意横向扩
- 有状态的对话/checkpointer 移到外部（Redis / PostgreSQL）——正是当初 `InMemorySaver→SqliteSaver→AsyncSqliteSaver` 演进逻辑在企业级的延续
- 落地选型：K8s/Docker Compose + 无状态 worker 池（复用 agent_pool 扩缩容逻辑，进程级→实例级）
- 代价：分布式下状态一致性与会话亲和（sticky session）需要处理

### 4. 数据与知识 —— 从「SQLite 够用」到「分库分层」

- 会话/业务数据 → PostgreSQL（主从/云数据库）
- 记忆检索 → pgvector 或独立向量库；「词法+语义双路合并」与「外部 vault 隔离」已在 memory.py 做好，**换存储后端、接口不变**
- Redis 从「缓存旁路」升级为集群（哨兵 / Cluster）
- 任务队列从 Streams 升级到 Kafka（多消费者组、分区、持久化）
- 落地选型：checkpointer 换 `AsyncPostgresSaver`（langgraph 官方支持）；记忆层抽象 `retriever` 接口
- 代价：引入分布式事务/一致性问题；备份、迁移、调优全要人管

### 5. 可观测与审计 —— 从「TUI + heartbeat」到「三件套 + 审计链」

- Prometheus 指标 + OpenTelemetry 追踪 + 集中日志（ELK/Loki）
- 合规审计链：谁、何时、让 agent 做了什么、agent 又对系统做了什么，全部可追溯
- 基础：`data/teams/<session>/blackboard/` 落盘哲学已埋审计种子；heartbeat 报告可升级为 `/metrics`
- 落地选型：OTel SDK 埋点 + Prometheus 拉取 + Loki/ES 存储
- 代价：埋点工作量 + 存储成本；审计链需要 ID 贯穿（trace_id / tenant / session）

---

## 三、演进优先级（若真做）

1. **数据层先动**（SQLite → PostgreSQL）：接口已有，收益最大、风险最小
2. **身份与多租户**：企业级分水岭，没有它后面都谈不上
3. **可观测**：让系统可运维，才能谈「企业级」
4. **编排治理**（审批/SLA/死信）：把 agent_harness 的 DAG 做成可控服务
5. **运行时弹性**：最后做，前面不稳定时扩了也白扩

---

## 四、迁移路径（分阶段落地）

每个阶段有**进入条件**（触发它才做）与**验收标准**，避免为了「企业级」而提前过度设计。
进入条件尽量是**实测信号**而非空想——对抗审查已确认两个现成信号（G4/G5）。

| 阶段 | 动作 | 进入条件 | 验收 |
|---|---|---|---|
| 0 基线（现状已备） | FastAPI 网关 / 外部 vault / 任务 DAG / 降级哲学 | — | — |
| 1 数据层上云 | SQLite → PostgreSQL（会话/业务）+ pgvector（记忆向量）；Redis 集群 | 并发会话突破 SQLite 单写瓶颈；或 **G5 实测信号**：Redis 长期不可达降级 memory，跨进程队列/总线失效 | 多实例共享会话；记忆召回行为不变；bus/queue 走真实 Redis |
| 2 身份与多租户 | 网关接 SSO（OIDC/SAML）；worker/team/session 加 tenant 前缀 | >1 个团队/组织接入；或 **G4 实测信号**：网关将暴露公网，无 token 全放行成为风险 | 租户数据隔离、操作可审计；未授权请求被拒 |
| 3 可观测 | heartbeat 升级 `/metrics`；OTel 打 span；集中日志；审计链 | 多实例部署后即必须 | 任一请求可追踪到完整链路 |
| 4 编排治理 | agent_harness DAG 加审批门 / SLA 配置 / 死信队列 / 沙箱 | agent 高危操作开始影响生产 | 高危动作人工确认、超时分级、失败进 DLQ |
| 5 运行时弹性 | 无状态 API 层容器化多副本 + 扩缩容；checkpointer 全外部化 | 前四阶段稳定后 | 副本可随意扩缩，会话不丢 |

关键：阶段 1 的接口已抽象好（checkpointer 一路可换后端），阶段 4 的 DAG 已实现于 `agent_harness.py`。**多数阶段是「换后端 / 加门禁」，不是重写。**

---

## 五、面试叙事弹药库（同一件事的两种讲法）

### 30 秒电梯陈述

> chuan-os 是一个**岗位化的多 agent 协作系统**：一个幕僚长（chief）把任务拆给
> 多个岗位（researcher / copywriter / secretary…）并行执行，共享磁盘黑板聚合产出。
> 我做了五层：接入（CLI/TUI/语音/微信/HTTP）、编排（岗位调度 + 任务 DAG + 团队协作）、
> 运行时（动态实例池 + 自动扩缩容）、数据（会话持久化 + FTS5 词法 + sqlite-vec 语义双路记忆）、
> 可观测（心跳健康 + HUD 多投影）。全程「失败静默降级」——任何外部依赖挂了，主链路不塌。
> 面试官如果追问 RAG：这就是一套轻量 RAG 管道，规模到阈值（N55 闸门）自动切向量，可平滑升级 ES/FAISS。

### 「没用 RAG」的正确讲法
> 我做了一套轻量 RAG 管道：FTS5 词法召回 + sqlite-vec 语义召回双路合并。
> 记忆库规模没到阈值（N55 评估闸门，1000 篇 / 100 万字符），向量索引默认关闭只走 FTS5——
> 延迟 <1ms、零外部依赖。设计上可平滑升级到 FAISS 或 ES。

### 「SQLite + FTS5」的正确讲法
> 项目定位是本地运行时、零外部依赖，FTS5 的 unicode61 + CJK 逐字分词在我的记忆库规模下
> 召回延迟 <1ms。sqlite-vec 做 KNN 时不能在 WHERE 加元数据过滤，我在 Python 侧做跨 vault 过滤——
> 这是约束下的工程取舍。

### 逐层问答预演（面试官可能问 + 参考回答）

**Q1「为什么不用 ES / 向量数据库？」**
> 项目是本地单机运行时，零外部依赖是硬约束。我的库规模（几十篇笔记）用 FTS5 延迟 <1ms，
> 引入 ES 是给 1 毫秒的问题加 500 兆的运行时。但架构上留了口子：检索层抽象成候选召回 + 评分两步，
> 规模到了 N55 阈值自动开向量，未来可无痛换 ES/FAISS。

**Q2「多 agent 协作怎么保证不互相覆盖？」**
> 三个机制：会话级隔离（每个岗位独立 session_id）、磁盘黑板按角色分文件、任务 DAG 的依赖判定
> （`_dep_ok`，依赖未结束的进 pending）。并行用 `run_coroutine_threadsafe` 调度到常驻事件循环，
> 用 barrier 测试验证过是真并行不是串行。

**Q3「免费模型 JSON 不稳，你怎么处理？」**
> 确定性优先：能不用 LLM 判断就绝不用。岗位名单用正则解析，任务拆 DAG 用严格 JSON 校验，
> 失败一律降级单 agent。RAG 的触发词检测（N51 中文逐字分词）也是确定性前置，不靠模型猜意图。

**Q4「SQLite 并发写怎么办？」**
> 单进程内用 `_fts_lock`（threading.Lock）串行化 FTS/vec 写；checkpointer 用 `AsyncSqliteSaver`
> 绑定事件循环。真到多进程/多副本，迁移路径已经规划好（阶段 1 换 PostgreSQL）。

**Q5「单机项目怎么保证质量？有多少测试？」**
> 全量回归 **717 passed + 2 skipped**。除了单元测试，我做三轮对抗审查：
> 第一轮代码层——并发时序、竞态（修了语音 barge-in 重复 reset、任务 DAG 依赖裁剪卡死）；
> 第二轮模块深挖——memory/team/wechat（修了通道异常兜底、前缀剥除误伤）；
> 第三轮模拟用户 + 攻击者视角——起真实网关打 `/health`、`/api/chat`，测空/超长/非法 JSON/未知 worker，
> 还发 `rm -rf /` 验证安全层真的拦截。发现 API 语义 bug（未知 worker 返回 500）和长度上限缺失都修了。

### 核心教训
- **面试不是代码 review，是替面试官翻译你的价值**。别用自己的技术诚实换面试官的理解偏差。
- 把叙述重心从「我做了什么」转到「我为什么这么做、放弃了什么」——展示判断力而非实现量。
- 面试官一句「没用 RAG」不代表你没做，代表你**没翻译**。

---

## 六、引入企业级的代价与风险

企业级不是免费的，引入前必须算这笔账（这也是面试加分项：你懂取舍）：

| 代价 | 说明 |
|---|---|
| 分布式一致性 | SQLite 单写变 PG 多实例，事务、锁、会话亲和都要重新处理 |
| 运维负担 | PG / Redis 集群 / Kafka 每一件都要人管：备份、迁移、扩容、调优、告警 |
| 成本 | 基础设施、存储、网络；个人项目可能翻几十倍 |
| 复杂度爆炸 | 排查问题从「看一个进程」变成「看链路图上十几个节点」 |
| 过度设计风险 | 单用户场景上企业级 = 用十倍成本换一个不存在的瓶颈 |

**结论**：企业级是「需要时才上」，不是「越高级越好」。面试时主动说出这句，比被动挨问强得多：
> 我不是不会上企业级，是我知道**什么时候不该上**——这是判断力，不是能力缺失。

---

## 七、可复用资产清单（改造时无需重做的部分）

- [memory.py](chuan/memory.py)：词法+语义双路、外部 vault 隔离、FTS/vec 竞态保护（`_fts_lock`）——换后端即可
- [agent_harness.py](chuan/gateway/agent_harness.py)：任务 DAG + 终态裁剪 + `_dep_ok` 依赖判定——包装成服务即可
- [gateway/api.py](chuan/gateway/api.py)：FastAPI 网关（`/health` + `/api/chat` + worker 大小写/空白归一化 + 长度上限）——接 SSO/RBAC 即可
- [agent_pool.py](chuan/agent_pool.py)：动态实例池 + 自动扩缩容——从进程级升级为实例级即可
- [team_orchestrator.py](chuan/team_orchestrator.py)：多岗位并行 + 共享黑板——加审批门即可
- `data/teams/<session>/blackboard/`：审计种子——接入审计链即可
