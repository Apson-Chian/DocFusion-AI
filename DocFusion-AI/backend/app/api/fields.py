from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import ExtractedEntity, Task
from ._extract_config import get_field_value_by_slot, normalize_extract_config
from ._result_utils import (
    build_parse_result_summary,
    build_pipeline_used,
    build_table_views_with_traces,
    ensure_list,
    get_paragraph_text,
    safe_json_loads,
    unify_task_payload,
)


router = APIRouter()
MAX_RESULT_RECORDS = 120
MAX_EXTRACT_RESULT_PREVIEW = 20
MAX_TABLE_ROWS = 40
MAX_TABLE_COLS = 18
MAX_PARAGRAPHS = 60
MAX_RAW_TEXT = 4000


def get_task_field_defs(task: Task, extract_result: dict):
    result_fields = []
    for item in ensure_list(extract_result.get("fields")):
        if not isinstance(item, dict):
            continue
        field_name = str(item.get("field_name") or "").strip()
        if not field_name:
            continue
        result_fields.append(
            {
                "field_name": field_name,
                "slot": item.get("slot"),
                "visible": bool(item.get("visible", True)),
                "key": item.get("key"),
            }
        )
    if result_fields:
        return result_fields
    return normalize_extract_config(safe_json_loads(task.extract_config)).get("fields", [])


def get_visible_task_fields(field_defs: list[dict]):
    return [item for item in field_defs if item.get("visible", True)]


def get_row_source_map(item: dict):
    source_map = item.get("__sources__")
    return source_map if isinstance(source_map, dict) else {}


def get_primary_source(item: dict, field_defs: list[dict]):
    source_map = get_row_source_map(item)
    preferred = []
    for field_name in ensure_list(item.get("__key_fields__")):
        if isinstance(field_name, str):
            preferred.append(field_name)
    preferred.extend(field.get("field_name") for field in field_defs if field.get("field_name"))

    seen = set()
    for field_name in preferred:
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


def get_source_paragraph(item: dict, field_defs: list[dict]):
    primary_source = get_primary_source(item, field_defs)
    if isinstance(primary_source, dict):
        try:
            return int(primary_source.get("paragraph_id")) if primary_source.get("paragraph_id") is not None else None
        except Exception:
            return None

    return get_field_value_by_slot(
        item,
        field_defs,
        "source_paragraph",
        default=item.get("source_paragraph") or item.get("paragraph_index") or item.get("paragraph"),
    )


def format_result_record(task: Task, task_result: dict, item: dict, index: int, field_defs: list[dict]):
    source_map = get_row_source_map(item)
    source_paragraph = get_source_paragraph(item, field_defs)
    primary_source = get_primary_source(item, field_defs)
    visible_fields = get_visible_task_fields(field_defs)
    field_options = item.get("__field_options__") if isinstance(item.get("__field_options__"), dict) else {}
    decision_trace = item.get("__decision_trace__") if isinstance(item.get("__decision_trace__"), dict) else {}

    return {
        "record_index": index,
        "record_id": item.get("record_id"),
        "fields": {
            field["field_name"]: (
                item.get(
                    field["field_name"],
                    get_field_value_by_slot(item, field_defs, field.get("slot"), default=None),
                )
                or "略"
            )
            for field in visible_fields
        },
        "field_sources": {
            field["field_name"]: source_map.get(field["field_name"])
            for field in visible_fields
            if source_map.get(field["field_name"]) is not None
        },
        "field_options": {
            field["field_name"]: field_options.get(field["field_name"])
            for field in visible_fields
            if field_options.get(field["field_name"])
        },
        "decision_trace": {
            field["field_name"]: decision_trace.get(field["field_name"])
            for field in visible_fields
            if decision_trace.get(field["field_name"])
        },
        "key_fields": ensure_list(item.get("__key_fields__")),
        "source_kind": item.get("source_kind") or ("paragraph" if primary_source else None),
        "source_file": task.file_name,
        "source_paragraph": source_paragraph,
        "source_text": (
            primary_source.get("paragraph_text")
            if isinstance(primary_source, dict) and primary_source.get("paragraph_text")
            else item.get("source_text") or get_paragraph_text(ensure_list(task_result.get("paragraphs")), source_paragraph)
        ),
        "source_table_id": item.get("source_table_id"),
        "source_row": item.get("source_row"),
        "source_col": item.get("source_col"),
        "source_header": item.get("source_header"),
        "source_locator": item.get("source_locator"),
        "source_context": (
            primary_source.get("evidence")
            if isinstance(primary_source, dict) and primary_source.get("evidence")
            else item.get("source_context")
        ),
    }


def find_source_record(task_result: dict, field_name: str, field_defs: list[dict]):
    extract_result = task_result.get("extract_result", {})
    results = ensure_list(extract_result.get("results")) if isinstance(extract_result, dict) else []
    match_result = task_result.get("match_result", {})
    matched_result = match_result.get("matched_result", {}) if isinstance(match_result, dict) else {}
    matched_trace_map = match_result.get("matched_trace_map", {}) if isinstance(match_result, dict) else {}
    paragraphs = ensure_list(task_result.get("paragraphs"))

    if isinstance(matched_trace_map, dict) and field_name in matched_trace_map:
        trace = matched_trace_map.get(field_name) or {}
        return {
            "source_file": trace.get("source_file"),
            "source_key": trace.get("source_key") or field_name,
            "value": trace.get("value"),
            "source_kind": trace.get("source_kind"),
            "source_paragraph": trace.get("source_paragraph"),
            "source_text": trace.get("source_text"),
            "source_table_id": trace.get("source_table_id"),
            "source_row": trace.get("source_row"),
            "source_col": trace.get("source_col"),
            "source_header": trace.get("source_header"),
            "source_locator": trace.get("source_locator"),
            "source_context": trace.get("source_context"),
            "record_index": trace.get("record_index"),
            "raw_record": trace.get("raw_record"),
        }

    for index, item in enumerate(results):
        if not isinstance(item, dict):
            continue
        if field_name not in item and field_name not in get_row_source_map(item):
            continue
        source = get_row_source_map(item).get(field_name) or {}
        source_paragraph = source.get("paragraph_id")
        return {
            "source_file": None,
            "source_key": field_name,
            "value": item.get(field_name),
            "source_kind": "paragraph" if source else item.get("source_kind"),
            "source_paragraph": source_paragraph,
            "source_text": source.get("paragraph_text") or get_paragraph_text(paragraphs, source_paragraph),
            "source_table_id": item.get("source_table_id"),
            "source_row": item.get("source_row"),
            "source_col": item.get("source_col"),
            "source_header": item.get("source_header"),
            "source_locator": item.get("source_locator"),
            "source_context": source.get("evidence") or item.get("source_context"),
            "record_index": index,
            "raw_record": item,
        }

    target_value = matched_result.get(field_name) if isinstance(matched_result, dict) else None
    if target_value is not None:
        for index, item in enumerate(results):
            if not isinstance(item, dict):
                continue
            if str(item.get(field_name, "")).strip() != str(target_value).strip():
                continue
            source = get_primary_source(item, field_defs)
            source_paragraph = source.get("paragraph_id") if isinstance(source, dict) else None
            return {
                "source_file": None,
                "source_key": field_name,
                "value": item.get(field_name),
                "source_kind": "paragraph" if source else item.get("source_kind"),
                "source_paragraph": source_paragraph,
                "source_text": source.get("paragraph_text") if isinstance(source, dict) else get_paragraph_text(paragraphs, source_paragraph),
                "source_table_id": item.get("source_table_id"),
                "source_row": item.get("source_row"),
                "source_col": item.get("source_col"),
                "source_header": item.get("source_header"),
                "source_locator": item.get("source_locator"),
                "source_context": source.get("evidence") if isinstance(source, dict) else item.get("source_context"),
                "record_index": index,
                "raw_record": item,
            }

    return None


def get_record_source(task: Task, task_result: dict, record_index: int, field_defs: list[dict]):
    extract_result = task_result.get("extract_result", {})
    results = ensure_list(extract_result.get("results")) if isinstance(extract_result, dict) else []
    paragraphs = ensure_list(task_result.get("paragraphs"))
    if record_index < 0 or record_index >= len(results):
        return None

    raw_record = results[record_index]
    if not isinstance(raw_record, dict):
        return None

    source = get_primary_source(raw_record, field_defs)
    source_paragraph = source.get("paragraph_id") if isinstance(source, dict) else get_source_paragraph(raw_record, field_defs)
    key_fields = [item for item in ensure_list(raw_record.get("__key_fields__")) if isinstance(item, str)]
    source_key = key_fields[0] if key_fields else None
    if source_key is None and field_defs:
        source_key = field_defs[0].get("field_name")

    return {
        "source_file": task.file_name,
        "source_key": source_key,
        "value": raw_record.get(source_key) if source_key else None,
        "source_kind": "paragraph" if source else raw_record.get("source_kind"),
        "source_paragraph": source_paragraph,
        "source_text": source.get("paragraph_text") if isinstance(source, dict) else get_paragraph_text(paragraphs, source_paragraph),
        "source_table_id": raw_record.get("source_table_id"),
        "source_row": raw_record.get("source_row"),
        "source_col": raw_record.get("source_col"),
        "source_header": raw_record.get("source_header"),
        "source_locator": raw_record.get("source_locator"),
        "source_context": source.get("evidence") if isinstance(source, dict) else raw_record.get("source_context"),
        "record_index": record_index,
        "raw_record": raw_record,
    }


def clamp_table_views(table_views):
    preview_tables = []
    was_limited = False
    for table in table_views:
        if not isinstance(table, dict):
            continue
        if len(ensure_list(table.get("rows"))) > MAX_TABLE_ROWS:
            was_limited = True
        rows = []
        for row in ensure_list(table.get("rows"))[:MAX_TABLE_ROWS]:
            if not isinstance(row, dict):
                continue
            cells = ensure_list(row.get("cells"))[:MAX_TABLE_COLS]
            if len(ensure_list(row.get("cells"))) > MAX_TABLE_COLS:
                was_limited = True
            rows.append({**row, "cells": cells})
        preview_tables.append({**table, "rows": rows, "preview_limited": True})
    return preview_tables, was_limited


def build_preview_extract_result(extract_result: dict):
    if not isinstance(extract_result, dict):
        return {}
    results = [item for item in ensure_list(extract_result.get("results")) if isinstance(item, dict)]
    return {
        **extract_result,
        "results": results[:MAX_EXTRACT_RESULT_PREVIEW],
        "result_total": len(results),
        "preview_limited": len(results) > MAX_EXTRACT_RESULT_PREVIEW,
    }


def clamp_raw_tables(tables):
    limited = False
    preview_tables = []
    for table in ensure_list(tables):
        if not isinstance(table, list):
            continue
        if len(table) > MAX_TABLE_ROWS:
            limited = True
        preview_rows = []
        for row in table[:MAX_TABLE_ROWS]:
            if not isinstance(row, list):
                continue
            if len(row) > MAX_TABLE_COLS:
                limited = True
            preview_rows.append(row[:MAX_TABLE_COLS])
        preview_tables.append(preview_rows)
    return preview_tables, limited


@router.get("/fields/{task_id}")
def get_fields(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not task.result:
        raise HTTPException(status_code=400, detail="当前任务没有结果")

    task_result, extract_result, match_result = unify_task_payload(task)
    field_defs = get_task_field_defs(task, extract_result)
    visible_fields = get_visible_task_fields(field_defs)
    results = [item for item in ensure_list(extract_result.get("results")) if isinstance(item, dict)]
    preview_results = results[:MAX_RESULT_RECORDS]
    preview_extract_result = build_preview_extract_result(extract_result)
    preview_tables, table_views_limited = clamp_table_views(build_table_views_with_traces(task_result, results, field_defs=field_defs))
    preview_raw_tables, raw_tables_limited = clamp_raw_tables(task_result.get("tables"))
    preview_paragraphs = ensure_list(task_result.get("paragraphs"))[:MAX_PARAGRAPHS]
    raw_text = task_result.get("raw_text") or ""

    return {
        "task_id": task.id,
        "pipeline_used": build_pipeline_used(match_result, extract_result),
        "parse_result_summary": build_parse_result_summary(task_result),
        "extract_result": preview_extract_result,
        "match_result": match_result,
        "file_name": task.file_name,
        "doc_id": task_result.get("doc_id"),
        "doc_type": task_result.get("doc_type"),
        "selected_fields": visible_fields,
        "extract_config": {"table_id": normalize_extract_config(safe_json_loads(task.extract_config)).get("table_id"), "fields": field_defs},
        "total": len(results),
        "results": [format_result_record(task, task_result, item, index, field_defs) for index, item in enumerate(preview_results)],
        "results_preview_limited": len(results) > MAX_RESULT_RECORDS,
        "tables": preview_raw_tables,
        "tables_preview_limited": raw_tables_limited,
        "table_views": preview_tables,
        "table_views_preview_limited": table_views_limited,
        "paragraphs": preview_paragraphs,
        "paragraphs_preview_limited": len(ensure_list(task_result.get("paragraphs"))) > MAX_PARAGRAPHS,
        "raw_text": raw_text[:MAX_RAW_TEXT],
        "raw_text_preview_limited": len(raw_text) > MAX_RAW_TEXT,
    }


@router.get("/fields/{task_id}/source/{field_name}")
def get_field_source(task_id: int, field_name: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not task.result:
        raise HTTPException(status_code=400, detail="当前任务没有结果")

    task_result, extract_result, _ = unify_task_payload(task)
    field_defs = get_task_field_defs(task, extract_result)
    source_data = find_source_record(task_result, field_name, field_defs)
    if source_data is None:
        entity = (
            db.query(ExtractedEntity)
            .filter(ExtractedEntity.task_id == task_id, ExtractedEntity.field_name == field_name)
            .first()
        )
        if entity:
            source_data = {
                "source_file": entity.source_document,
                "source_key": entity.field_name,
                "value": entity.field_value,
                "source_kind": entity.source_kind,
                "source_paragraph": entity.source_paragraph,
                "source_text": entity.source_text,
                "source_table_id": entity.source_table_id,
                "source_row": entity.source_row,
                "source_col": entity.source_col,
                "source_header": entity.source_header,
                "source_locator": entity.source_locator,
                "source_context": entity.source_context,
                "record_index": entity.record_id,
                "raw_record": None,
            }

    if source_data is None:
        raise HTTPException(status_code=404, detail=f"未找到字段 {field_name} 的溯源信息")

    return {
        "task_id": task.id,
        "field_name": field_name,
        "source_file": source_data.get("source_file") or task.file_name,
        "source_key": source_data.get("source_key") or field_name,
        "value": source_data.get("value"),
        "source_kind": source_data.get("source_kind"),
        "source_paragraph": source_data.get("source_paragraph"),
        "source_text": source_data.get("source_text"),
        "source_table_id": source_data.get("source_table_id"),
        "source_row": source_data.get("source_row"),
        "source_col": source_data.get("source_col"),
        "source_header": source_data.get("source_header"),
        "source_locator": source_data.get("source_locator"),
        "source_context": source_data.get("source_context"),
        "record_index": source_data.get("record_index"),
        "raw_record": source_data.get("raw_record"),
    }


@router.get("/fields/{task_id}/records/{record_index}/source")
def get_record_source_api(task_id: int, record_index: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not task.result:
        raise HTTPException(status_code=400, detail="当前任务没有结果")

    task_result, extract_result, _ = unify_task_payload(task)
    field_defs = get_task_field_defs(task, extract_result)
    source_data = get_record_source(task, task_result, record_index, field_defs)
    if source_data is None:
        raise HTTPException(status_code=404, detail=f"未找到记录 {record_index} 的溯源信息")

    return {
        "task_id": task.id,
        "source_file": source_data.get("source_file") or task.file_name,
        "source_key": source_data.get("source_key"),
        "value": source_data.get("value"),
        "source_kind": source_data.get("source_kind"),
        "source_paragraph": source_data.get("source_paragraph"),
        "source_text": source_data.get("source_text"),
        "source_table_id": source_data.get("source_table_id"),
        "source_row": source_data.get("source_row"),
        "source_col": source_data.get("source_col"),
        "source_header": source_data.get("source_header"),
        "source_locator": source_data.get("source_locator"),
        "source_context": source_data.get("source_context"),
        "record_index": source_data.get("record_index"),
        "raw_record": source_data.get("raw_record"),
    }
