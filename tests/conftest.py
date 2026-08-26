"""全局测试配置。

GUI 元素记忆库（N58，handlers/gui_memory）默认写 data/gui/elements.db。

注意一个 import 陷阱：SkillRegistry 会把 skills/ 加进 sys.path，使
`handlers.gui_memory`（生产路径，gui_automation 内懒加载用）与
`skills.handlers.gui_memory`（命名空间包路径，测试 import 用）成为**两个不同的模块对象**，
各自的 _DB 模块全局不互通。因此这里必须对两个对象都补丁到同一个临时 DB，
否则任何走 handlers.gui_memory 的真实调用都会污染 data/gui/elements.db。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_DIR = _PROJECT_ROOT / "skills"
if str(_SKILLS_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILLS_DIR))

import handlers.gui_memory as _prod_gm  # noqa: E402 - skills/ 入 path 后的生产路径
import skills.handlers.gui_memory as _ns_gm  # noqa: E402 - 命名空间包路径


@pytest.fixture(autouse=True)
def _isolate_gui_memory_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把两个模块对象（handlers.* 与 skills.handlers.*）的 _DB 都指向同一临时 DB。"""
    db = tmp_path / "gui_elements_test.db"
    monkeypatch.setattr(_prod_gm, "_DB", db)
    monkeypatch.setattr(_ns_gm, "_DB", db)
    yield
