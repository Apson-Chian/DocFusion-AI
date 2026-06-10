import hashlib
import json
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db.database import get_db
from ..db.models import FieldEmbeddingCache, Task
from ._result_utils import ensure_list, safe_json_loads
from ._task_progress import set_task_progress


router = APIRouter()
_matcher = None


def normalize_key(text: str) -> str:
    text = str(text or "").strip().lower()
    return re.sub(r"[\s:：_\-（）()\[\]【】,，.。/\\]+", "", text)


def get_matcher():
    global _matcher
    if _matcher is None:
        from ._matcher_engine import FieldSemanticMatcher

        _matcher = FieldSemanticMatcher()
    return _matcher


def extract_kv_pair(text: str):
    text = str(text or "").strip().lstrip("-•* ").strip()
    if not text:
        return None, None
    for sep in ["：", ":"]:
        if sep in text:
            key, value = text.split(sep, 1)
            key = key.strip()
            value = value.strip()
            if key and value:
                return key, value
    return None, None


def infer_row_label(row_values: list[str]):
    for item in row_values:
        value = str(item or "").strip()
        if not value:
            continue
        if re.search(r"\d", value):
            continue
        return value
    return None


def build_input_items_from_parse(parse_data: dict):
    items = []
    paragraphs = ensure_list(parse_data.get("paragraphs"))
    for idx, item in enumerate(paragraphs):
        if not isinstance(item, str):
            continue
        key, value = extract_kv_pair(item)
        if key and value:
            items.append(
                {
                    "source_key": key,
                    "value": value,
                    "source_kind": "paragraph",
                    "source_paragraph": idx,
                    "source_text": item,
                }
            )

    table_views = ensure_list(parse_data.get("table_views"))
    for table in table_views:
        if not isinstance(table, dict):
            continue
        rows = ensure_list(table.get("rows"))
        header = ensure_list(table.get("header"))
        for row in rows[1:]:
            if not isinstance(row, dict):
                continue
            cells = ensure_list(row.get("cells"))
            row_values = [str(cell.get("value", "")).strip() for cell in cells if isinstance(cell, dict)]
            row_label = infer_row_label(row_values)
            for cell in cells:
                if not isinstance(cell, dict):
                    continue
                value = str(cell.get("value", "")).strip()
                col_index = cell.get("col_index")
                if not value or col_index is None:
                    continue
                key = header[col_index] if col_index < len(header) else None
                if not key:
                    continue
                items.append(
                    {
                        "source_key": key,
                        "value": value,
                        "source_kind": "table_cell",
                        "source_paragraph": None,
                        "source_text": f"{key}: {value}",
                        "source_table_id": table.get("table_id"),
                        "source_row": cell.get("row_index"),
                        "source_col": col_index,
                        "source_header": key,
                        "source_locator": cell.get("locator"),
                        "source_context": row_label,
                    }
                )
    return items


def build_input_data_from_items(items: list):
    result = {}
    for item in items:
        key = item.get("source_key")
        value = item.get("value")
        if key and value and key not in result:
            result[key] = value
    return result


def is_suitable_for_match(parse_data: dict, input_data: dict):
    if not input_data:
        return False, "当前文件不包含适合 matcher 处理的字段键值对"

    doc_type = str(parse_data.get("doc_type") or "").lower()
    table_views = ensure_list(parse_data.get("table_views"))
    if doc_type == "xlsx" and table_views:
        row_count = max((int(item.get("row_count") or 0) for item in table_views if isinstance(item, dict)), default=0)
        col_count = max((int(item.get("column_count") or 0) for item in table_views if isinstance(item, dict)), default=0)
        if row_count >= 12 or col_count >= 10:
            return False, "当前文件属于表格型数据集，保留 extract + table trace 更合适"

    business_keywords = [
        "姓名", "名称", "项目名称", "联系人", "联系电话", "电话", "手机号", "邮箱",
        "单位", "公司", "学校", "学院", "专业", "预算", "金额", "负责人",
        "招考单位", "人数", "地址", "法人", "信用代码", "证件号",
    ]

    keys = list(input_data.keys())
    values = list(input_data.values())
    score = 0

    if len(keys) >= 3:
        score += 2
    if sum(1 for key in keys if len(str(key)) <= 12) >= max(2, len(keys) // 2):
        score += 2
    if sum(1 for key in keys if any(keyword in str(key) for keyword in business_keywords)) >= 1:
        score += 3
    if sum(1 for value in values if len(str(value)) >= 40) >= max(1, len(keys) // 2):
        score -= 2
    if len(ensure_list(parse_data.get("tables"))) >= 1 and score < 3:
        score -= 1

    if score >= 3:
        return True, "当前文件具备较明显的业务键值对特征"
    return False, "当前文件更偏向统计文本或表格，matcher 自动跳过"


def build_skipped_result(reason: str, pipeline_used: str):
    return {
        "pipeline_used": pipeline_used,
        "match_status": "skipped",
        "reason": reason,
        "input_data": {},
        "matched_result": None,
        "matched_trace_map": {},
    }


def build_matched_trace_map(matched_result: dict, input_items: list):
    trace_map = {}
    if not isinstance(matched_result, dict):
        return trace_map

    for std_field, matched_value in matched_result.items():
        if matched_value in [None, ""]:
            continue
        target = str(matched_value).strip()
        hit = None
        for item in input_items:
            if str(item.get("value", "")).strip() == target:
                hit = item
                break
        if hit is None:
            for item in input_items:
                item_value = str(item.get("value", "")).strip()
                if target and item_value and (target in item_value or item_value in target):
                    hit = item
                    break

        if hit is None:
            trace_map[std_field] = {"value": matched_value}
            continue

        trace_map[std_field] = {
            "source_key": hit.get("source_key"),
            "value": hit.get("value"),
            "source_kind": hit.get("source_kind"),
            "source_paragraph": hit.get("source_paragraph"),
            "source_text": hit.get("source_text"),
            "source_table_id": hit.get("source_table_id"),
            "source_row": hit.get("source_row"),
            "source_col": hit.get("source_col"),
            "source_header": hit.get("source_header"),
            "source_locator": hit.get("source_locator"),
            "source_context": hit.get("source_context"),
        }
    return trace_map


def get_embedding_cache_key(model_name: str, field_name: str):
    return hashlib.sha256(f"{model_name}:{field_name}".encode("utf-8")).hexdigest()


def get_or_create_field_embedding(db: Session, matcher, field_name: str):
    model_name = getattr(matcher, "model_name", "unknown")
    field_hash = get_embedding_cache_key(model_name, field_name)
    cached = (
        db.query(FieldEmbeddingCache)
        .filter(FieldEmbeddingCache.model_name == model_name, FieldEmbeddingCache.field_hash == field_hash)
        .first()
    )
    if cached:
        try:
            return json.loads(cached.embedding)
        except Exception:
            pass

    vector = matcher.encode_field(field_name)
    if hasattr(vector, "tolist"):
        vector = vector.tolist()

    if cached is None:
        cached = FieldEmbeddingCache(
            model_name=model_name,
            field_name=field_name,
            field_hash=field_hash,
            embedding=json.dumps(vector),
        )
        db.add(cached)
    else:
        cached.embedding = json.dumps(vector)
    db.flush()
    return vector


def process_data_with_cached_embeddings(matcher, input_data: dict, db: Session):
    result = {}
    for chinese_key, value in input_data.items():
        if normalize_key(chinese_key) in matcher.reverse_dict:
            matched_key, _ = matcher.match_field(chinese_key, value)
        else:
            embedding = get_or_create_field_embedding(db, matcher, chinese_key)
            matched_key, _ = matcher.match_field_with_embedding(chinese_key, value, embedding)
        result[matched_key or f"未匹配_{chinese_key}"] = value
    return result


@router.post("/match/{task_id}")
def match_task(task_id: int, db: Session = Depends(get_db)):
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not task.result:
        raise HTTPException(status_code=400, detail="请先完成解析，再进行字段匹配")

    parse_data = safe_json_loads(task.result)
    if not isinstance(parse_data, dict):
        raise HTTPException(status_code=500, detail="解析结果格式异常，无法执行 matcher")

    input_items = build_input_items_from_parse(parse_data)
    input_data = build_input_data_from_items(input_items)
    suitable, reason = is_suitable_for_match(parse_data, input_data)
    pipeline_used = "extract" if task.extract_result else "parse"

    task.status = "matching"
    task.match_status = "running"
    task.error_message = None
    set_task_progress(
        task,
        stage="match",
        current=0,
        total=1,
        percent=94,
        message="正在执行标准字段匹配",
    )
    db.commit()
    db.refresh(task)

    if not suitable:
        skipped_result = build_skipped_result(reason, pipeline_used)
        task.match_result = json.dumps(skipped_result, ensure_ascii=False)
        parse_data["match_result"] = skipped_result
        task.result = json.dumps(parse_data, ensure_ascii=False)
        task.match_status = "skipped"
        task.status = "extracted" if task.extract_result else "parsed"
        task.error_message = None
        set_task_progress(
            task,
            stage="match",
            current=1,
            total=1,
            percent=100,
            message="标准字段匹配已跳过",
        )
        db.commit()
        db.refresh(task)
        return {
            "message": "当前文件不适合 matcher，已自动跳过",
            "task_id": task.id,
            "status": task.status,
            "match_result": skipped_result,
        }

    try:
        matcher = get_matcher()
        matched_result = process_data_with_cached_embeddings(matcher, input_data, db)
        matched_trace_map = build_matched_trace_map(matched_result, input_items)
        save_data = {
            "pipeline_used": "match",
            "match_status": "success",
            "reason": None,
            "input_data": input_data,
            "matched_result": matched_result,
            "matched_trace_map": matched_trace_map,
        }

        task.match_result = json.dumps(save_data, ensure_ascii=False)
        parse_data["match_result"] = save_data
        task.result = json.dumps(parse_data, ensure_ascii=False)
        task.match_status = "success"
        task.status = "matched"
        task.error_message = None
        set_task_progress(
            task,
            stage="match",
            current=1,
            total=1,
            percent=100,
            message="标准字段匹配完成",
        )
        db.commit()
        db.refresh(task)

        return {
            "message": "字段标准化完成",
            "task_id": task.id,
            "status": task.status,
            "match_result": save_data,
        }
    except Exception as exc:
        skipped_result = build_skipped_result(str(exc), pipeline_used)
        task.match_result = json.dumps(skipped_result, ensure_ascii=False)
        parse_data["match_result"] = skipped_result
        task.result = json.dumps(parse_data, ensure_ascii=False)
        task.match_status = "failed"
        task.status = "extracted" if task.extract_result else "parsed"
        task.error_message = str(exc)
        set_task_progress(
            task,
            stage="failed" if task.extract_result else "match",
            current=1,
            total=1,
            percent=100,
            message=f"标准字段匹配失败: {exc}",
        )
        db.commit()
        return {
            "message": "matcher 初始化或执行失败，已回退到抽取结果",
            "task_id": task.id,
            "status": task.status,
            "match_result": skipped_result,
        }
