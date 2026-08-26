# 学习笔记 —— GUI 自动化（N57，借鉴影刀 RPA 能力）落地经验

> 日期：2026-08-25
> 目的：记录 N57 四阶段落地过程中的关键经验与坑，供后续 GUI 自动化扩展（UI-TARS 视觉接管、Electron 壳）复用。

---

## 1. 后台静默 ≠ 不移动鼠标：pywinauto 三种「点击」的语义差异

**现象**：同一句「点击按钮」，阶段 2 实现时一开始打算统一用 `click_input()`，但那是**真实移动鼠标**，会抢用户焦点——违背「后台静默模式（UIA 不抢鼠标键盘）」的定位。

**区分**（pywinauto 提供三种激活方式，按优先级排）：
- `invoke()`：走 UIA Invoke 模式，纯消息，**不动鼠标**（最静默）；
- `click()`：client-side，发 WM_CLICK 消息，**不动鼠标**（次静默）；
- `click_input()`：**真实移动鼠标到控件中心再点**（前台，抢焦点）。

**修复**：`_silent_activate` 按 `invoke → click` 顺序尝试，都不行才降级 `click_input`/`pyautogui`。`gui_click` 的 `mode` 参数把「静默/前台」变成显式选择，`auto` 下静默失败才前台兜底。

**教训**：RPA 类能力「静默」与「可靠」天然冲突——UIA 消息式点击最静默但兼容性差（自绘控件收不到），前台坐标最可靠但抢焦点。**分层降级 + 显式 mode** 是解法，别二选一。

---

## 2. handler 函数内 `import mss` / `import pyautogui` 的单测 mock 姿势

**现象**：惰性导入（函数内 `import mss`）让 `monkeypatch.setattr(mss, "mss", ...)` 失效——函数内的 import 拿到的是**模块对象**，改模块属性能命中，但 `mss` 包本身没有 `mss` 属性（是 `__init__.py` 里 `from ._mss import mss` 再经包机制暴露的），改不动。

**修复**（`tests/test_gui_automation.py` 的 `_patch_mss` / `_patch_pyautogui`）：**整体替换 `sys.modules['mss']` / `sys.modules['pyautogui']` 为自建 fake 模块**（`types.ModuleType` + 记录调用的 lambda），函数内 `import` 命中 fake。顺带能记录调用参数（click/scroll/hotkey 断言用）。

**教训**：mock 第三方包时，先确认它是「模块属性」还是「包导出」；包导出类（mss）直接换整个 `sys.modules` 条目最省事，还能顺带断言调用。

---

## 3. verify（截图留痕）与动作日志是两层，别耦合

**现象**：阶段 3 设计 `gui_operate(verify=False)` 时，一度想「verify=False 就完全不落盘」。但 ADR-054 的「静默可见性」= **前后截图 + 动作日志**两件独立的事：截图为审计留痕（重，可关），日志为操作记录（轻，常开）。

**修复**：`verify` 只控制 `gui_screenshot()` 前后调用；`_audit` 无条件写 `data/gui/actions.log`（含 mode/timeout/目标/前后截图路径）。测试据此拆开断言：`verify=False` → 截图 0 次，但日志文件仍存在且含动作。

**教训**：把「重副作用」和「轻副作用」耦合在一个开关里是设计失误；分层开关 + 对应测试，语义才清楚。

---

## 4. 测「留痕失败不阻断」要用不可写路径，别 monkeypatch 抛异常

**现象**：`_audit` 内部有 try/except 吞错（对齐 ADR-007 静默降级），所以正常调用**永不抛**。一开始用 `monkeypatch.setattr(ga, "_audit", 抛异常函数)` 想验证「审计失败不阻断」，结果 OSError 直接冒到测试外——因为异常发生在**进入** `_audit` 之前，根本没测到 `_audit` 的吞错逻辑。

**修复**：改为给 `_ACTION_LOG` 指向「父路径是普通文件」的路径 → 真实 `_audit` 内部 `mkdir` 必然失败 → 验证它静默返回、主流程不受影响。

**教训**：mock 一个「真实实现里永不抛错」的函数来模拟异常，测的是 mock 不是实现。要触发真实路径的失败分支，得用真实的不可行输入（不可写路径），而不是替换函数。

---

## 5. pywinauto wrapper 方法是动态暴露的，`hasattr` 判断不可靠

**现象**：`set_edit_text` 只在 Edit 类 wrapper 上可用，但 pywinauto 的 wrapper 通过 `__getattr__` 动态代理方法——`hasattr(ctl, "set_edit_text")` 可能返回 True 而调用时抛 `AttributeError`（"not implemented for this control"），也可能反之。

**修复**：`gui_type` 里不依赖 `hasattr`，直接 try/except 调 `set_edit_text`，失败落前台 `type_keys` 兜底。

**教训**：对动态代理 API（pywinauto、部分 ORM），**用「尝试调用 + 捕获」代替「hasattr 预判」**，并始终保留降级路径。

---

## 6. 热键安全闸用规范化匹配，但别过度归一化

**现象**：安全闸要拦 `Ctrl+Alt+Del` 的任意变体（大小写/空格），所以 `_normalize_hotkey` 做「小写 + 按 + 分段 + 修饰键排前」，`CTRL + ALT + DEL` → `ctrl+alt+del` 命中拦截。但**不能**把 `escape` 也归一成 `esc`——`_to_pywinauto_keys` 对两者都映射 `{ESC}`，归一化只需保证「同一语义键名一致」用于拦截，别名映射交给转换层。

**修复**：归一化只做大小写/空格/顺序标准化（安全匹配用），键名别名（esc/escape、del/delete）由 `_to_pywinauto_keys` 转换层统一映射。

**教训**：安全校验的「规范化」和「语义映射」是两个职责——前者只管字符形态，后者管同义词展开；混在一起会让拦截规则和转换规则互相污染。

---

## 7. SkillRegistry 把 skills/ 加 sys.path → `handlers.*` 与 `skills.handlers.*` 是两个模块对象

**现象**（N58 元素记忆库测试）：真实 DB `data/gui/elements.db` 被测试污染，fixture 明明把 `_DB` 补丁到了 tmp，却仍拦截不住；单独跑 GUI 测试通过、全量跑就挂。

**根因**：`SkillRegistry.__init__`（skill_loader.py）会把 `skills/` 目录插入 `sys.path`（为让 `handlers.xxx` 可导入）。于是同一个 `skills/handlers/gui_memory.py` 文件可能被加载成**两个独立模块对象**：
- `handlers.gui_memory`——gui_automation 内 `from handlers.gui_memory import ...` 懒加载的（生产路径，走 skills/ 入 path）；
- `skills.handlers.gui_memory`——测试里 `from skills.handlers import gui_memory` 的（命名空间包路径，走项目根）。

两者 `_DB` 模块全局**不互通**。只补丁 `skills.handlers.gui_memory._DB` 时，gui_automation 实际用的是 `handlers.gui_memory`（真实路径）→ 全量跑时某个测试先实例化了 SkillRegistry，`skills/` 入 path，内存才真正启用并写真实 DB（单独跑 GUI 测试没实例化 SkillRegistry，`from handlers.gui_memory import` 直接 ImportError 被静默吞掉，反而全绿）。

**修复**（`tests/conftest.py`）：autouse fixture 对**两个模块对象**的 `_DB` 同时补丁到同一个 tmp DB；conftest 顶部显式 `import handlers.gui_memory` + `import skills.handlers.gui_memory` 先拿到两个对象。

**教训**：命名空间包 + sys.path 动态插入会制造「同文件双模块对象」的幻影隔离。凡是「生产代码懒导入 A，测试代码导入 B」的配对，必须验证 `sys.modules` 里是不是同一个对象；DB/全局状态类模块尤其要防——fixture 补丁对象 ≠ 被测代码实际用的对象，测试就形同虚设（且单测/全量行为不一致正是此坑的警报）。
