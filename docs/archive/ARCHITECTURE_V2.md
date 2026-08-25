> **📦 历史归档**：本文档为 2026-08 架构升级前的 v2 设计稿，已过时，仅作历史参考。最新架构见 [DEVELOPMENT.md](../guide/DEVELOPMENT.md) 与 [DECISIONS.md](../plan/DECISIONS.md)。

# 川流 v2 架构设计 —— 多角色并行 + 角色-agent 解耦

> ⚠️ **本文档已过时（2026-08 架构升级后）**。最新架构设计请参阅 [DEVELOPMENT.md](../guide/DEVELOPMENT.md) 第2章「整体架构」和 [DECISIONS.md](../plan/DECISIONS.md) ADR-012（Gateway 七大组件）。
>
> 本文档保留作为历史参考，其中的「多角色并行」「角色-agent 解耦」「sub_agent」等概念已融入新架构，但具体组件拆分和实现路径已更新。

> 本文档描述从当前 1:1 串行架构向 N:N 并行分层系统的演进方案。

---

## 1. 现状 (v1)

```
用户 ──→ 幕僚长(LLM路由) ──→ 选1个worker ──→ 回复用户
```

| 特性 | v1 |
|------|----|
| 角色与 agent | 1:1 绑定（persona = `create_react_agent`） |
| 调度方式 | 串行，单轮 handoff |
| 并行能力 | 无 |
| 外部 agent | 作为独立 worker 注册，可被路由但不能被角色调用 |
| 唤醒方式 | 仅被动（用户输入） |

---

## 2. 目标 (v2)

```
用户/定时器 ──→ 幕僚长 ──→ 意图拆解
                              │
                 ┌────────────┼────────────┐
                 ▼            ▼            ▼
              角色1         角色2         角色3
            (programmer)  (researcher)  (lawyer)
                 │            │
           ┌─────┼─────┐      │
           ▼     ▼     ▼      ▼
        agentA  agentB  agentC  agentD
        (pi)  (prime)  (ghost)  (deep research)

                  └──────┬──────┘
                         ▼
                     聚合回复
                         │
                         ▼
                      用户
```

| 特性 | v2 |
|------|----|
| 角色与 agent | 1:N 解耦（角色编排多个 agent） |
| 调度方式 | 并行 fan-out，多角色同时工作 |
| 并行能力 | asyncio.gather / 线程池 |
| 外部 agent | 作为 sub-agent tool 被角色调用 |
| 唤醒方式 | 被动（用户）+ 主动（定时器/事件） |

---

## 3. 核心概念

### 3.1 三层结构

```
L1: 幕僚长 (Chief of Staff)
    ├── 意图拆解（LLM 将用户请求分解为子任务）
    ├── 并行调度（fan-out 到多个角色）
    └── 结果聚合（合并各角色回复）

L2: 角色 (Persona/Role)
    ├── 定义人设、权限边界、大脑档位
    ├── 持有工具集 + sub-agent 注册表
    └── 可调用其他角色或 sub-agent

L3: Sub-agent
    ├── 来自外部注册（pi, prime_agent, 开源 agent）
    ├── 通过 stdin/stdout 协议通信
    └── 经过 guard 安全闸
```

### 3.2 角色与 agent 的关系

```
角色 (Persona)
  ├── 身份定义（display_name, description, role）
  ├── 大脑绑定（brain 档位）
  ├── 工具集（MCP tools + skill handlers）
  └── sub-agent 列表（可调用的外部 agent 列表）
       ├── agent_id: "pi"
       │   ├── type: "command" | "prompt" | "mcp"
       │   ├── invoke: 调用方式
       │   └── timeout: 超时
       └── agent_id: "prime_agent"
           ├── type: "command"
           ├── invoke: ["powershell", "run_prime_agent.ps1"]
           └── timeout: 600
```

---

## 4. 组件设计

### 4.1 SubAgentRegistry

统一管理所有可用的 sub-agent，包括内置 agent 和外部 agent。

```python
class SubAgentSpec:
    id: str                    # 唯一标识
    name: str                  # 显示名
    type: Literal["command", "prompt", "mcp", "langgraph"]
    description: str
    invoke: Any                # 调用方式
    timeout: int               # 超时秒数
    guard: Guard               # 安全闸实例

class SubAgentRegistry:
    def register(self, spec: SubAgentSpec) -> None
    def get(self, id: str) -> SubAgentSpec | None
    def list(self) -> list[SubAgentSpec]
    def invoke(self, id: str, task: str, context: dict) -> str
    async def invoke_async(self, id: str, task: str, context: dict) -> str
```

### 4.2 并行调度器 (ParallelDispatcher)

在 RuntimeSupervisor 中增加并行分发能力。

```python
class ParallelDispatcher:
    def __init__(self, supervisor: RuntimeSupervisor):
        self._supervisor = supervisor

    def dispatch(self, message: str) -> dict[str, Any]:
        """1. 意图拆解 → 2. 并行 fan-out → 3. 聚合回复"""
        plan = self._decompose(message)          # LLM: 拆解为子任务
        futures = self._fan_out(plan.subtasks)   # 并行执行
        return self._aggregate(futures)          # 合并结果

    async def dispatch_async(self, message: str) -> dict[str, Any]:
        plan = await self._decompose_async(message)
        results = await asyncio.gather(*[
            self._execute_subtask(st) for st in plan.subtasks
        ])
        return await self._aggregate_async(results)

    def _decompose(self, message: str) -> TaskPlan:
        """LLM 将用户请求拆解为多个子任务，每个子任务指定目标角色"""

    def _fan_out(self, subtasks: list[SubTask]) -> list[Future]:
        """线程池并行执行多个子任务"""

    def _aggregate(self, results: list[dict]) -> dict:
        """LLM 聚合各角色回复为最终回复"""
```

### 4.3 TaskPlan 模型

```python
@dataclass
class SubTask:
    target_role: str          # 目标角色名
    instruction: str          # 子任务指令
    context: dict             # 上下文（记忆引用等）
    depends_on: list[str]     # 依赖的其他子任务 ID（用于 DAG）
    agents: list[str] | None  # 可选：指定使用的 sub-agent

@dataclass
class TaskPlan:
    id: str
    original_message: str
    subtasks: list[SubTask]
    aggregation_strategy: Literal["merge", "select_best", "priority"]
```

### 4.4 ProactiveWakeSource

主动唤醒源，扩展 scheduler 使其能唤醒幕僚长。

```python
@dataclass
class WakeEvent:
    source: Literal["timer", "file_change", "webhook", "system"]
    priority: int
    message: str
    target_role: str | None = None  # None = 幕僚长路由

class ProactiveWakeSource(ABC):
    @abstractmethod
    async def poll(self) -> list[WakeEvent]: ...
    @abstractmethod
    def start(self): ...
    @abstractmethod
    def stop(self): ...
```

---

## 5. 数据流

### 5.1 被动唤醒（用户输入）

```
用户输入 "帮我重构这个项目，同时检查合同风险"

1. 幕僚长收到消息
2. 意图拆解 LLM 分析：
   - SubTask 1: programmer — "重构 src/ 目录下的代码结构"
   - SubTask 2: lawyer — "检查 contracts/ 目录下的合同风险"
3. 并行调度器 fan-out：
   ┌─ programmer worker ─────────────────────┐
   │  programmer 收到任务                     │
   │  ├─ 调用工具: filesystem 读取代码        │
   │  ├─ 调用 sub-agent: prime_agent 分析重构 │
   │  └─ 输出重构方案                        │
   └──────────────────────────────────────────┘
   ┌─ lawyer worker ──────────────────────────┐
   │  lawyer 收到任务                         │
   │  ├─ 调用工具: filesystem 读取合同        │
   │  ├─ 调用 skill: contract_review 分析     │
   │  └─ 输出风险报告                        │
   └──────────────────────────────────────────┘
4. 聚合 LLM 合并两路输出为一条回复
5. 回复经 guard 审核后返回用户
```

### 5.2 主动唤醒（定时器/事件）

```
定时器触发 "每日代码审查"

1. scheduler 生成 WakeEvent
2. 幕僚长被主动唤醒（无用户输入）
3. 幕僚长根据预设规则决定：
   - 唤醒 programmer → 执行代码审查
   - 结果写入黑板的 "daily_review" 命名空间
   - 生成 ProactiveAlert 放入提醒队列
4. 用户下次交互时看到 /alerts 提醒
```

---

## 6. 实现阶段

### 阶段 1：SubAgentRegistry + 角色-agent 绑定（当前优先级）

**改动范围**：
- `chuan/adapters/` 新增 `sub_agent_registry.py`
- `persona_loader.py` 的 `Persona` 类增加 `sub_agents` 字段
- `external_agents.py` 注册到 SubAgentRegistry
- `persona_loader.py` 的 `_resolve_tools()` 将 sub-agent 作为 tool 挂载到角色

**影响**：零架构变动，角色在自己的 ReAct 循环里可以工具调用方式使用 sub-agent。

**预估行数**：~200 行

### 阶段 2：并行调度（当前优先级）

**改动范围**：
- `runtime_supervisor.py` 新增 `ParallelDispatcher`
- 新增 `TaskPlan` / `SubTask` 数据模型
- `dispatch()` 支持意图拆解 → fan-out → 聚合
- Intent decomposer（LLM 调用来拆解用户请求）

**影响**：架构核心变动，需要确保 `InMemorySaver` 在并行下会话隔离正确。

**预估行数**：~400 行

### 阶段 3：主动唤醒增强

**改动范围**：
- `scheduler.py` 增加 `WakeEvent` 和 `ProactiveWakeSource`
- 定时任务可唤醒幕僚长而不仅是指定 worker
- 文件变化监听、webhook 等作为主动唤醒源

**影响**：scheduler 从「定时派活」升级为「事件驱动唤醒」。

**预估行数**：~200 行

### 阶段 4：开源 agent 市场接入

**改动范围**：
- `SubAgentRegistry` 增加从 GitHub 仓库 URL 自动拉取 agent 的能力
- 定义开源 agent 接入规范（agent.yaml 契约）
- agent 沙箱执行环境（可选 Docker）

**影响**：需要设计 agent 市场协议和沙箱机制。

**预估行数**：~300 行

---

## 7. 接口契约

### 7.1 角色 YAML 新增字段

```yaml
# personas/programmer.yaml
name: programmer
sub_agents:
  - pi              # 可调用 pi agent
  - prime_agent     # 可调用 prime_agent
  - code_reviewer   # 可调用内置代码审查 agent
```

### 7.2 外部 agent YAML 规范（扩展）

```yaml
# agents/xxx/agent.yaml
name: my_agent
display_name: My Agent
description: 从 GitHub 获取的开源 agent
external: true
type: command          # command | prompt | mcp | langgraph
command: ["python", "agent.py"]
timeout_seconds: 300
```

### 7.3 SubAgentRegistry 注册 API

```python
# 注册内置 agent
registry.register(SubAgentSpec(
    id="code_reviewer",
    name="代码审查",
    type="prompt",       # 纯 prompt 型，不需要额外进程
    description="审查代码质量、安全漏洞",
    invoke={"prompt": "你是一个资深代码审查员..."},
    timeout=120,
))

# 注册外部 agent
registry.register(SubAgentSpec(
    id="prime_agent",
    name="Prime Agent",
    type="command",
    description="自改进 RLM 编码与研究 agent",
    invoke={"command": ["powershell", "run_prime_agent.ps1"]},
    timeout=600,
))
```

---

## 8. 与现有 ADR 的兼容性

| ADR | v2 兼容性 | 说明 |
|-----|-----------|------|
| ADR-001 三层命名 | ✅ 兼容 | 不变 |
| ADR-002 单入口 | ✅ 兼容 | 用户仍只与幕僚长交互，内部并行对外透明 |
| ADR-003 三档大脑 | ✅ 兼容 | 不变 |
| ADR-004 Obsidian 记忆 | ✅ 兼容 | 并行写入需注意文件锁 |
| ADR-005 LangGraph Supervisor | ⚠️ 部分兼容 | 并行 fan-out 需要绕过 `create_supervisor` 的 handoff 机制，自定义图节点 |
| ADR-006 外来 agent | 🔄 扩展 | 从「独立 worker」扩展为「sub-agent + 可路由 worker」两种形态 |
| ADR-007 薄层 | ⚠️ 需注意 | 并行调度器应控制在 ~400 行内，不引入新框架 |
| ADR-008 封驳关 | ✅ 兼容 | guard 仍然拦截所有 sub-agent 调用 |
| ADR-009 全局工具 | ✅ 兼容 | sub-agent 也可纳入 deny 机制 |

---

## 9. 开放问题

1. **并行会话隔离**：多个角色的并行调用如何隔离 `InMemorySaver` 的会话状态？每个 sub-task 用独立 `thread_id`？
2. **聚合策略**：当多个角色返回冲突结果时，LLM 如何裁决？是否需要引入「投票/仲裁」机制？
3. **DAG 依赖**：子任务之间有依赖关系（如 lawyer 需要等 programmer 产出代码才能审查），如何表达 DAG？
4. **sub-agent 发现**：开源 agent 如何注册到 SubAgentRegistry？是手动配置还是自动扫描？
5. **资源限制**：多个 sub-agent 并行运行时，CPU/内存/API token 如何做配额管理？

---

## 10. 架构图

```
┌─────────────────────────────────────────────────────────┐
│                     chuan-os v2                          │
│                                                         │
│  ┌──────────┐   ┌──────────────┐   ┌────────────────┐  │
│  │ 用户 CLI  │   │  Scheduler   │   │ Webhook/事件   │  │
│  └─────┬────┘   └──────┬───────┘   └───────┬────────┘  │
│        │              │                    │           │
│        └──────────────┼────────────────────┘           │
│                       ▼                                │
│  ┌──────────────────────────────────────────┐          │
│  │        幕僚长 (Chief of Staff)           │          │
│  │  ┌─────────────┐  ┌──────────────────┐   │          │
│  │  │ Intent      │  │ Parallel         │   │          │
│  │  │ Decomposer  │──│ Dispatcher       │   │          │
│  │  └─────────────┘  └────────┬─────────┘   │          │
│  └────────────────────────────┼──────────────┘          │
│                               │                         │
│         ┌─────────────────────┼─────────────────┐       │
│         ▼                     ▼                  ▼       │
│  ┌──────────┐          ┌──────────┐       ┌──────────┐  │
│  │ 角色1    │          │ 角色2    │  ...  │ 角色N    │  │
│  │(prog)   │          │(lawyer)  │       │(research)│  │
│  └────┬────┘          └────┬─────┘       └────┬─────┘  │
│       │                    │                   │        │
│  ┌────┴────┐          ┌────┴─────┐       ┌────┴─────┐  │
│  │ SubAgent│          │ Tools    │       │ SubAgent  │  │
│  │ Registry│          │ (MCP/    │       │ Registry  │  │
│  │         │          │  skill)  │       │           │  │
│  ├─ pi     │          └──────────┘       ├─ deep     │  │
│  ├─ prime  │                             │   research│  │
│  └─────────┘                             └───────────┘  │
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │           Guard 安全闸 (每层)                    │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │           Memory (Obsidian + 黑 板)              │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

---

## 11. 总结

| 阶段 | 内容 | 行数 | 风险 |
|------|------|------|------|
| 1 | SubAgentRegistry + 角色-agent 绑定 | ~200 | 低，增量改动 |
| 2 | 并行调度 | ~400 | 中，核心架构变动 |
| 3 | 主动唤醒增强 | ~200 | 低，scheduler 扩展 |
| 4 | 开源 agent 市场接入 | ~300 | 中，需设计协议 |
| **合计** | | **~1100** | |

总增量 ~1100 行，加上现有 ~3000 行，v2 总代码量约 ~4100 行，仍符合 ADR-007 的薄层原则（<8k 行）。