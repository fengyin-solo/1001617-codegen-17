"""摆渡接送业务规则：状态流转、字段校验、按航班波次派车与车辆占用校验都收在这里。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "shuttle"
WAVE_MODULE = "shuttle_wave"
WAVE_FLIGHT_MODULE = "shuttle_wave_flight"
VEHICLE_MODULE = "shuttle_vehicle"

REQUIRED_FIELDS = ["任务编号", "关联航班", "车辆编号"]
STATUS_ORDER = ["待发车", "行驶中", "已送达", "已取消"]
ACTION_RULES = {"安排发车": "行驶中", "确认送达": "已送达", "取消任务": "已取消"}
NEGATIVE_ACTIONS = []

# 已取消、已送达的任务不再占用车辆；只有待发车、行驶中会参与时段冲突判断。
ACTIVE_STATUSES = ["待发车", "行驶中"]

TASK_PREFIX = "SHUT"
DRIVER_PLACEHOLDER = "待安排"
BLOCKED_VEHICLE_STATUSES = ["维修中", "停用"]


def parse_time(value: Any) -> datetime | None:
    """把 "YYYY-MM-DD HH:MM" 或 "HH:MM" 解析成可比较的时刻；样例占位文本解析不了时返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%H:%M"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def parse_head_count(value: Any) -> int:
    """乘客人数转整数；历史示例里是占位文本时按 0 计，保证清点口径不报错。"""
    try:
        return max(int(float(value)), 0)
    except (TypeError, ValueError):
        return 0


def overlaps(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    """两个半开时段是否重叠：首尾相接（A 结束正好 B 出发）不算占用冲突。"""
    return start_a < end_b and start_b < end_a


def decorate(row: dict[str, Any]) -> dict[str, Any]:
    """给列表/导出补展示字段：摆渡状态与内部状态对齐，波次编号便于核对来源。"""
    row = dict(row)
    row["摆渡状态"] = row.get("status")
    wave_id = row.get("波次ID")
    if wave_id is not None:
        wave = store.find(WAVE_MODULE, int(wave_id))
        row["航班波次"] = wave.get("波次编号") if wave else "—"
    else:
        row.setdefault("航班波次", "—")
    row.setdefault("驾驶人员", DRIVER_PLACEHOLDER)
    return row


class ShuttleService:
    # ---------- 列表与明细 ----------

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        wave: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int, int, dict[str, int]]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("任务编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if wave:
            wave_rows = [
                item for item in store.rows(WAVE_MODULE)
                if wave in str(item.get("波次编号", "")) or wave in str(item.get("波次名称", ""))
            ]
            wave_ids = {int(item["id"]) for item in wave_rows}
            rows = [row for row in rows if int(row.get("波次ID", 0) or 0) in wave_ids]
        total = len(rows)
        head_count = sum(parse_head_count(row.get("乘客人数")) for row in rows)
        status_counts = {
            label: sum(1 for row in rows if row.get("status") == label)
            for label in STATUS_ORDER
        }
        start = max(page - 1, 0) * size
        page_rows = [decorate(row) for row in rows[start:start + size]]
        return page_rows, total, head_count, status_counts

    def all_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        wave: str | None = None,
    ) -> list[dict[str, Any]]:
        rows, _, _, _ = self.list_entries(keyword=keyword, status=status, wave=wave, page=1, size=100000)
        return rows

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        return decorate(row) if row else None

    # ---------- 单条登记（保留既有动作） ----------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        entry = {"id": store.next_id(MODULE)}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        for optional in ("乘客人数", "出发时刻", "到达时刻", "驾驶人员"):
            if optional in values:
                entry[optional] = values.get(optional)
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        store.rows(MODULE).append(entry)
        return decorate(entry), []

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
        return decorate(entry), f"摆渡任务已{action}"

    # ---------- 航班波次派车 ----------

    def list_waves(self) -> list[dict[str, Any]]:
        waves = sorted(store.rows(WAVE_MODULE), key=lambda row: (row.get("波次日期", ""), row.get("id", 0)))
        flight_rows = store.rows(WAVE_FLIGHT_MODULE)
        result = []
        for wave in waves:
            wave_id = int(wave["id"])
            flights = [row for row in flight_rows if int(row.get("波次ID", 0)) == wave_id]
            result.append({
                "id": wave_id,
                "波次编号": wave.get("波次编号"),
                "波次名称": wave.get("波次名称"),
                "波次日期": wave.get("波次日期"),
                "航班数量": len(flights),
                "乘客合计": sum(parse_head_count(row.get("乘客人数")) for row in flights),
            })
        return result

    def _available_vehicles(self) -> list[dict[str, Any]]:
        return [
            row for row in store.rows(VEHICLE_MODULE)
            if str(row.get("车辆状态", "")).strip() not in BLOCKED_VEHICLE_STATUSES
        ]

    def _vehicle_conflicts(
        self,
        vehicle_no: str,
        start: datetime,
        end: datetime,
        candidate_map: dict[str, tuple[str, datetime, datetime]] | None = None,
    ) -> list[str]:
        """返回车辆在指定时段已被占用的任务编号；candidate_map 里是同批已占用车辆的候选任务。"""
        conflicts: list[str] = []
        for row in store.rows(MODULE):
            if str(row.get("车辆编号")) != vehicle_no or row.get("status") not in ACTIVE_STATUSES:
                continue
            row_start = parse_time(row.get("出发时刻"))
            row_end = parse_time(row.get("到达时刻"))
            if row_start and row_end and overlaps(start, end, row_start, row_end):
                conflicts.append(str(row.get("任务编号")))
        if candidate_map:
            for flight_no, (other_vehicle, other_start, other_end) in candidate_map.items():
                if other_vehicle == vehicle_no and overlaps(start, end, other_start, other_end):
                    conflicts.append(f"同批航班 {flight_no}")
        return conflicts

    @staticmethod
    def _pick_vehicle(
        passengers: int,
        start: datetime,
        end: datetime,
        vehicles: list[dict[str, Any]],
        candidate_map: dict[str, tuple[str, datetime, datetime]],
        existing_rows: list[dict[str, Any]],
    ) -> tuple[str | None, str]:
        """按"够用且最省"挑车：载客量够的车里选容量最小、编号最靠前、且时段空闲的。"""
        usable = [row for row in vehicles if int(row.get("载客量", 0)) >= passengers]
        usable.sort(key=lambda row: (int(row.get("载客量", 0)), str(row.get("车辆编号"))))
        if not usable:
            biggest = max((int(row.get("载客量", 0)) for row in vehicles), default=0)
            return None, f"车辆不够：单车最大载客 {biggest} 人，本航班 {passengers} 人"
        for vehicle in usable:
            vehicle_no = str(vehicle.get("车辆编号"))
            busy = False
            for row in existing_rows:
                if str(row.get("车辆编号")) != vehicle_no or row.get("status") not in ACTIVE_STATUSES:
                    continue
                row_start = parse_time(row.get("出发时刻"))
                row_end = parse_time(row.get("到达时刻"))
                if row_start and row_end and overlaps(start, end, row_start, row_end):
                    busy = True
                    break
            if busy:
                continue
            if any(
                other_vehicle == vehicle_no and overlaps(start, end, other_start, other_end)
                for other_vehicle, other_start, other_end in candidate_map.values()
            ):
                continue
            return vehicle_no, ""
        return None, "时段冲突：够用的车辆该时段均已被占用"

    def build_plan(self, wave_id: int) -> dict[str, Any] | None:
        """选定波次后一次性生成候选摆渡任务，并逐条给出能否派车的判断。"""
        wave = store.find(WAVE_MODULE, wave_id)
        if wave is None:
            return None
        flights = sorted(
            (row for row in store.rows(WAVE_FLIGHT_MODULE) if int(row.get("波次ID", 0)) == wave_id),
            key=lambda row: (str(row.get("出发时刻")), int(row.get("id", 0))),
        )
        vehicles = self._available_vehicles()
        existing_rows = store.rows(MODULE)
        # 已经分配过车辆的同批候选，参与后续车辆的时段占用判断。
        candidate_map: dict[str, tuple[str, datetime, datetime]] = {}
        candidates: list[dict[str, Any]] = []
        available_count = 0
        for index, flight in enumerate(flights, start=1):
            flight_no = str(flight.get("航班号"))
            passengers = parse_head_count(flight.get("乘客人数"))
            departure = str(flight.get("出发时刻") or "").strip()
            arrival = str(flight.get("到达时刻") or "").strip()
            item_key = f"W{wave_id}-{index}"
            base = {
                "item_key": item_key,
                "关联航班": flight_no,
                "乘客人数": passengers,
                "出发时刻": departure,
                "到达时刻": arrival,
            }
            start = parse_time(departure)
            end = parse_time(arrival)
            if start is None or end is None:
                reason = "时刻格式无法识别，请使用 YYYY-MM-DD HH:MM"
            elif start >= end:
                reason = f"时刻校验未通过：出发时刻 {departure} 不早于到达时刻 {arrival}"
            else:
                vehicle_no, reason = self._pick_vehicle(
                    passengers, start, end, vehicles, candidate_map, existing_rows
                )
                if vehicle_no:
                    candidate_map[flight_no] = (vehicle_no, start, end)
                    candidates.append({**base, "车辆编号": vehicle_no, "valid": True, "reason": ""})
                    available_count += 1
                    continue
            candidates.append({**base, "车辆编号": None, "valid": False, "reason": reason})
        head_count = sum(int(item["乘客人数"]) for item in candidates)
        return {
            "wave": {
                "id": wave_id,
                "波次编号": wave.get("波次编号"),
                "波次名称": wave.get("波次名称"),
                "波次日期": wave.get("波次日期"),
                "航班数量": len(flights),
                "乘客合计": head_count,
            },
            "candidates": candidates,
            "available_count": available_count,
            "rejected_count": len(candidates) - available_count,
            "head_count": head_count,
        }

    def submit_plan(
        self, wave_id: int | None, items: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        """提交勾选的派车任务；车辆不够/时段冲突/同批重复占用的任务挡下并说明冲突编号。"""
        wave = store.find(WAVE_MODULE, wave_id) if wave_id is not None else None
        parsed: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []

        for seq, item in enumerate(items, start=1):
            label = str(item.get("item_key") or f"第{seq}条")
            flight_no = str(item.get("关联航班") or "").strip()
            vehicle_no = str(item.get("车辆编号") or "").strip()
            departure = str(item.get("出发时刻") or "").strip()
            arrival = str(item.get("到达时刻") or "").strip()
            passengers = parse_head_count(item.get("乘客人数"))
            base = {
                "item_key": label,
                "关联航班": flight_no,
                "车辆编号": vehicle_no,
                "乘客人数": passengers,
                "出发时刻": departure,
                "到达时刻": arrival,
            }
            if not flight_no or not vehicle_no:
                blocked.append({**base, "reason": "缺少关联航班或车辆编号"})
                continue
            start, end = parse_time(departure), parse_time(arrival)
            if start is None or end is None:
                blocked.append({**base, "reason": "时刻格式无法识别，请使用 YYYY-MM-DD HH:MM"})
                continue
            if start >= end:
                blocked.append({
                    **base,
                    "reason": f"时刻校验未通过：出发时刻 {departure} 不早于到达时刻 {arrival}",
                })
                continue
            vehicle = next(
                (row for row in store.rows(VEHICLE_MODULE) if str(row.get("车辆编号")) == vehicle_no),
                None,
            )
            if vehicle is None:
                blocked.append({**base, "reason": f"车辆 {vehicle_no} 不在车辆台账中"})
                continue
            if str(vehicle.get("车辆状态", "")).strip() in BLOCKED_VEHICLE_STATUSES:
                blocked.append({**base, "reason": f"车辆 {vehicle_no} 当前状态为{vehicle.get('车辆状态')}，不可派车"})
                continue
            if int(vehicle.get("载客量", 0)) < passengers:
                blocked.append({
                    **base,
                    "reason": f"车辆不够：{vehicle_no} 载客 {vehicle.get('载客量')} 人，本航班 {passengers} 人",
                })
                continue
            parsed.append({**base, "start": start, "end": end})

        # 同一车辆同一时段在本批内重复派车：整组挡住，互相点名冲突的是哪条任务。
        accepted: list[dict[str, Any]] = []
        rejected_keys: set[str] = set()
        for index, current in enumerate(parsed):
            mates = []
            for other_index, other in enumerate(parsed):
                if other_index == index or other["车辆编号"] != current["车辆编号"]:
                    continue
                if overlaps(current["start"], current["end"], other["start"], other["end"]):
                    mates.append(str(other["item_key"]))
            if mates:
                rejected_keys.add(str(current["item_key"]))
                blocked.append({
                    **{k: current[k] for k in ("item_key", "关联航班", "车辆编号", "乘客人数", "出发时刻", "到达时刻")},
                    "reason": f"同车同时段重复派车，与候选任务 {'、'.join(mates)} 冲突",
                })
        parsed = [item for item in parsed if str(item["item_key"]) not in rejected_keys]

        # 与库里已派车（待发车/行驶中）任务做占用校验，通过的立即入库并占用时段。
        rows = store.rows(MODULE)
        for item in parsed:
            conflicts = self._vehicle_conflicts(item["车辆编号"], item["start"], item["end"])
            if conflicts:
                blocked.append({
                    **{k: item[k] for k in ("item_key", "关联航班", "车辆编号", "乘客人数", "出发时刻", "到达时刻")},
                    "reason": f"时段冲突：车辆 {item['车辆编号']} 已被任务 {'、'.join(conflicts)} 占用",
                })
                continue

            entry = {
                "id": store.next_id(MODULE),
                "任务编号": self._next_task_no(),
                "关联航班": item["关联航班"],
                "车辆编号": item["车辆编号"],
                "乘客人数": item["乘客人数"],
                "出发时刻": item["出发时刻"],
                "到达时刻": item["到达时刻"],
                "驾驶人员": DRIVER_PLACEHOLDER,
                "波次ID": wave["id"] if wave else None,
                "status": STATUS_ORDER[0],
                "pending": True,
                "abnormal": False,
            }
            rows.append(entry)
            stored = decorate(entry)
            stored["item_key"] = item["item_key"]
            accepted.append(stored)

        if accepted and blocked:
            message = f"已生成 {len(accepted)} 条摆渡任务，{len(blocked)} 条因车辆不够或时段冲突未提交"
        elif accepted:
            message = f"已按波次生成 {len(accepted)} 条摆渡任务"
        else:
            message = "本次勾选的任务均未通过校验，没有生成摆渡任务"
        return accepted, blocked, message

    def _next_task_no(self) -> str:
        max_seq = 0
        for row in store.rows(MODULE):
            text = str(row.get("任务编号", ""))
            if text.startswith(TASK_PREFIX):
                digits = text[len(TASK_PREFIX):].lstrip("-")
                if digits.isdigit():
                    max_seq = max(max_seq, int(digits))
        return f"{TASK_PREFIX}-{max_seq + 1:04d}"
