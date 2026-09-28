"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

数据先落到内存里方便各服务读写；每次发生写操作后会把全量数据快照到
data/snapshot.json，服务重启（以及浏览器刷新后重新进入）时自动读回，
已派车的摆渡任务不会因为重启而丢失。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.seed import SEED_ROWS

SNAPSHOT_PATH = Path(__file__).resolve().parent.parent / "data" / "snapshot.json"


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {}
        self._load_snapshot()
        # 快照里还没有的模块（例如新增的示例数据）回退到内置示例，保证既有入口可用。
        for name, rows in SEED_ROWS.items():
            self._tables.setdefault(name, [dict(row) for row in rows])

    def _load_snapshot(self) -> None:
        if not SNAPSHOT_PATH.exists():
            for name, rows in SEED_ROWS.items():
                self._tables[name] = [dict(row) for row in rows]
            return
        try:
            payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 快照损坏时不拖垮启动，退回示例数据。
            payload = {}
        tables = payload.get("tables") if isinstance(payload, dict) else None
        if isinstance(tables, dict):
            for name, rows in tables.items():
                if isinstance(rows, list):
                    self._tables[name] = [dict(row) for row in rows if isinstance(row, dict)]

    def save_snapshot(self) -> None:
        """把当前全量数据原子写入快照文件，避免写一半被读到。"""
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "tables": self._tables}
        fd, tmp_name = tempfile.mkstemp(prefix=".snapshot-", suffix=".tmp", dir=SNAPSHOT_PATH.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(tmp_name, SNAPSHOT_PATH)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def next_id(self, module: str) -> int:
        return max((int(row.get("id", 0)) for row in self.rows(module)), default=0) + 1

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
