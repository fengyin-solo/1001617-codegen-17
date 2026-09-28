"""摆渡接送接口：维护摆渡任务，覆盖波次派车、安排发车、确认送达、取消任务与文件导出。"""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.schemas import (
    ActionResult,
    EntryPayload,
    ShuttleDispatchCommitPayload,
    ShuttlePageResult,
)
from app.services.shuttle import ShuttleService

router = APIRouter(prefix="/api/shuttle", tags=["摆渡接送"])

service = ShuttleService()

LIST_FIELDS = ["任务编号", "关联航班", "车辆编号", "乘客人数", "出发时刻", "到达时刻", "驾驶人员", "摆渡状态"]
STATUSES = ["待发车", "行驶中", "已送达", "已取消"]


# ---------- 波次派车（具体路径放在 /{entry_id} 之前，避免被动态路径截获） ----------
@router.get("/dispatch/waves")
def list_dispatch_waves() -> dict[str, Any]:
    """列出可选航班波次与其包含的航班乘客信息。"""
    return {"items": service.list_waves()}


@router.get("/dispatch/vehicles")
def list_dispatch_vehicles() -> dict[str, Any]:
    """列出摆渡车辆档案，派车时只取「可用」车辆。"""
    return {"items": service.list_vehicles()}


@router.get("/dispatch/preview/{wave_id}")
def preview_dispatch(wave_id: int) -> dict[str, Any]:
    """选定波次后预览派车方案：不落库，只返回可派、车辆不足、时段冲突三类候选。"""
    result = service.preview_wave(wave_id)
    if result.get("wave") is None:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.post("/dispatch/commit")
def commit_dispatch(payload: ShuttleDispatchCommitPayload) -> dict[str, Any]:
    """提交勾选的派车候选；提交时重新校验，挡住车辆不足与时段冲突的部分。"""
    return service.commit_wave(payload.wave_id, payload.selected)


# ---------- 列表与导出 ----------
@router.get("", response_model=ShuttlePageResult)
def list_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="待发车、行驶中、已送达、已取消"),
    page: int = 1,
    size: int = 20,
) -> ShuttlePageResult:
    """按任务编号与状态过滤摆渡接送列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total, summary = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return ShuttlePageResult(items=items, total=total, page=page, size=size, summary=summary)


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="按任务编号检索"),
    status: str | None = Query(default=None, description="按摆渡状态过滤"),
    format: str = Query(default="csv", description="csv 下载文件，json 返回原始数据"),
) -> Any:
    """导出已派车的摆渡任务：CSV 与列表页、人数清点同口径；json 保留兼容。"""
    items, summary = service.export_rows(keyword=keyword, status=status)
    if format == "json":
        return {"module": "shuttle", "total": len(items), "summary": summary, "items": items}

    buffer = io.StringIO()
    # 加 BOM，避免 Excel 直接打开中文乱码。
    buffer.write("﻿")
    writer = csv.writer(buffer)
    writer.writerow(LIST_FIELDS)
    for row in items:
        writer.writerow([row.get(field, "") if row.get(field) is not None else "" for field in LIST_FIELDS])
    writer.writerow([])
    writer.writerow([
        f"合计：任务 {summary['任务总数']} 条",
        f"待发车 {summary['待发车任务']} 条",
        f"取消 {summary['取消任务数']} 条",
        f"摆渡总人次 {summary['摆渡总人次']} 人",
    ])
    data = buffer.getvalue().encode("utf-8")
    filename = f"shuttle-dispatch-{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
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
