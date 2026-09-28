"""摆渡接送业务规则：波次派车、车辆占用校验、状态流转与筛选口径都收在这里。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "shuttle"
WAVE_MODULE = "shuttle_wave"
VEHICLE_MODULE = "shuttle_vehicle"
REQUIRED_FIELDS = ["任务编号", "关联航班", "车辆编号"]
STATUS_ORDER = ["待发车", "行驶中", "已送达", "已取消"]
# 待发车与行驶中的任务仍占着车辆；已送达、已取消释放运力。
ACTIVE_STATUSES = {"待发车", "行驶中"}
ACTION_RULES = {"安排发车": "行驶中", "确认送达": "已送达", "取消任务": "已取消"}
NEGATIVE_ACTIONS = []

TIME_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"]
TIME_PATTERN = re.compile(r"SHUT-(\d+)$")


def _parse_time(value: Any) -> datetime | None:
    """把 'YYYY-MM-DD HH:MM' 之类的时刻转成可比较的对象；识别不了返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_passengers(value: Any) -> int:
    """乘客人数容错解析：数字、数字字符串都能转，无法识别按 0 处理。"""
    if isinstance(value, (int, float)):
        return max(int(value), 0)
    matched = re.search(r"\d+", str(value or ""))
    return int(matched.group()) if matched else 0


def _overlap(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    """时段是否重叠：采用半开区间 [出发, 到达)，首尾相接不算冲突。"""
    return start_a < end_b and start_b < end_a


class ShuttleService:
    # ---------- 列表与明细 ----------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        rows = self._filtered_rows(keyword=keyword, status=status)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total, self.summarize(rows)

    def _filtered_rows(self, *, keyword: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return rows

    def summarize(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        """清点人数与任务量：列表页统计卡和导出汇总都取同一份口径。"""
        return {
            "待发车任务": sum(1 for row in rows if row.get("status") == "待发车"),
            "摆渡总人次": sum(_parse_passengers(row.get("乘客人数")) for row in rows),
            "取消任务数": sum(1 for row in rows if row.get("status") == "已取消"),
            "任务总数": len(rows),
        }

    def export_rows(
        self, *, keyword: str | None = None, status: str | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        rows = self._filtered_rows(keyword=keyword, status=status)
        return rows, self.summarize(rows)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        entry = self._build_entry(
            task_code=str(values["任务编号"]).strip(),
            flight=str(values["关联航班"]).strip(),
            vehicle_code=str(values["车辆编号"]).strip(),
            passengers=_parse_passengers(values.get("乘客人数")),
            depart_at=str(values.get("出发时刻") or "").strip(),
            arrive_at=str(values.get("到达时刻") or "").strip(),
            driver=str(values.get("驾驶人员") or "").strip(),
        )
        store.rows(MODULE).append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"摆渡任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于摆渡接送可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        # 列表与导出都展示「摆渡状态」列，随状态流转一并更新，避免两列口径不一致。
        entry["摆渡状态"] = target
        return entry, f"摆渡任务已{action}"

    # ---------- 航班波次与摆渡车辆 ----------
    def list_waves(self) -> list[dict[str, Any]]:
        return store.rows(WAVE_MODULE)

    def get_wave(self, wave_id: int) -> dict[str, Any] | None:
        return store.find(WAVE_MODULE, wave_id)

    def list_vehicles(self) -> list[dict[str, Any]]:
        return store.rows(VEHICLE_MODULE)

    # ---------- 波次派车 ----------
    def preview_wave(self, wave_id: int) -> dict[str, Any]:
        """生成派车方案但不落库：可派任务、车辆不足、时段冲突分别清点。"""
        wave = self.get_wave(wave_id)
        if wave is None:
            return {"ok": False, "message": f"航班波次 {wave_id} 不存在", "wave": None, "candidates": []}
        candidates = self._plan_wave(wave)
        return self._plan_result(wave, candidates, message="派车方案已生成，可勾选可派任务提交")

    def commit_wave(self, wave_id: int, selected: list[int]) -> dict[str, Any]:
        """按勾选的候选序号落库；提交时重新校验，挡住车辆不足与时段冲突的部分。"""
        wave = self.get_wave(wave_id)
        if wave is None:
            return {"ok": False, "message": f"航班波次 {wave_id} 不存在", "wave": None, "candidates": [], "created": []}
        selected_set = {int(index) for index in selected}

        plan = self._plan_wave(wave)
        valid_indexes = {candidate["index"] for candidate in plan}
        unknown = sorted(selected_set - valid_indexes)
        if unknown:
            return {"ok": False, "message": f"候选序号 {', '.join(map(str, unknown))} 不在当前方案里，请重新预览后再提交", "wave": wave, "candidates": plan, "created": []}
        if not selected_set:
            return {"ok": False, "message": "未勾选任何可派任务，没有内容可提交", "wave": wave, "candidates": plan, "created": []}

        created: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for candidate in plan:
            if candidate["index"] not in selected_set:
                continue
            if candidate["blocked"]:
                # 预览后数据可能变化（车辆被别处占用），提交时以最新校验结果为准。
                skipped.append({"index": candidate["index"], "block_type": candidate["block_type"], "reason": candidate["reason"]})
                continue
            entry = self._build_entry(
                task_code=self._next_task_code(),
                flight=candidate["关联航班"],
                vehicle_code=candidate["车辆编号"],
                passengers=candidate["乘客人数"],
                depart_at=candidate["出发时刻"],
                arrive_at=candidate["到达时刻"],
                driver=candidate.get("驾驶人员") or "",
                wave_code=str(wave.get("波次编号") or ""),
            )
            store.rows(MODULE).append(entry)
            created.append(entry)

        latest = self._plan_wave(wave)
        if created and not skipped:
            message = f"已按波次生成 {len(created)} 条摆渡任务"
        elif created:
            message = f"已生成 {len(created)} 条摆渡任务，{len(skipped)} 条因车辆不足或时段冲突未提交"
        else:
            message = f"所选 {len(skipped)} 条任务均未通过车辆占用校验，未生成任何摆渡任务"
        result = self._plan_result(wave, latest, message=message)
        result["created"] = created
        result["skipped"] = skipped
        result["ok"] = bool(created)
        return result

    def _plan_result(self, wave: dict[str, Any], candidates: list[dict[str, Any]], *, message: str) -> dict[str, Any]:
        ready = [c for c in candidates if not c["blocked"]]
        shortage = [c for c in candidates if c["blocked"] and c["block_type"] == "车辆不足"]
        conflicts = [c for c in candidates if c["blocked"] and c["block_type"] == "时段冲突"]
        return {
            "ok": True,
            "message": message,
            "wave": wave,
            "candidates": candidates,
            "ready_count": len(ready),
            "shortage_count": len(shortage),
            "conflict_count": len(conflicts),
            "created": [],
            "skipped": [],
        }

    def _plan_wave(self, wave: dict[str, Any]) -> list[dict[str, Any]]:
        """核心算法：逐航班按乘客人数拆条、按车辆容量 best-fit 派车。

        候选序号在整个波次内递增，预测任务编号只用于预览展示与冲突引用。
        """
        vehicles = [v for v in self.list_vehicles() if str(v.get("车辆状态") or "").strip() == "可用"]
        occupancy = self._current_occupancy()
        # 本波次内已预占的车辆时段：车辆编号 -> [(开始, 结束, 预测任务编号)]
        claimed: dict[str, list[tuple[datetime, datetime, str]]] = {}
        next_code = self._next_task_code()
        candidates: list[dict[str, Any]] = []
        index = 0

        for flight_info in wave.get("flights", []):
            flight_no = str(flight_info.get("航班号") or "").strip()
            passengers = _parse_passengers(flight_info.get("乘客人数"))
            depart_text = str(flight_info.get("出发时刻") or "").strip()
            arrive_text = str(flight_info.get("到达时刻") or "").strip()
            depart_at = _parse_time(depart_text)
            arrive_at = _parse_time(arrive_text)

            # 先校验出发时刻与到达时刻：识别不了或先后颠倒，整组乘客单独列为时段冲突。
            if depart_at is None or arrive_at is None:
                bad_field = "出发时刻" if depart_at is None else "到达时刻"
                candidates.append(self._blocked_candidate(
                    index, flight_no, passengers, depart_text, arrive_text,
                    "时段冲突", f"{bad_field}「{depart_text if depart_at is None else arrive_text}」无法识别，需为 YYYY-MM-DD HH:MM 格式",
                ))
                index += 1
                continue
            if depart_at >= arrive_at:
                candidates.append(self._blocked_candidate(
                    index, flight_no, passengers, depart_text, arrive_text,
                    "时段冲突", f"出发时刻 {depart_text} 不早于到达时刻 {arrive_text}，时刻先后校验未通过",
                ))
                index += 1
                continue

            remaining = passengers
            while remaining > 0:
                assignment = self._pick_vehicle(vehicles, occupancy, claimed, depart_at, arrive_at, remaining)
                if assignment is None:
                    # 一辆能派的车都没有（全部维修/停用）。
                    busy_codes = self._busy_descriptions(vehicles, occupancy, claimed, depart_at, arrive_at)
                    reason = f"当前没有可用摆渡车辆，剩余 {remaining} 人无车可派"
                    if busy_codes:
                        reason += f"（{'；'.join(busy_codes)}）"
                    candidates.append(self._blocked_candidate(
                        index, flight_no, remaining, depart_text, arrive_text, "车辆不足", reason,
                        conflict_with=self._busy_task_codes(occupancy, claimed, depart_at, arrive_at),
                    ))
                    index += 1
                    break

                vehicle, take_seats, status = assignment
                if status == "shortage":
                    # 波次并发超过可用运力：候选车辆全部在本波次内被占，列为车辆不足。
                    busy_codes = self._busy_descriptions(vehicles, occupancy, claimed, depart_at, arrive_at)
                    reason = f"波次并发超过可用运力，剩余 {remaining} 人无车可派"
                    if busy_codes:
                        reason += f"（{'；'.join(busy_codes)}）"
                    candidates.append(self._blocked_candidate(
                        index, flight_no, remaining, depart_text, arrive_text, "车辆不足", reason,
                    ))
                    index += 1
                    break

                if status == "occupied":
                    # 与已落库任务在同一车辆同一时段重叠：重复派车必须挡住并指出冲突任务。
                    occupied_codes = [code for start, end, code in occupancy.get(vehicle["车辆编号"], [])
                                      if _overlap(depart_at, arrive_at, start, end)]
                    reason = (
                        f"车辆 {vehicle['车辆编号']} 在 {depart_text}~{arrive_text} 已被任务 "
                        f"{('、'.join(occupied_codes))} 占用，不能重复派车"
                    )
                    candidates.append(self._blocked_candidate(
                        index, flight_no, remaining, depart_text, arrive_text,
                        "时段冲突", reason, conflict_with=occupied_codes,
                    ))
                    index += 1
                    break

                # status == "free"：按容量最合适的车派一条任务。
                predicted_code = next_code
                next_code = self._bump_code(next_code)
                candidate = {
                    "index": index,
                    "关联航班": flight_no,
                    "乘客人数": take_seats,
                    "出发时刻": depart_text,
                    "到达时刻": arrive_text,
                    "任务编号": predicted_code,
                    "车辆编号": vehicle["车辆编号"],
                    "核定载客": int(vehicle.get("核定载客") or 0),
                    "驾驶人员": str(vehicle.get("默认驾驶人员") or ""),
                    "blocked": False,
                    "block_type": None,
                    "reason": "",
                    "conflict_with": [],
                }
                candidates.append(candidate)
                claimed.setdefault(vehicle["车辆编号"], []).append((depart_at, arrive_at, predicted_code))
                remaining -= take_seats
                index += 1

        return candidates

    def _pick_vehicle(
        self,
        vehicles: list[dict[str, Any]],
        occupancy: dict[str, list[tuple[datetime, datetime, str]]],
        claimed: dict[str, list[tuple[datetime, datetime, str]]],
        depart_at: datetime,
        arrive_at: datetime,
        remaining: int,
    ) -> tuple[dict[str, Any], int, str] | None:
        """挑一辆车，返回 (车辆, 本车乘坐人数, 状态)。

        状态取值：
        - free：有空闲车，按容量 best-fit 派一条；
        - shortage：可用车辆在本波次内已被前序候选占满（并发超过运力）；
        - occupied：没有空闲车，且空闲车是被已落库任务占住（重复派车，时段冲突）；
        - None：车队里没有可用车辆（全部维修/停用）。
        """
        if not vehicles:
            return None

        def overlaps(items: list[tuple[datetime, datetime, str]]) -> bool:
            return any(_overlap(depart_at, arrive_at, start, end) for start, end, _task in items)

        def is_free(vehicle: dict[str, Any]) -> bool:
            code = vehicle["车辆编号"]
            return not overlaps(occupancy.get(code, []) + claimed.get(code, []))

        free_vehicles = [v for v in vehicles if is_free(v)]
        if free_vehicles:
            capacities = [(int(v.get("核定载客") or 0), int(v.get("id") or 0), v) for v in free_vehicles]
            # 先找容量够装剩余乘客的最小车（少占座位）；都装不下就用容量最大的车拆条。
            fitting = [item for item in capacities if item[0] >= remaining]
            pool = min(fitting, key=lambda item: (item[0], item[1])) if fitting else max(capacities, key=lambda item: item[0])
            capacity, _, vehicle = pool
            return vehicle, min(remaining, capacity), "free"

        # 没有空闲车：先看是不是被已落库任务挡着——同一车辆同一时段重复派车，按冲突处理。
        for vehicle in vehicles:
            if overlaps(occupancy.get(vehicle["车辆编号"], [])):
                return vehicle, remaining, "occupied"
        # 否则是本波次前序候选把可用车辆都预占了：波次并发超过运力，按车辆不足处理。
        return vehicles[0], remaining, "shortage"

    def _current_occupancy(self) -> dict[str, list[tuple[datetime, datetime, str]]]:
        """汇总当前在档任务对车辆的占用：车辆编号 -> [(出发, 到达, 任务编号)]。"""
        occupancy: dict[str, list[tuple[datetime, datetime, str]]] = {}
        for row in store.rows(MODULE):
            if row.get("status") not in ACTIVE_STATUSES:
                continue
            start = _parse_time(row.get("出发时刻"))
            end = _parse_time(row.get("到达时刻"))
            vehicle = str(row.get("车辆编号") or "").strip()
            if start is None or end is None or not vehicle:
                continue
            occupancy.setdefault(vehicle, []).append((start, end, str(row.get("任务编号") or "")))
        return occupancy

    def _busy_descriptions(
        self,
        vehicles: list[dict[str, Any]],
        occupancy: dict[str, list[tuple[datetime, datetime, str]]],
        claimed: dict[str, list[tuple[datetime, datetime, str]]],
        depart_at: datetime,
        arrive_at: datetime,
    ) -> list[str]:
        descriptions = []
        for vehicle in vehicles:
            code = vehicle["车辆编号"]
            for start, end, task in occupancy.get(code, []) + claimed.get(code, []):
                if _overlap(depart_at, arrive_at, start, end):
                    descriptions.append(f"{code} 已派 {task}")
        return descriptions

    def _busy_task_codes(
        self,
        occupancy: dict[str, list[tuple[datetime, datetime, str]]],
        claimed: dict[str, list[tuple[datetime, datetime, str]]],
        depart_at: datetime,
        arrive_at: datetime,
    ) -> list[str]:
        codes: list[str] = []
        for items in list(occupancy.values()) + list(claimed.values()):
            for start, end, task in items:
                if _overlap(depart_at, arrive_at, start, end) and task and task not in codes:
                    codes.append(task)
        return codes

    def _blocked_candidate(
        self,
        index: int,
        flight_no: str,
        passengers: int,
        depart_text: str,
        arrive_text: str,
        block_type: str,
        reason: str,
        *,
        conflict_with: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "index": index,
            "关联航班": flight_no,
            "乘客人数": passengers,
            "出发时刻": depart_text,
            "到达时刻": arrive_text,
            "任务编号": None,
            "车辆编号": None,
            "核定载客": None,
            "驾驶人员": None,
            "blocked": True,
            "block_type": block_type,
            "reason": reason,
            "conflict_with": conflict_with or [],
        }

    # ---------- 编号与落库 ----------
    def _next_task_code(self) -> str:
        numbers = [int(m.group(1)) for row in store.rows(MODULE)
                   if (m := TIME_PATTERN.search(str(row.get("任务编号") or "")))]
        return f"SHUT-{max(numbers, default=0) + 1:04d}"

    def _bump_code(self, code: str) -> str:
        matched = TIME_PATTERN.search(code)
        return f"SHUT-{int(matched.group(1)) + 1:04d}" if matched else code

    def _build_entry(
        self,
        *,
        task_code: str,
        flight: str,
        vehicle_code: str,
        passengers: int,
        depart_at: str,
        arrive_at: str,
        driver: str,
        wave_code: str = "",
    ) -> dict[str, Any]:
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "任务编号": task_code,
            "关联航班": flight,
            "车辆编号": vehicle_code,
            "乘客人数": passengers,
            "出发时刻": depart_at,
            "到达时刻": arrive_at,
            "驾驶人员": driver,
            "status": STATUS_ORDER[0],
            "摆渡状态": STATUS_ORDER[0],
            "pending": True,
            "abnormal": False,
        }
        if wave_code:
            entry["来源波次"] = wave_code
        return entry
