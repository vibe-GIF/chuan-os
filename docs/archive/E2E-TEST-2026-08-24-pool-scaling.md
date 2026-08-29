# 端到端实测报告 — N41 动态实例池自动扩缩容

> **📦 历史归档**：2026-08-24 的 N41 动态实例池运行时验证报告。功能已落地，现状以 [ROADMAP.md](../plan/ROADMAP.md) N41 与 [DECISIONS.md](../plan/DECISIONS.md) ADR-036 为准。

- 日期：2026-08-24
- 环境：Windows / 本机 Python 3.13 / chuan-os v0.1.0
- 测试方式：`python -m chuan.main` CLI 启动 + 真实 `RuntimeSupervisor` 脚本直调
- 全量回归：552 passed / 2 skipped（1 例 hud 网络时序偶发 flaky，隔离重跑通过）

---

## 1. 背景与要验证的点

N41 给岗位实例池加了**自动扩缩容**（[ADR-036](../plan/DECISIONS.md)）：config.yaml `role_instances.pool` 声明容量与空闲回收策略，岗位按需扩容、开工前自动缩容。单元测试已覆盖逻辑，本次用**真实运行时栈**（真实 config + 真实 AgentSpawner + 真实岗位 + 真实 worker spawn）验证配置接线与运行时行为：

1. **配置接线**：`role_instances.pool` → 每个岗位的 `pool_config` 是否生效
2. **扩容**：并行子任务是否建 worker 实例、是否遵守 `pool.max` 上限
3. **用量统计**：`_touch_agent` 是否更新最近使用（作为缩容判定依据）
4. **缩容**：开工前 `_maybe_reclaim_idle` 是否回收空闲超 TTL 实例、保留 `min` 下限
5. **观测**：Heartbeat 健康报告是否暴露池状态（TUI/心跳可看）

---

## 2. 服务启动

`python -m chuan.main` CLI 启动成功并干净退出（exit 0），MCP 连接 `filesystem / weather / opencode`，未报错。

真实 `RuntimeSupervisor.wake_up()` 唤醒：13 个岗位 + 幕僚长，全部注入动态池配置。

---

## 3. 验证结果

### 3.1 配置接线 ✅

```text
岗位: bodyguard
pool_config: RolePoolConfig(min_instances=1, max_instances=3, idle_ttl=300.0)
```

`config.yaml role_instances.pool` 经 `load_role_instances` → `RoleInstanceConfig.pool` → `Department._pool_config` 全链路注入，14 个岗位（含幕僚长）动态池全部启用。

### 3.2 扩容：遵守 max 上限 ✅

4 个并行 auto 子任务 → 分配独立 worker 实例，上限 `pool.max=3`：

```text
扩容前 _agents: []  |  pool_stats: {'size': 0, 'min': 1, 'max': 3, 'idle': 0, 'uses': {}}
分配: {'s1': 'bodyguard', 's2': 'bodyguard', 's3': 'bodyguard', 's4': 'bodyguard'}
扩容后 _agents: ['worker0', 'worker1', 'worker2']
扩容后 pool_stats: size=3 min=1 max=3 idle=0
```

第 4 个并行子任务复用 worker2——扩容被 `max_instances=3` 截断，不会无上限铺开。

### 3.3 用量统计：_touch_agent ✅

```text
worker2 stat: _InstanceStat(created_at=..., last_used_at=..., uses=1)
```

实例实际执行一次即更新 `last_used_at` / `uses`，作为「谁最近在用」的确定性判定依据。

### 3.4 缩容：自动回收 + 保留下限 ✅

把该岗位 TTL 调小（`idle_ttl=0.0`）后触发开工前自动回收 `_maybe_reclaim_idle`：

```text
缩容前 _agents: ['worker0', 'worker1', 'worker2']   （worker2 刚被 touch，最近使用）
缩容后 _agents: ['worker2']
缩容后 pool_stats: size=1 min=1 max=3 idle=1
```

- 最久未用的 `worker0` / `worker1` 被回收；
- 最近使用的 `worker2` 幸免（`_touch_agent` 统计生效）；
- 保留 `min=1` 个非默认实例下限，默认实例永不回收。

### 3.5 观测：Heartbeat 健康报告 ✅

```text
pool_total: {'size': 1, 'idle': 1, 'min': 14, 'max': 42}
pools: role=bodyguard size=1 min=1 max=3 idle=1 uses={'worker2': 1}
pools: role=companion size=0 min=1 max=3 idle=0 uses={}
...（14 个岗位逐个）
summary: 健康 · 成员 13 · agent 1 · 脑 bailian_flash · MCP 3 · 池 1/42
```

健康报告 `pools` / `pool_total` 暴露每个岗位的池状态，状态栏可看「池 1/42」——扩缩容行为对 TUI/心跳全程可见。

---

## 4. 结论

- **自动扩缩容闭环在真实运行时成立**：按需扩容（守 max）→ 用量统计（touch）→ 开工前自动缩容（守 min）→ 按需重建（扩容闭环）；
- **配置驱动**：全链路从 config.yaml 生效，改配置即可调整容量/回收策略，无需改代码；
- 验证脚本为临时物，已清理，未改动任何业务代码。

> 全量回归：552 passed / 2 skipped。唯一失败 `test_hud.py::test_push_monitor_sends_json_command` 为 TCP 网络时序偶发 flaky（隔离重跑通过），与 N41 无关。
