# 学习笔记 —— 媒体生成 V2（N56，视频/图片配置化 HTTP 后端）落地经验

> 日期：2026-08-27
> 目的：记录 N56 V2（ADR-058）把 video/image 占位升级为配置化 HTTP 后端的经验，供后续接真实 seedance/seedream 端点复用。

---

## 1. IDE 侧插件 ≠ 运行时 API：配置化后端骨架的正确姿势

**现象**：ROADMAP 标注「视频/图片后端 seedance/seedream 待接入，接口已留」。但 seedance/seedream 是 IDE 侧插件——只给 IDE 助手发 GenerateVideo/GenerateImage 指令，**没有暴露 chuan 运行时可直接调用的 HTTP 端点**，也无密钥。

**解法**（ADR-058）：无真实端点时别等，先交付**配置化协议骨架**：
- `config/config.yaml` `media:` 段：`video`/`image` 各含 `endpoint`（默认空）+ `api_key_env`（环境变量名，推荐不进 Git）+ `api_key_secret`（兜底 secrets.yaml，同 brain 惯例）+ `timeout`；
- handler 里 `urllib` 发通用请求：`POST JSON {"prompt": ...}` + `Bearer <key>`，响应二进制按 `Content-Type` 落盘 `data/media/`；
- 未配置（endpoint 空 / 密钥空）→ 返回可读提示，绝不抛错。

**教训**：插件能力（IDE 绑定）与运行时能力（可编程调用）是两回事。「配好即用」的骨架 + mock 服务器单测，能在无真实端点时把全链路（请求格式/鉴权/落盘/降级）锁死；真实端点到位只需改 config.yaml，零代码改动。要避免的是：把「已装插件」误当成「运行时可用」，继续占位拖到有端点才动。

---

## 2. monkeypatch 配置时别丢 handler 实际读取的字段

**现象**：单测里 `monkeypatch.setattr(mg, "_load_media_cfg", lambda: {"image": {"endpoint": mock, "timeout": 5}})` 后，全链路测试全部落到「未接入」提示——`_gen_http` 读 `cfg.get("api_key_env")` 拿到空，密钥永远读不到。

**根因**：mock 配置缺了 `api_key_env` 字段。handler 读配置是「字段级读取」不是「整段回传」，mock 必须带上 handler 真正消费的每个字段（endpoint/api_key_env/timeout…），只给一部分就等于部分功能关着。

**修复**：mock cfg 补全 `api_key_env: "SEEDREAM_API_KEY"` 后 18 passed。

**教训**：配置驱动的 handler，单测 mock 配置时对照真实 config.yaml 逐字段核对（或用真实 config 结构做底再改），别凭直觉只塞关心的字段。
