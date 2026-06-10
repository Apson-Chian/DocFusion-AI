from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ._task_progress import serialize_task_progress
from .upload import recover_task_pipeline_if_needed
from ..db.database import get_db
from ..db.models import Task
from ._extract_config import normalize_extract_config
from ._result_utils import build_parse_result_summary, build_pipeline_used, safe_json_loads, unify_task_payload


router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db)):
    recover_task_pipeline_if_needed(task_id)
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    task_result, extract_result, match_result = unify_task_payload(task)

    return {
        "task_id": task.id,
        "file_name": task.file_name,
        "file_path": task.file_path,
        "file_type": task.file_type,
        "processor_version": task.processor_version,
        "extract_config": normalize_extract_config(safe_json_loads(task.extract_config)),
        "status": task.status,
        "parse_status": task.parse_status,
        "extract_status": task.extract_status,
        "match_status": task.match_status,
        "progress": serialize_task_progress(task),
        "error_message": task.error_message,
        "result": task.result,
        "parse_result_summary": build_parse_result_summary(task_result),
        "pipeline_used": build_pipeline_used(match_result, extract_result),
        "extract_result": extract_result,
        "match_result": match_result,
        "tables": task_result.get("tables") or [],
        "table_views": task_result.get("table_views") or [],
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }


@router.get("/{task_id}/progress")
def get_task_progress(task_id: int, response: Response, db: Session = Depends(get_db)):
    recover_task_pipeline_if_needed(task_id)
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"

    return {
        "task_id": task.id,
        "file_name": task.file_name,
        "status": task.status,
        "parse_status": task.parse_status,
        "extract_status": task.extract_status,
        "match_status": task.match_status,
        "progress": serialize_task_progress(task),
        "error_message": task.error_message,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
    }
