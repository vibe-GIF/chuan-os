# 学习笔记 —— 语音打断 / 后台委派两处并发 bug 修复

> 日期：2026-08-24
> 目的：记录对抗性审查中定位并修复的两处并发时序 bug。

---

## 1. 唤醒应答播报中被 barge-in 打断 → 后续「重复 reset」丢话语

**现象**：免提唤醒后，幕僚长播报应答（如「在的，先生。请讲。」），用户在播报过程中开口打断（barge-in）继续说话，有时前半句被吞、识别为空或残缺——「说话没反应」。

**根因**：两条路径同时操作 `UtteranceListener` 的录音状态，时序竞争：

1. 麦线程在 `_feed_idle`（armed 态）检测到 barge-in → 调 `start_recording(interrupted=True)`，此时 `_recording=True`、`armed=False`，开始收集话语；
2. 主循环 `_run_alwayson_loop` 里 `playback_done()` 检测到应答 TTS 线程已退出 → 在 `awake_pending` 分支里**再次**调 `listener.start_recording()`，把正在进行的 barge-in 录音整体 reset——已录到的半句话被清空。

**修复**（`chuan/voice/main.py`）：`awake_pending` 分支在 reset 前先判 `listener.busy`（= `_recording or armed`）。barge-in 已转入录音时 `busy=True`，跳过重复 reset；正常播完（无打断）时 `busy=False`，照常开录。

**教训**：状态机里「播报完成 → 自动转录音」与「打断 → 立即转录音」是两条独立来源，汇合点必须幂等——用 `busy` 门控，别把第二条路径的 reset 强加到第一条已在进行的录音上。

---

## 2. AgentHarness 终态裁剪会把「依赖被裁掉」的 pending 任务永久卡死

**现象**：后台委派（fire-and-forget）开启依赖 DAG 时，若已完成任务累积超过 `_MAX_KEEP_DONE`（200）条，最旧的终态任务被 `_mark_done` 从 `_tasks` 裁剪掉；此时仍 pending 且依赖那条被裁任务的下游任务，依赖检查读不到前置 → 永远无法推进到 ready。

**根因**：`_promote_pending` 用 `self._tasks.get(d, {}).get("status") in _TERMINAL` 判断依赖是否结束。依赖一旦被裁剪，`get` 返回空 dict，`status` 为 `None`，`None in _TERMINAL` 恒为 False。

**修复**（`chuan/gateway/agent_harness.py`）：抽出 `_dep_ok(d)`——依赖不在 `_tasks` 里即视为「已被裁剪的终态任务，视作已结束」（`submit` 已保证依赖在提交时存在过），让下游照常推进。

**教训**：内存上限裁剪与依赖解析必须一致——「删除」具有语义（这里是「已完成的历史任务」），依赖侧要把「查不到」映射回正确的终态语义，而不是静默当非终态。