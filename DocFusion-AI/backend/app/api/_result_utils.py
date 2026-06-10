import json

from ._extract_config import get_field_value_by_slot


def safe_json_loads(value):
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except Exception:
            return {}
    return {}


def ensure_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = safe_json_loads(value)
        if isinstance(parsed, list):
            return parsed
    return []


def get_paragraph_text(paragraphs, paragraph_index):
    if paragraph_index is None:
        return None
    try:
        paragraph_index = int(paragraph_index)
    except Exception:
        return None
    if not isinstance(paragraphs, list) or not paragraphs:
        return None
    if 0 <= paragraph_index < len(paragraphs):
        return str(paragraphs[paragraph_index])
    one_based = paragraph_index - 1
    if 0 <= one_based < len(paragraphs):
        return str(paragraphs[one_based])
    return None


def build_parse_result_summary(task_result: dict):
    paragraphs = ensure_list(task_result.get("paragraphs"))
    tables = ensure_list(task_result.get("tables"))
    raw_text = task_result.get("raw_text") or ""
    table_views = ensure_list(task_result.get("table_views"))
    return {
        "doc_id": task_result.get("doc_id"),
        "doc_type": task_result.get("doc_type"),
        "paragraph_count": len(paragraphs),
        "table_count": len(tables) or len(table_views),
        "raw_text_preview": str(raw_text)[:1000],
    }


def build_pipeline_used(match_result: dict, extract_result: dict):
    if isinstance(match_result, dict) and match_result.get("match_status") == "success":
        return "match"
    if isinstance(extract_result, dict) and ensure_list(extract_result.get("results")):
        return "extract"
    return "parse"


def unify_task_payload(task):
    task_result = safe_json_loads(task.result)
    if not isinstance(task_result, dict):
        task_result = {}

    extract_result = task_result.get("extract_result")
    if not isinstance(extract_result, dict):
        extract_result = safe_json_loads(task.extract_result)
        if not isinstance(extract_result, dict):
            extract_result = {}
        if extract_result:
            task_result["extract_result"] = extract_result

    match_result = task_result.get("match_result")
    if not isinstance(match_result, dict):
        match_result = safe_json_loads(task.match_result)
        if not isinstance(match_result, dict):
            match_result = {}
        if match_result:
            task_result["match_result"] = match_result

    return task_result, extract_result, match_result


def build_table_trace_lookup(records, field_defs=None):
    field_defs = field_defs or []
    lookup = {}
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            continue
        table_id = item.get("source_table_id")
        row = item.get("source_row")
        col = item.get("source_col")
        if not table_id or row is None or col is None:
            continue
        key = (str(table_id), int(row), int(col))
        lookup.setdefault(key, []).append(
            {
                "record_index": index,
                "record_id": item.get("record_id"),
                "indicator": get_field_value_by_slot(item, field_defs, "indicator", default=item.get("指标") or item.get("indicator")),
                "value": get_field_value_by_slot(
                    item,
                    field_defs,
                    "value",
                    default=get_field_value_by_slot(item, field_defs, "yoy", default=item.get("数值") or item.get("value")),
                ),
                "source_header": item.get("source_header"),
                "source_text": item.get("source_text"),
                "source_context": item.get("source_context"),
                "raw_record": item,
            }
        )
    return lookup


def build_table_views_with_traces(task_result: dict, records: list[dict], field_defs=None):
    trace_lookup = build_table_trace_lookup(records, field_defs=field_defs)
    table_views = ensure_list(task_result.get("table_views"))
    enriched = []
    for table in table_views:
        if not isinstance(table, dict):
            continue
        rows = []
        table_id = str(table.get("table_id") or "")
        for row in ensure_list(table.get("rows")):
            if not isinstance(row, dict):
                continue
            cells = []
            for cell in ensure_list(row.get("cells")):
                if not isinstance(cell, dict):
                    continue
                row_index = cell.get("row_index")
                col_index = cell.get("col_index")
                hits = trace_lookup.get((table_id, row_index, col_index), [])
                cells.append({**cell, "trace_hits": hits})
            rows.append({**row, "cells": cells})
        enriched.append({**table, "rows": rows})
    return enriched
