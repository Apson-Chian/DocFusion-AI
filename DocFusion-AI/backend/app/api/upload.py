import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..core.paths import UPLOAD_DIR
from ..core.version import PROCESSOR_VERSION
from ..db.database import SessionLocal
from ..db.models import Task
from ._extract_config import normalize_extract_config, parse_extract_config_text, serialize_extract_config
from ._task_progress import set_task_progress, serialize_task_progress
from .extract import run_extract
from .match import match_task
from .parse import run_parse


router = APIRouter()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
PIPELINE_WORKERS = int(os.getenv("UPLOAD_BATCH_WORKERS", "4"))
PIPELINE_EXECUTOR = ThreadPoolExecutor(max_workers=max(1, PIPELINE_WORKERS))
RECOVERY_STALE_SECONDS = int(os.getenv("TASK_RECOVERY_STALE_SECONDS", "5"))
RUNNING_STAGE_STALE_SECONDS = max(RECOVERY_STALE_SECONDS, int(os.getenv("TASK_RUNNING_STALE_SECONDS", "90")))
IN_FLIGHT_TASK_IDS: set[int] = set()
IN_FLIGHT_TASK_IDS_LOCK = Lock()


def get_file_type(file_name: str):
    return file_name.rsplit(".", 1)[-1] if "." in file_name else ""


def build_storage_path(file_name: str, file_hash: str) -> Path:
    safe_name = os.path.basename(file_name or "upload.bin")
    base = UPLOAD_DIR / safe_name
    if not base.exists():
        return base
    stem = base.stem
    suffix = base.suffix
    return UPLOAD_DIR / f"{stem}_{file_hash[:8]}{suffix}"


def save_upload_content(file_name: str, content: bytes):
    file_hash = hashlib.sha256(content).hexdigest()
    target = build_storage_path(file_name, file_hash)
    target.write_bytes(content)
    return os.path.basename(file_name or target.name), str(target.resolve()), file_hash


def find_cached_task(db, file_hash: str, extract_config_text: str):
    return (
        db.query(Task)
        .filter(
            Task.file_hash == file_hash,
            Task.processor_version == PROCESSOR_VERSION,
            Task.extract_config == extract_config_text,
            Task.result.isnot(None),
            Task.extract_result.isnot(None),
            Task.status.in_(["extracted", "matched"]),
        )
        .order_by(Task.updated_at.desc())
        .first()
    )


def build_task_response(task: Task, message: str, cached: bool):
    return {
        "message": message,
        "task_id": task.id,
        "status": task.status,
        "cached": cached,
        "progress": serialize_task_progress(task),
    }


def validate_extract_config_payload(config: dict):
    fields = config.get("fields") if isinstance(config, dict) else []
    if not isinstance(fields, list) or not fields:
        raise HTTPException(status_code=400, detail="请先在前端填写表头，再上传文件")

    names = [str(item.get("field_name") or "").strip() for item in fields if isinstance(item, dict)]
    names = [item for item in names if item]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=400, detail="表头名称不能重复，请调整前端列名后重试")


def mark_task_failed(task: Task, db, message: str):
    if task.parse_status == "running":
        task.parse_status = "failed"
    if task.extract_status == "running":
        task.extract_status = "failed"
    if task.match_status == "running":
        task.match_status = "failed"
    task.status = "failed"
    task.error_message = message
    set_task_progress(task, stage="failed", current=1, total=1, percent=100, message=message)
    db.commit()


def get_resume_stage(task: Task) -> str | None:
    if not task.result or task.parse_status != "success":
        return "parse"
    if not task.extract_result or task.extract_status != "success":
        return "extract"
    if task.match_status not in {"success", "skipped", "failed"}:
        return "match"
    return None


def task_is_recently_active(task: Task) -> bool:
    timestamp = task.updated_at or task.created_at
    if timestamp is None:
        return False
    threshold = RUNNING_STAGE_STALE_SECONDS if any(
        status == "running" for status in [task.parse_status, task.extract_status, task.match_status]
    ) else RECOVERY_STALE_SECONDS
    return (datetime.utcnow() - timestamp).total_seconds() < threshold


def try_mark_task_in_flight(task_id: int) -> bool:
    with IN_FLIGHT_TASK_IDS_LOCK:
        if task_id in IN_FLIGHT_TASK_IDS:
            return False
        IN_FLIGHT_TASK_IDS.add(task_id)
        return True


def clear_task_in_flight(task_id: int) -> None:
    with IN_FLIGHT_TASK_IDS_LOCK:
        IN_FLIGHT_TASK_IDS.discard(task_id)


def task_is_in_flight(task_id: int) -> bool:
    with IN_FLIGHT_TASK_IDS_LOCK:
        return task_id in IN_FLIGHT_TASK_IDS


def run_task_pipeline(task_id: int):
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task is None:
            return
        resume_stage = get_resume_stage(task)
        if resume_stage is None:
            return

        set_task_progress(task, stage="queued", current=0, total=1, percent=5, message="任务已入队，等待执行")
        db.commit()

        if resume_stage == "parse":
            run_parse(task.id, db)
            task = db.query(Task).filter(Task.id == task_id).first()
            resume_stage = get_resume_stage(task) if task is not None else None
        if task is not None and resume_stage == "extract":
            run_extract(task.id, db)
            task = db.query(Task).filter(Task.id == task_id).first()
            resume_stage = get_resume_stage(task) if task is not None else None
        if task is not None and resume_stage == "match":
            match_task(task.id, db)
    except HTTPException as exc:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task is not None:
            mark_task_failed(task, db, str(exc.detail))
    except Exception as exc:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task is not None:
            mark_task_failed(task, db, str(exc))
    finally:
        db.close()
        clear_task_in_flight(task_id)


def enqueue_task_pipeline(task_id: int, *, force: bool = False):
    if force:
        clear_task_in_flight(task_id)
    if not try_mark_task_in_flight(task_id):
        return False
    PIPELINE_EXECUTOR.submit(run_task_pipeline, task_id)
    return True


def recover_task_pipeline_if_needed(task_id: int):
    db = SessionLocal()
    try:
        if task_is_in_flight(task_id):
            return False
        task = db.query(Task).filter(Task.id == task_id).first()
        if task is None:
            return False

        resume_stage = get_resume_stage(task)
        if resume_stage is None:
            return False
        if task_is_recently_active(task):
            return False

        stage_label = {
            "parse": "解析",
            "extract": "抽取",
            "match": "匹配",
        }.get(resume_stage, "处理")
        queued = enqueue_task_pipeline(task_id)
        if not queued:
            return False

        set_task_progress(
            task,
            stage="queued",
            current=0,
            total=1,
            percent=5,
            message=f"检测到未完成任务，已从{stage_label}阶段重新入队",
        )
        db.commit()
        return True
    finally:
        db.close()


def create_upload_task(file_name: str, content: bytes, extract_config=None):
    db = SessionLocal()
    try:
        normalized_config = normalize_extract_config(extract_config)
        validate_extract_config_payload(normalized_config)
        extract_config_text = serialize_extract_config(normalized_config)
        display_name, file_path, file_hash = save_upload_content(file_name, content)
        cached_task = find_cached_task(db, file_hash, extract_config_text)
        if cached_task:
            return build_task_response(cached_task, "命中文档缓存，复用已处理结果", True)

        task = Task(
            file_name=display_name,
            file_path=file_path,
            file_type=get_file_type(display_name),
            file_hash=file_hash,
            processor_version=PROCESSOR_VERSION,
            extract_config=extract_config_text,
            status="uploaded",
            parse_status="pending",
            extract_status="pending",
            match_status="pending",
        )
        set_task_progress(
            task,
            stage="queued",
            current=0,
            total=1,
            percent=2,
            message="文件已上传，等待后端处理",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        enqueue_task_pipeline(task.id, force=True)
        return build_task_response(task, "文件上传成功，后台处理中", False)
    finally:
        db.close()


def read_extract_config(raw_text: str | None):
    try:
        return parse_extract_config_text(raw_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/upload")
async def upload_file(file: UploadFile = File(...), extract_config: str | None = Form(None)):
    return create_upload_task(file.filename, await file.read(), read_extract_config(extract_config))


@router.post("/upload/batch")
async def upload_files(files: list[UploadFile] = File(...), extract_config: str | None = Form(None)):
    payloads = [(file.filename, await file.read()) for file in files]
    if not payloads:
        raise HTTPException(status_code=400, detail="没有可上传的文件")

    config = read_extract_config(extract_config)
    results = []
    for file_name, content in payloads:
        try:
            payload = create_upload_task(file_name, content, config)
            payload["fileName"] = file_name
            payload["success"] = True
            results.append(payload)
        except HTTPException as exc:
            results.append(
                {
                    "fileName": file_name,
                    "success": False,
                    "status": "failed",
                    "message": str(exc.detail),
                }
            )

    return {"message": "批量上传已受理", "count": len(results), "results": results}
