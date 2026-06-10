from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import Task
from ._result_utils import ensure_list, get_paragraph_text, unify_task_payload


router = APIRouter()


@router.get("/trace/{task_id}")
def trace_field(
    task_id: int,
    record_index: int | None = Query(None, description="抽取结果中的第几条，从0开始"),
    indicator: str | None = Query(None, description="抽取结果中的指标"),
    value: str | None = Query(None, description="抽取结果中的数值"),
    db: Session = Depends(get_db),
):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not task.result:
        raise HTTPException(status_code=400, detail="当前任务没有解析结果")

    task_result, extract_result, _ = unify_task_payload(task)
    results = ensure_list(extract_result.get("results")) if isinstance(extract_result, dict) else []
    if not results:
        raise HTTPException(status_code=404, detail="当前任务没有抽取结果")

    matched_record = None
    matched_index = None

    if record_index is not None:
        if record_index < 0 or record_index >= len(results):
            raise HTTPException(status_code=400, detail="record_index 超出范围")
        matched_record = results[record_index]
        matched_index = record_index
    else:
        if not indicator:
            raise HTTPException(status_code=400, detail="请提供 record_index，或至少提供 indicator")
        for idx, item in enumerate(results):
            if not isinstance(item, dict):
                continue
            if str(item.get("指标", "")).strip() != indicator.strip():
                continue
            if value is not None and str(item.get("数值", "")).strip() != value.strip():
                continue
            matched_record = item
            matched_index = idx
            break

    if not isinstance(matched_record, dict):
        raise HTTPException(status_code=404, detail="未找到匹配的抽取结果")

    source_paragraph = matched_record.get("来源段落")
    try:
        source_paragraph = int(source_paragraph) if source_paragraph is not None else None
    except Exception:
        source_paragraph = None

    return {
        "task_id": task.id,
        "file_name": task.file_name,
        "record_index": matched_index,
        "indicator": matched_record.get("指标"),
        "value": matched_record.get("数值"),
        "unit": matched_record.get("单位"),
        "time": matched_record.get("时间"),
        "yoy": matched_record.get("同比"),
        "source_kind": matched_record.get("source_kind"),
        "source_paragraph": source_paragraph,
        "source_text": matched_record.get("source_text") or get_paragraph_text(task_result.get("paragraphs"), source_paragraph),
        "source_table_id": matched_record.get("source_table_id"),
        "source_row": matched_record.get("source_row"),
        "source_col": matched_record.get("source_col"),
        "source_header": matched_record.get("source_header"),
        "source_locator": matched_record.get("source_locator"),
        "source_context": matched_record.get("source_context"),
        "record": matched_record,
    }
