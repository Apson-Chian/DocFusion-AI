import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import DocumentField, ExtractedEntity, Task
from . import _extract_engine as extract_engine
from ._extract_config import get_field_value_by_slot, normalize_extract_config, serialize_extract_config
from ._result_utils import ensure_list, get_paragraph_text, safe_json_loads
from ._task_progress import set_task_progress


router = APIRouter()


def normalize_paragraph_id(value):
    try:
        return int(value) if value not in [None, ""] else None
    except Exception:
        return None


def get_extract_field_defs(task: Task, extract_result: dict):
    result_fields = []
    for item in ensure_list(extract_result.get("fields")):
        if not isinstance(item, dict):
            continue
        field_name = str(item.get("field_name") or "").strip()
        slot = str(item.get("slot") or "").strip()
        if not field_name:
            continue
        result_fields.append(
            {
                "field_name": field_name,
                "slot": slot,
                "visible": bool(item.get("visible", True)),
            }
        )
    if result_fields:
        return result_fields
    return normalize_extract_config(safe_json_loads(task.extract_config)).get("fields", [])


def get_row_source_map(record: dict):
    source_map = record.get("__sources__")
    return source_map if isinstance(source_map, dict) else {}


def get_primary_source(record: dict, field_defs: list[dict]):
    source_map = get_row_source_map(record)
    preferred_names = []

    for field_name in ensure_list(record.get("__key_fields__")):
        if isinstance(field_name, str):
            preferred_names.append(field_name)
    preferred_names.extend(field.get("field_name") for field in field_defs if field.get("field_name"))

    seen = set()
    for field_name in preferred_names:
        if not field_name or field_name in seen:
            continue
        seen.add(field_name)
        source = source_map.get(field_name)
        if isinstance(source, dict) and (
            source.get("paragraph_id") not in [None, ""]
            or source.get("paragraph_text")
            or source.get("evidence")
        ):
            return source
    return None


def save_document_field(task: Task, parse_data: dict, extract_result: dict, db: Session):
    paragraphs = ensure_list(parse_data.get("paragraphs"))
    tables = ensure_list(parse_data.get("tables"))
    results = [item for item in ensure_list(extract_result.get("results")) if isinstance(item, dict)]
    field_defs = get_extract_field_defs(task, extract_result)
    main_record = results[0] if results else {}
    primary_source = get_primary_source(main_record, field_defs)
    source_paragraph = normalize_paragraph_id(primary_source.get("paragraph_id") if primary_source else None)
    source_text = (
        primary_source.get("paragraph_text")
        if isinstance(primary_source, dict) and primary_source.get("paragraph_text")
        else get_paragraph_text(paragraphs, source_paragraph)
    )

    entity = db.query(DocumentField).filter(DocumentField.task_id == task.id).first()
    if entity is None:
        entity = DocumentField(task_id=task.id)
        db.add(entity)

    entity.doc_id = str(parse_data.get("doc_id")) if parse_data.get("doc_id") is not None else None
    entity.doc_type = str(parse_data.get("doc_type") or task.file_type) if (parse_data.get("doc_type") or task.file_type) else None
    entity.raw_text = str(parse_data.get("raw_text")) if parse_data.get("raw_text") is not None else None
    entity.paragraphs = json.dumps(paragraphs, ensure_ascii=False)
    entity.tables = json.dumps(tables, ensure_ascii=False)
    entity.category = get_field_value_by_slot(main_record, field_defs, "category")
    entity.indicator = get_field_value_by_slot(main_record, field_defs, "indicator")
    entity.value = get_field_value_by_slot(main_record, field_defs, "value")
    entity.unit = get_field_value_by_slot(main_record, field_defs, "unit")
    entity.time = get_field_value_by_slot(main_record, field_defs, "time")
    entity.yoy = get_field_value_by_slot(main_record, field_defs, "yoy")
    entity.source_document = task.file_name
    entity.source_paragraph = source_paragraph
    entity.source_text = source_text
    entity.source_span = primary_source.get("evidence") if isinstance(primary_source, dict) else None


def save_extracted_entities(task: Task, parse_data: dict, extract_result: dict, db: Session):
    paragraphs = ensure_list(parse_data.get("paragraphs"))
    doc_id = parse_data.get("doc_id")
    results = [item for item in ensure_list(extract_result.get("results")) if isinstance(item, dict)]
    field_defs = get_extract_field_defs(task, extract_result)

    db.query(ExtractedEntity).filter(ExtractedEntity.task_id == task.id).delete()

    if field_defs and results:
        for row_index, record in enumerate(results, start=1):
            record_id = str(record.get("record_id") or f"row_{row_index}")
            source_map = get_row_source_map(record)
            for field in field_defs:
                field_name = field.get("field_name")
                if not field_name:
                    continue
                field_value = record.get(field_name)
                if field_value in [None, ""]:
                    continue
                source = source_map.get(field_name) if isinstance(source_map, dict) else None
                source_paragraph = normalize_paragraph_id(source.get("paragraph_id") if isinstance(source, dict) else None)
                source_text = (
                    source.get("paragraph_text")
                    if isinstance(source, dict) and source.get("paragraph_text")
                    else get_paragraph_text(paragraphs, source_paragraph)
                )
                confidence = source.get("confidence") if isinstance(source, dict) else None
                db.add(
                    ExtractedEntity(
                        task_id=task.id,
                        doc_id=str(doc_id) if doc_id is not None else None,
                        source_document=task.file_name,
                        record_id=record_id,
                        field_name=str(field_name),
                        field_value=str(field_value) if field_value is not None else None,
                        normalized_value=str(field_value) if field_value is not None else None,
                        source_paragraph=source_paragraph,
                        source_text=str(source_text) if source_text is not None else None,
                        source_span=source.get("evidence") if isinstance(source, dict) else None,
                        field_embedding=None,
                        confidence=float(confidence) if confidence not in [None, ""] else None,
                        extractor_type="llm_row_keyed",
                        source_kind=(source.get("source_kind") if isinstance(source, dict) else None) or ("paragraph" if source_paragraph is not None else None),
                        source_table_id=source.get("source_table_id") if isinstance(source, dict) else None,
                        source_row=source.get("source_row") if isinstance(source, dict) else None,
                        source_col=source.get("source_col") if isinstance(source, dict) else None,
                        source_header=source.get("source_header") if isinstance(source, dict) else None,
                        source_locator=(
                            source.get("source_locator")
                            if isinstance(source, dict) and source.get("source_locator")
                            else (f"paragraph:{source_paragraph}" if source_paragraph is not None else None)
                        ),
                        source_context=source.get("evidence") if isinstance(source, dict) else None,
                    )
                )
        return

    for index, record in enumerate(results, start=1):
        record_id = str(record.get("record_id") or f"record_{index}")
        for field_name, field_value in record.items():
            if field_name.startswith("__") or field_name in {"record_id"}:
                continue
            db.add(
                ExtractedEntity(
                    task_id=task.id,
                    doc_id=str(doc_id) if doc_id is not None else None,
                    source_document=task.file_name,
                    record_id=record_id,
                    field_name=str(field_name),
                    field_value=str(field_value) if field_value is not None else None,
                    normalized_value=str(field_value) if field_value is not None else None,
                    source_paragraph=None,
                    source_text=None,
                    source_span=None,
                    field_embedding=None,
                    confidence=None,
                    extractor_type="llm_row_keyed",
                    source_kind=None,
                    source_table_id=None,
                    source_row=None,
                    source_col=None,
                    source_header=None,
                    source_locator=None,
                    source_context=None,
                )
            )


def run_extract(task_id: int, db: Session):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not task.result:
        raise HTTPException(status_code=400, detail="请先完成解析，再进行字段抽取")

    parse_data = safe_json_loads(task.result)
    if not isinstance(parse_data, dict) or not parse_data:
        raise HTTPException(status_code=500, detail="解析结果读取失败")

    try:
        extract_config = normalize_extract_config(safe_json_loads(task.extract_config))
        if not extract_config.get("fields"):
            raise HTTPException(status_code=400, detail="请先在前端填写表头后再启动解析流水线")

        task.status = "extracting"
        task.extract_status = "running"
        task.error_message = None
        set_task_progress(
            task,
            stage="extract",
            current=0,
            total=1,
            percent=28,
            message="正在按行识别并逐列归并字段",
        )
        db.commit()
        db.refresh(task)

        def progress_callback(*, percent=None, message=None, current=None, total=None):
            set_task_progress(
                task,
                stage="extract",
                current=current,
                total=total,
                percent=percent,
                message=message,
            )
            db.commit()

        task.extract_config = serialize_extract_config(extract_config)
        extract_result = extract_engine.extract(parse_data, frontend_form=extract_config, progress_callback=progress_callback)
        task.extract_result = json.dumps(extract_result, ensure_ascii=False)

        merged_result = dict(parse_data)
        merged_result["extract_result"] = extract_result
        existing_match = safe_json_loads(task.match_result)
        if isinstance(existing_match, dict) and existing_match:
            merged_result["match_result"] = existing_match
        task.result = json.dumps(merged_result, ensure_ascii=False)

        task.status = "extracted"
        task.parse_status = task.parse_status or "success"
        task.extract_status = "success"
        task.match_status = "pending" if not task.match_result else task.match_status
        task.error_message = None
        set_task_progress(
            task,
            stage="extract",
            current=1,
            total=1,
            percent=92,
            message="字段抽取完成，准备执行标准字段匹配",
        )

        save_document_field(task, parse_data, extract_result, db)
        save_extracted_entities(task, parse_data, extract_result, db)

        db.commit()
        db.refresh(task)
        return {
            "message": "字段抽取完成",
            "task_id": task.id,
            "status": task.status,
            "extract_result": extract_result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        task.status = "extract_failed"
        task.extract_status = "failed"
        task.error_message = str(exc)
        set_task_progress(
            task,
            stage="failed",
            current=1,
            total=1,
            percent=100,
            message=f"字段抽取失败: {exc}",
        )
        db.commit()
        raise HTTPException(status_code=500, detail=f"字段抽取失败: {exc}") from exc


@router.post("/extract/{task_id}")
def extract_task(task_id: int, db: Session = Depends(get_db)):
    return run_extract(task_id, db)
