import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.logger import logger
from ..core.paths import resolve_uploaded_file_path
from ..db.database import get_db
from ..db.models import Task
from ..services.document_parser import DocumentParser
from ._task_progress import set_task_progress


router = APIRouter()
parser = DocumentParser()


def run_parse(task_id: int, db: Session):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    try:
        file_path = resolve_uploaded_file_path(task.file_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {task.file_path}")
    if task.file_path != str(file_path):
        task.file_path = str(file_path)
        db.commit()
        db.refresh(task)

    try:
        task.status = "parsing"
        task.parse_status = "running"
        task.extract_status = "pending"
        task.match_status = "pending"
        task.error_message = None
        set_task_progress(
            task,
            stage="parse",
            current=0,
            total=1,
            percent=8,
            message="正在解析文件结构",
        )
        db.commit()
        db.refresh(task)

        ext = file_path.suffix.lower().lstrip(".")
        doc_id = f"{file_path.stem}_{ext}"
        parse_result = parser.parse(file_path, doc_id=doc_id)

        task.result = json.dumps(parse_result, ensure_ascii=False)
        task.extract_result = None
        task.match_result = None
        task.status = "parsed"
        task.parse_status = "success"
        task.extract_status = "pending"
        task.match_status = "pending"
        task.error_message = None
        set_task_progress(
            task,
            stage="parse",
            current=1,
            total=1,
            percent=25,
            message="文件结构解析完成",
        )
        db.commit()
        db.refresh(task)

        logger.info("任务解析成功: task_id=%s file=%s", task.id, task.file_name)
        return {
            "message": "解析成功",
            "task_id": task.id,
            "status": task.status,
            "result": parse_result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        task.status = "parse_failed"
        task.parse_status = "failed"
        task.error_message = str(exc)
        set_task_progress(
            task,
            stage="failed",
            current=1,
            total=1,
            percent=100,
            message=f"解析失败: {exc}",
        )
        db.commit()
        logger.exception("任务解析失败: task_id=%s", task_id)
        raise HTTPException(status_code=500, detail=f"解析失败: {exc}") from exc


@router.post("/parse/{task_id}")
def parse_task(task_id: int, db: Session = Depends(get_db)):
    return run_parse(task_id, db)
