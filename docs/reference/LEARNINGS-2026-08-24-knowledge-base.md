---
created: 2026-08-24
updated: 2026-08-24
importance: 4
tags: [research, memory, knowledge-base, wiki, rag, karpathy, second-brain]
source: 2026-08-24 调研（Karpathy gist / obsidian-second-brain / 姜胡说 7 层 / CC-Codex-Hermes）
---

# 知识库方法论调研：Karpathy LLM Wiki · obsidian-second-brain · 姜胡说 7 层 · 记忆机制

> 本笔记沉淀 2026-08-24 的记忆/知识库调研结论，直接指导 **N24 Wiki 知识库（ADR-019）** 落地。
> 核心结论：**「编译而非检索」是共识；此规模不需要向量 RAG；记忆要归并、互链、主动维护。**

---

## 1. 四者一句话定位

| 方法论 | 一句话 | 视角 |
|---|---|---|
| **Karpathy LLM Wiki**（2026-04） | 把资料「编译」成持久 wiki，LLM 当图书管理员 | 知识怎么被 AI 维护 |
| **obsidian-second-brain**（GitHub，MIT） | Karpathy 点子工程化成可安装 skill，加矛盾调和/定时维护 | 怎么落地成工具 |
| **姜胡说 7 层**（抖音，2026-07） | 从「当搜索引擎用」到「知识原子赚钱」的成长阶梯 | 你处在哪一层 |
| **Claude Code / Codex / Hermes 记忆** | 分层 markdown + 后台异步提取 + 硬容量约束 | coding agent 怎么做记忆 |

---

## 2. Karpathy LLM Wiki（概念源头）

- **出处**：2026-04-03 X 帖「LLM Knowledge Bases」（1500 万浏览），04-04 GitHub gist「LLM Wiki」。
- **核心批判 RAG**：每次提问从原始文档临时检索拼凑，**知识不积累**；LLM Wiki 是先把资料**编译**成结构化 wiki，然后永久维护——「knowledge compiled once, kept current, not re-derived」。
- **类比**：`Obsidian 是 IDE，LLM 是程序员，wiki 是代码库`。你只负责采集来源 + 提问 + 验收，wiki 由 LLM 独占维护。
- **三层架构**：
  1. **Raw Sources（不可变）**：文章/论文/PDF/图片，只读，防幻觉扩散。
  2. **Wiki（LLM 维护）**：实体页/概念页/摘要页/对比页/综合页 + `index.md` + `log.md`。
  3. **Schema（规则文件）**：`CLAUDE.md`/`AGENTS.md`，定义结构/命名/矛盾处理——**最关键的一层**，没它 wiki 会变混乱文本堆。
- **三大操作**：**Ingest**（加来源 → 扫全库更新 10-15 个文件）/ **Query**（读 index 定位 → 综合回答，好答案反哺回 wiki）/ **Lint**（查矛盾/过时/孤立/该建未建）。
- **三个坑（Critical Concerns）**：① Generation Effect（AI 写你未必学到）；② Error Accumulation（幻觉经交叉引用扩散）；③ Authority Creep（组织得好≠事实）。保命符：**raw 永远不可变可访问，wiki 是地图不是领土**。

---

## 3. obsidian-second-brain（Karpathy 的工程化）

把 gist 点子做成可安装 skill（v0.14，198 测试，MIT），核心扩展：

- **改写而非追加**：新来源更新已有人物/项目页，过期声明被替换（Karpathy 的 Ingest 具体化）。
- **矛盾自动调和** `/obsidian-reconcile`：按来源/日期/置信度解决冲突，不是只标记。
- **模式自动合成** `/obsidian-synthesize`：扫跨来源的未命名模式写综合页。
- **4 个定时 agent**：晨报 / 夜间巩固 / 周回顾 / 健康检查。
- **AI-first 笔记规则 7 条**：自包含上下文、`## For future agent` 前言、机器可读 frontmatter、每条声明带时间标记、来源原文保留、强制 `[[wikilink]]`、置信度分级。
- **写时校验 hook**：每次 Write/Edit 自动检查 AI-first 规则，不靠 AI 记规则。
- **接入方式**：主通道是**直接读写 vault 文件**（不需要 Obsidian 本体/MCP）；可选附带的 `obsidian-mcp-server` 供外部 MCP 客户端用。

---

## 4. 姜胡说 7 层模型（个人成长阶梯）

AI 知识库分 7 层，从低到高：

| 层 | 名称 | 说明 |
|---|---|---|
| L1 | 对话查询 | 当搜索引擎用，给思路 |
| L2 | Agent 干活 | 订票/生图/写文案，单任务自动化 |
| L3 | 从做到造 | 重复做一件事，告诉 AI 你是谁、你做什么——**90% 卡这层** |
| L4 | 结构化目录 | P.A.R.A，分门别类放好 |
| L5 | 索引定位 | 几百个文件，建索引目录，AI 自己去查 |
| L6 | 语义搜索 | 上千个文件，向量搜索 |
| L7 | 自动赚钱 | 知识原子 = 乐高积木，排列组合解决问题 |

**关键分界线**：L5（索引）→ L6（向量）的分界是「**几百个 vs 上千个文件**」——与 Karpathy「此规模不需要 RAG」、chuan「363 篇卡在分水岭前」的结论一致，**三方独立盖章**。
**独特价值**：L3「从做到造」讲人机分工（你提供身份/判断，AI 提供执行），是 Karpathy 没强调的成长视角；L7 有营销色彩，对 chuan 更实在的是把 L2/L3 的沉淀闭环做扎实。
**→ 已落地 N26/ADR-021（并入 wiki 第 6 类 `howto/`）**：HowToStore 委托 `Wiki.write`（白得 index/lint/双链/归并留痕）+ `howto_save/find/show` + `PersonaRole` 开工前自动注入「参考做法」，实现「重复做→沉淀原子→下次自动复用」闭环。

---

## 5. Coding CLI 记忆机制（CC / Codex / OpenCode / Hermes）

| 工具 | 指令层（人写） | 生成层（agent 写） | 召回 | 自动记忆 |
|---|---|---|---|---|
| **Claude Code** | `CLAUDE.md` 4 层作用域 | Auto Memory（`~/.claude/projects/*/memory/`） | `MEMORY.md` 索引（200 行封顶）+ 文件按需读 | ✅ 后台 subagent 提取 |
| **Codex** | `AGENTS.md` 分层 walk（32KiB 截断） | Memories（`~/.codex/memories/`） | 渐进式文件层级 + grep，**无向量** | ✅ 两阶段异步 pipeline |
| **OpenCode** | `AGENTS.md`/`CLAUDE.md`/`CONTEXT.md` 三文件 | **无内置**，靠社区插件（mem0/mnemosyne/close-session） | 插件补 | ⚠️ 官方弱 |
| **Hermes** | `AGENTS.md`（可选） | `MEMORY.md`+`USER.md`（`~/.hermes/memories/`） | **frozen snapshot** 全量注入 | ✅ `memory` 工具自管 |

**可借鉴的细节**：
- **Claude Code**：记忆严格限定 4 类（user/feedback/project/reference），其余丢弃；`MEMORY.md` 硬性「单行 ≤150 字符、总 ≤200 行」；后台 subagent 不在热路径写记忆；成功确认也记（防过度谨慎）。
- **Codex**：两阶段（gpt-5.1-mini 8 路并行提取 → gpt-5.3-codex 单 job 整合）；渐进式披露 `memory_summary.md → MEMORY.md → rollout_summaries → skills/`；usage-based 淘汰；**技能即记忆**（重复流程写成 SKILL.md）；源码明确「No vector search」。
- **Hermes**：frozen snapshot 会话开始冻结（保 prefix cache）；**满了不静默丢**（`memory` 工具报错逼 agent 当场整理）；记忆=事实（what），技能=流程（how）。

---

## 6. 为什么都不用 RAG（共识结论）

三个独立来源盖章：
1. **Karpathy**：100 篇/40 万字规模，`index.md` + `log.md` 就够，不需要向量库。
2. **姜胡说**：几百文件 L5 索引，上千文件才上 L6 向量。
3. **Codex 源码**：明确「无向量、无知识图谱，检索就是关键词 grep + 渐进文件访问」。

**原因**：① 记忆规模小（KB 级），grep 毫秒级，向量索引构建成本 > 收益；② 编码/记忆要求精确可审计（能逐字符追溯），向量是模糊黑盒；③ 真正瓶颈是 token 预算而非"找不到"（frozen snapshot/索引行数都在省 token）；④ 确定性可缓存、可调试、可 git 版本化。**触发 RAG 的信号**：语料跨几千篇、频繁模糊语义查询、需跨文档综合——chuan 目前远未达到。

---

## 7. 与 chuan N24 的映射（ADR-019）

| N24 要素 | 借鉴自 |
|---|---|
| 5 类目录（sources 只读 + topics/entities/analysis/projects） | Karpathy Raw→Wiki + Aivy |
| 实体页改写（同名归并 + deprecated 留痕） | obsidian-second-brain / Karpathy |
| `index.md` + `log.md` 双文件 | Karpathy（替代 RAG） |
| `reconcile` / `lint`（矛盾/孤立/死链/过时） | obsidian-second-brain + Karpathy |
| consolidation 蒸馏落 `sources/`（raw 不可变） | Karpathy（防幻觉扩散） |
| 归位 `ingest_sources`（原料→实体页，LLM 路由/确定性回退 + 幂等） | Karpathy Ingest / obsidian-second-brain 主动整理 |
| 确定性实现，LLM 不进关键路径 | 项目教训（免费模型不稳定） |

**N24 落地记录**：`chuan/wiki.py`（Wiki.write/import_source/ingest_sources/search_index/reconcile/lint）+ memory.py confidence + memory_tools.py wiki_write/wiki_search + consolidation 落点迁移 + memory_ops 每日维护（建目录+归位+lint）+ runtime_supervisor wiki_status。测试 17 例，全量 371 passed、2 skipped。详见 [DECISIONS.md](../plan/DECISIONS.md) ADR-019。

---

## 8. 待办（N24+ 未落地借鉴点）

- **P1**：ACI 预判注入（BaiLongma）；HUD SCENE 协议（BaiLongma）。
- **P2**：记忆加「类型 + 硬容量」约束（CC 4 类 + Hermes 2200 字符封顶）；技能即记忆（Codex/Hermes）；自动技能创建 `skill_creator.py`；岗位化 1:N 过渡；vault MCP server（obsidian-second-brain）；search_vault 检索工具。
- **P3**：视觉理解；工具市场；本地资源感知；HTTP API Gateway；文档口径修正（向量召回过度宣称 → 预留未实现）。
- **P4**：机器绑定加密；媒体生成；声纹防欺骗；Electron 桌面壳。

> 完整候选清单见 [ROADMAP.md](../plan/ROADMAP.md)「后续候选（N24+）」。

## 9. N27 自动沉淀落地（ADR-022，L3 闭环补「自动」）

**动机**：N26 沉淀靠 agent 显式 `howto_save`，免费模型不会主动调 → 闭环缺「自动」。
借鉴 Claude Code Auto Memory 的「后台提取」，但**加了人工确认闸**避免噪声化知识库。

**关键设计**：
- **自动提炼 ≠ 自动入库**：提炼产物先入 staging 队列（`data/memory/howto_staging/`，vault 之外，
  避免被 FTS/wiki 误扫），人工 `/howto approve` 才经 `Wiki.write` 入库——知识库只被确认过的
  做法增长，免费模型的脑补被人工把关挡在外面。
- **确定性门槛（先廉后贵）**：失败/任务<8 字/结果<40 字 → 秒拒，不用碰 FTS；`suggest ≥10` 分
  已覆盖 → 不重复沉淀；队列满 30 / 同任务已在队列 → 跳过。长度判断优先于召回，保回复路径不卡。
- **LLM 只做可选润色**：默认确定性提取（剥前缀取名、任务作触发场景、成功结果作怎么做），
  传 `brain` 才尝试 JSON 提炼，失败一律回退——沿用 wiki ingest「LLM + 确定性回退」，不赌免费模型。

**踩坑**：
- **门槛阈值别设太低**：结果 <40 字即「无实质」不沉淀，否则「好的，已搞定」这类空回复也会
  堆满队列；测试数据必须 ≥40 字才不会被门槛误拦（本迭代因测试样例 31 字被门槛拦下而先红后修）。
- **staging 必须放 vault 外**：若放 `notes/howto/_staging/`，`Wiki._refresh_index`/`lint` 的
  `rglob("*.md")` 会把它当成品页收录/报缺元数据——放 `data/memory/howto_staging/`（vault 之外）
  天然隔离，FTS reindex 只扫 `notes/`，零污染。
- **挂接点选 `_wrap_result` 而非 dispatch 内部**：显式 agent/单 agent/规划汇总三条返回路径都会
  经过它，一次挂接全覆盖；旁路 try/except，绝不阻断答复。
- **TUI/CLI 双入口复用同一套 bridge**：`/howto` 在 CLI（`main.py`）与 TUI（`tui/app.py`）各配
  一份入口，但都只调 `RuntimeSupervisor.howto_*` / `SupervisorBridge.howto_*`——面板只做展示，
  逻辑不复制，避免两套行为漂移。
- **确认交互进主流程要「精确匹配 + 旁路」**：`dispatch` 前置解析「确认/丢弃（可带名字）」，
  必须满足「队列非空 + 整条消息命中意图词/前缀」才拦截，否则 `好的`/`行` 这类泛词会误吞正常
  对话；多条候选时列清单让用户指定名字，绝不擅自挑一条。dispatch 结束后对比 staging 前后集合
  追加「[待确认]」提示——只提示本次新增，不重复打扰。
- **按名匹配别在解析阶段吞大小写**：意图前缀匹配用 `lower()` 没问题，但名字必须从**原文**提取、
  匹配时才忽略大小写——否则 `确认 整理 Obsidian 笔记并归档` 会被 lower 成 `整理 obsidian 笔记并归档`
  导致查不到候选（实跑演示抓到的 bug，已修复并补用例）。

## 10. N28 例行自动化闭环（ADR-023，系统自转）

**动机**：L3 闭环（N26 沉淀 / N27 自动沉淀+确认）已成型，但「重复做的事」还需要**被系统定期
重复执行**才算闭环最终形态——真用户故事「每周五自动出部署周报」：scheduler 给「到点」、howto
给「复用+沉淀」、wiki 给「归档归位」，三者串成自转。

**关键设计**：
- **例行任务一等概念**：`RoutineManager`（`data/routines.json` 磁盘真相，重启不丢），运行时
  `/routine` 管理；调度写法确定性解析（`fri 17:30` / `every 3600`，兼容 `@` 紧凑写法），不引入
  croniter、不赌模型——免费环境下每周+间隔两态覆盖现实例行。
- **每周调度**：`ScheduledJob` 增 `weekly` 字段 + `_next_weekly`（周一=0，已过顺延一周）；触发后
  按周重排而非 interval——这是「每周五」的关键增量。
- **闭环回流**：`on_routine_done` 回调把例行输出归档 wiki `sources/` 原料层，由既有每日 ingest
  归位成实体页——例行结果进知识库而不是「跑一次看一眼」。

**踩坑**：
- **演示/测试数据必须 ≥40 字**：`HowToDistiller` 结果 <40 字即「无实质」不沉淀——演示脚本里
  「收尾自动沉淀（待确认候选）」两次为空都是因为这个门槛拦下（跟 N27 同一个坑，务必记住）。
- **已有原子去重会让「首次运行」演示落空**：库里已有「部署周报」原子时，例行任务再跑会被
  `suggest ≥10` 门槛判定「已覆盖」而不沉淀新候选——这是**正确的去重**（避免重复原子），但演示
  「沉淀候选」环节必须先清空原子走首次运行路径。
- **FTS 库是全局共享的**：`data/memory_fts.db` 不随临时 vault 隔离，但 `recall` 会按命名空间根
  过滤候选路径（临时 vault 路径不在真实库路径下 → 命中被滤掉），所以跨 vault 不串数据。

## 11. N29 例行失败重试（ADR-024，任务级安全网）

**动机**：N28 例行闭环里，每周五 17:30 那次触发若撞上瞬时故障（LLM 5xx、worker 超时 600s、
事件循环抖动）就整周白跑且无补偿。N23 死胡同只管任务内单步，管不住整轮 `dispatch_to` 级别
失败——补一层调度器级退避重试安全网。

**关键设计**：
- **失败分级（确定性）**：仅 `KeyError`（worker 缺失，配置错误）判永久不重试；其余异常 + 退化
  内容（空回复 / `[PROACTIVE JOB ERROR]` / `[PROACTIVE JOB COMPLETED]` 占位符）一律瞬态可重试。
- **指数退避无抖动**：基数 60s、系数 2、封顶 30min；`retries` 可配（0=关闭向后兼容），
  `fail_count` 仅在本轮窗口累计，成功/耗尽后清零，下个触发点重新开始。
- **重试间隙静默**：退避期返回 `None` 不 `_record_alert`（防刷屏），顺带保证 `on_routine_done`
  归档钩子不触发——wiki 不会被中间失败污染；耗尽才发 ERROR 告警并正常重排。

**踩坑**：
- **失败分级先细分异常类型**：初版把 `RuntimeError` 整体判永久，测试里 `RuntimeError("boom")`
  （模拟瞬态故障）被当永久直接告警、退避逻辑永不触发。修正为仅 `KeyError` 判永久——未唤醒在
  调度器运行期本就不可达，重试无害且代码更简单。
- **run_pending 测试别用 run_immediately**：`run_immediately=True` 把 `next_run` 设为真实
  `datetime.now(UTC)`，固定 `now` 早于它会让任务永不触发（run_count=0 很迷惑）。测试显式
  `job.next_run = now` 再 `run_pending(now=now)`，时序断言才可复现。
- **退化内容也是失败**：LLM 偶发返回空消息 / 占位符，纯异常路径覆盖不到（invoke 成功但产出
  为空），必须用 `_is_failed_content` 兜底判定才可重试。

## 12. N30 自动技能创建（ADR-025，技能即记忆）

**动机**：L3「从做到造」已有知识原子（N26/N27），但知识原子是**知识**（FTS 召回注入参考
做法）。「干完活自动沉淀 SKILL.md」再进一步——沉淀**能力**（可注册技能，带触发关键词，命中
即注入复用做法），是 L3 闭环收尾。

**关键设计**：
- **prompt 型技能**：`skills/<name>.yaml`（`type: prompt`）= `trigger.keywords`（触发关键词）
  + `prompt`（可复用做法）；`Skill` 补齐 `matches`/`render_prompt`；不产生工具，只做注入参考。
- **复用 howto 模式**：自动提炼（确定性门槛）→ staging（`data/memory/skill_staging/`，
  vault 外不污染 FTS/wiki）→ `/skill` approve 写 YAML + `SkillRegistry.add` 运行时注册。
- **注入优先级**：`_inject_reference` 技能触发词命中优先 → howto FTS 兜底，避免双重注入；
  `_maybe_inject_skill` 每次现读 `skills/`（廉价）保证同会话新确认技能即时生效。

**踩坑**：
- **`Skill.kind` 由键推断而非 `type` 字段**：`Skill.__init__` 看 `mcp_server`/`handler` 键存在性，
  不是 `type: handler` 文案。测试里只写 `type: handler` 没给 `handler:` 键，被当成 prompt 型，
  `find_prompt_skill` 错误命中——测试定义 handler 技能必须带 `handler:` 键。
- **门槛按字符数，演示任务别太短**：`_MIN_TASK_CHARS=8` 会拦下 6 字任务（如「帮我部署周报」），
  演示/测试用 ≥8 字任务（如「帮我生成部署周报」），否则 maybe_create 静默返回 None 很迷惑。
- **关键词提炼 CJK 串去停词**：长 CJK 串（>6 字）退化为相邻二元组，保证后续任务子串命中；
  通用词（帮我/生成/整理…）进停词表，避免「帮我写首诗」被「生成」类技能误命中。
- **写 YAML 用 `yaml.safe_dump(allow_unicode=True)`**：手工拼字符串处理中文/冒号/换行易翻车，
  交给 safe_dump 保证 round-trip 安全（沿用 N24 wiki 的教训）。

## 13. N31 记忆「类型 + 硬容量」约束（ADR-026，CC 4 类 + Hermes 2200 封顶）

**动机**：N26/N27/N30 让记忆自动沉淀（howto 原子 + 技能 + 蒸馏），系统自转久了记忆会无限
增长——给长期记忆加「类型可分类 + 单条封顶」双约束，防止失控。

**关键设计**：
- **type 字段（CC 4 类）**：`fact` 事实 / `preference` 偏好 / `process` 过程 / `memory` 默认，
  写进 frontmatter；`recall(..., type)` 过滤、`MemoryHit.type` 暴露；非法值确定性归 `memory`。
- **单条硬容量**：`_MAX_DOC_CHARS=2200`（Hermes 参考值），`remember` 写入时确定性截断，
  覆盖写同样遵守——不误删 curated 知识，只防单条爆表。

**踩坑**：
- **只做单条封顶、不做总量淘汰**：总量淘汰（eviction）会误删 wiki/howto 等 curated 知识，
  「保 importance≥4」等规则复杂且难测。真需要时单独评估，别顺手加进本节点。
- **改 `_with_frontmatter` 加参要带默认值**：wiki.py 按 6 位置参数调用 `_with_frontmatter`
  （path/body/importance/confidence/tags/source），新 `type` 参数必须放第 7 位带默认，
  否则破坏 wiki 写入（全量回归立刻暴露）。
- **frontmatter 顺序 `type` 放末尾**：新字段加在 `source` 之后，既有的
  `test_remember_writes_frontmatter_meta` 等断言（只查子串）不受影响，但别改已有字段顺序。
