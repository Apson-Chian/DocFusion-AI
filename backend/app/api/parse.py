from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Task
from app.core.logger import logger

from pathlib import Path
import json
import sys

router = APIRouter()

# 把项目根目录加入 Python 路径
ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from parser.doc_parser import DocumentParser

parser = DocumentParser()


@router.post("/parse/{task_id}")
def parse_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    try:
        file_path = Path(task.file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {task.file_path}")

        parse_result = parser.parse(file_path, doc_id=f"doc_{task.id}")

        task.status = "parsed"
        task.result = json.dumps(parse_result, ensure_ascii=False)
        task.error_message = None
        db.commit()
        db.refresh(task)

        logger.info(f"任务解析成功: task_id={task.id}")

        return {
            "message": "解析成功",
            "task_id": task.id,
            "status": task.status,
            "result": parse_result
        }

    except Exception as e:
        task.status = "failed"
        task.error_message = str(e)
        db.commit()

        logger.error(f"任务解析失败: task_id={task.id}, error={str(e)}")
        raise HTTPException(status_code=500, detail=f"解析失败: {str(e)}")