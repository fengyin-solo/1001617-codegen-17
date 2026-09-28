"""摆渡接送接口：维护摆渡任务，覆盖按航班波次派车、安排发车、确认送达、取消任务与清单导出。"""
from __future__ import annotations

import csv
import io
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.schemas import (
    ActionResult,
    EntryPayload,
    PageResult,
    ShuttleDispatchPlan,
    ShuttleDispatchResult,
    ShuttleDispatchSubmit,
    ShuttleWaveOption,
)
from app.services.shuttle import MODULE, ShuttleService, parse_head_count

router = APIRouter(prefix="/api/shuttle", tags=["摆渡接送"])

service = ShuttleService()

LIST_FIELDS = ["任务编号", "关联航班", "车辆编号", "乘客人数", "出发时刻", "到达时刻", "驾驶人员", "摆渡状态"]
STATUSES = ["待发车", "行驶中", "已送达", "已取消"]


def _filters(
    keyword: str | None,
    status: str | None,
    wave: str | None,
) -> dict[str, str | None]:
    return {"keyword": keyword, "status": status, "wave": wave}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="待发车、行驶中、已送达、已取消"),
    wave: str | None = Query(default=None, description="按航班波次编号检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按任务编号、状态与波次过滤摆渡接送列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"状态需为：{'、'.join(STATUSES)}")
    items, total, head_count, status_counts = service.list_entries(
        **_filters(keyword, status, wave), page=page, size=size
    )
    return PageResult(
        items=items,
        total=total,
        page=page,
        size=size,
        head_count=head_count,
        status_counts=status_counts,
    )


@router.get("/waves", response_model=list[ShuttleWaveOption])
def list_waves() -> list[dict]:
    """列出可选航班波次，附带每个波次的航班数量与乘客合计，供派车前清点。"""
    return service.list_waves()


@router.post("/dispatch/plan", response_model=ShuttleDispatchPlan)
def build_dispatch_plan(payload: ShuttleDispatchSubmit) -> ShuttleDispatchPlan:
    """选定航班波次后一次生成多条候选摆渡任务：按乘客人数分配车辆，并校验时刻与占用。"""
    if payload.wave_id is None:
        raise HTTPException(status_code=400, detail="请先选择航班波次")
    plan = service.build_plan(payload.wave_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"航班波次 {payload.wave_id} 不存在")
    return plan


@router.post("/dispatch/submit", response_model=ShuttleDispatchResult)
def submit_dispatch(payload: ShuttleDispatchSubmit) -> ShuttleDispatchResult:
    """只提交勾选的那部分任务；同车同时段重复派车整组挡住并点名冲突的候选任务编号。"""
    if not payload.items:
        return ShuttleDispatchResult(
            ok=False, message="没有勾选任何可派任务", created=[], blocked=[]
        )
    created, blocked, message = service.submit_plan(
        payload.wave_id, [item.model_dump() for item in payload.items]
    )
    return ShuttleDispatchResult(
        ok=bool(created),
        message=message,
        created=created,
        blocked=blocked,
        created_count=len(created),
        blocked_count=len(blocked),
    )


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条摆渡任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"摆渡任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条摆渡任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="摆渡任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条摆渡任务执行安排发车、确认送达、取消任务；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export/data")
def export_data(
    keyword: str | None = None,
    status: str | None = None,
    wave: str | None = None,
) -> dict:
    """导出摆渡接送清单（JSON）：返回当前过滤条件下的全量数据，与列表页同一口径。"""
    items = service.all_entries(**_filters(keyword, status, wave))
    return {"module": MODULE, "total": len(items), "items": items}


@router.get("/export/file")
def export_file(
    keyword: str | None = None,
    status: str | None = None,
    wave: str | None = None,
) -> StreamingResponse:
    """导出摆渡接送清单文件（CSV，含 BOM，Excel 可直接打开），并随响应触发下载。

    导出口径与列表页完全一致：同样的过滤条件、同样的列；末尾附当前清单的
    乘客人数合计，和页面上的清点人数对得上。
    """
    items = service.all_entries(**_filters(keyword, status, wave))
    columns = ["任务编号", "航班波次", *LIST_FIELDS[1:-1], "摆渡状态"]

    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for item in items:
        writer.writerow([item.get(column, "") if item.get(column) is not None else "" for column in columns])
    head_count = sum(parse_head_count(item.get("乘客人数")) for item in items)
    writer.writerow(["合计", "", "", f"{len(items)} 条任务", head_count, "", "", "", "", ""])

    filename = quote("摆渡接送清单.csv")
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
