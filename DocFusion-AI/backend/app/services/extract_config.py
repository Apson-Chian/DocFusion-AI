import json
from copy import deepcopy


DEFAULT_TABLE_ID = "table_001"
DEFAULT_FIELDS = [
    {"slot": "category", "field_name": "分类", "type": "string", "visible": True},
    {"slot": "indicator", "field_name": "指标", "type": "string", "visible": True},
    {"slot": "value", "field_name": "数值", "type": "string", "visible": True},
    {"slot": "unit", "field_name": "单位", "type": "string", "visible": True},
    {"slot": "time", "field_name": "时间", "type": "string", "visible": True},
    {"slot": "yoy", "field_name": "同比", "type": "string", "visible": True},
    {"slot": "source_paragraph", "field_name": "来源段落", "type": "int", "visible": False},
]

SLOT_FALLBACK_KEYS = {
    "category": ["分类", "category"],
    "indicator": ["指标", "indicator"],
    "value": ["数值", "value"],
    "unit": ["单位", "unit"],
    "time": ["时间", "time"],
    "yoy": ["同比", "yoy"],
    "source_paragraph": ["来源段落", "source_paragraph", "paragraph_index", "paragraph"],
}

SLOT_NAME_MAP = {
    "category": "分类",
    "indicator": "指标",
    "value": "数值",
    "unit": "单位",
    "time": "时间",
    "yoy": "同比",
    "source_paragraph": "来源段落",
}


def normalize_text(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize_slot(value):
    slot = normalize_text(value).lower()
    if slot in SLOT_NAME_MAP:
        return slot
    return ""


def infer_slot(field_name, raw_slot=None):
    normalized_slot = normalize_slot(raw_slot)
    if normalized_slot:
        return normalized_slot

    name = normalize_text(field_name)
    if not name:
        return ""
    if any(token in name for token in ["来源段落", "段落", "paragraph"]):
        return "source_paragraph"
    if any(token in name for token in ["分类", "类别", "地区", "行业", "分组"]):
        return "category"
    if any(token in name for token in ["指标", "项目", "名称", "字段", "口径"]):
        return "indicator"
    if any(token in name for token in ["单位", "量纲"]):
        return "unit"
    if any(token in name for token in ["时间", "日期", "年月", "时点"]):
        return "time"
    if any(token in name for token in ["同比", "环比", "增速", "增长率", "变化率", "涨跌幅"]):
        return "yoy"
    if any(token in name for token in ["数值", "金额", "数量", "值", "占比", "比重"]):
        return "value"
    return "indicator"


def infer_field_type(slot, raw_type=None):
    value = normalize_text(raw_type).lower()
    if value in {"int", "float", "double", "number", "numeric"}:
        return "numeric"
    if value in {"date", "datetime", "time"}:
        return "date"
    if value in {"string", "text", "category", "enum"}:
        return "category" if value in {"category", "enum"} else "text"

    if slot in {"value", "yoy"}:
        return "numeric"
    if slot == "time":
        return "date"
    if slot == "category":
        return "category"
    if slot == "source_paragraph":
        return "numeric"
    return "text"


def default_field_label(slot):
    return SLOT_NAME_MAP.get(slot, "")


def normalize_field(item):
    if not isinstance(item, dict):
        return None

    field_name = normalize_text(item.get("field_name") or item.get("label") or item.get("name"))
    slot = infer_slot(field_name, item.get("slot") or item.get("role"))
    if not field_name:
        field_name = default_field_label(slot)
    if not field_name or not slot:
        return None

    aliases = item.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]

    visible = item.get("visible")
    if visible is None:
        visible = slot != "source_paragraph"

    return {
        "slot": slot,
        "field_name": field_name,
        "type": infer_field_type(slot, item.get("type")),
        "aliases": [normalize_text(alias) for alias in aliases if normalize_text(alias)],
        "description": normalize_text(item.get("description")),
        "multi": bool(item.get("multi", True)),
        "visible": bool(visible),
    }


def build_default_extract_config():
    return {"table_id": DEFAULT_TABLE_ID, "fields": deepcopy(DEFAULT_FIELDS)}


def normalize_extract_config(raw_config=None):
    base = build_default_extract_config()
    if not isinstance(raw_config, dict):
        return base

    table_id = normalize_text(raw_config.get("table_id")) or DEFAULT_TABLE_ID
    raw_fields = raw_config.get("fields")
    fields = []

    if isinstance(raw_fields, list):
        for item in raw_fields:
            normalized = normalize_field(item)
            if normalized is None:
                continue
            if item.get("enabled", True) is False:
                continue
            fields.append(normalized)

    if not fields:
        fields = deepcopy(base["fields"])

    has_source = any(item["slot"] == "source_paragraph" for item in fields)
    if not has_source:
        fields.append(
            {
                "slot": "source_paragraph",
                "field_name": SLOT_NAME_MAP["source_paragraph"],
                "type": "numeric",
                "aliases": [],
                "description": "",
                "multi": False,
                "visible": False,
            }
        )

    return {"table_id": table_id, "fields": fields}


def serialize_extract_config(raw_config=None):
    return json.dumps(normalize_extract_config(raw_config), ensure_ascii=False, sort_keys=True)


def parse_extract_config_text(raw_text):
    text = normalize_text(raw_text)
    if not text:
        return build_default_extract_config()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("提取字段配置必须是 JSON 对象")
    return normalize_extract_config(parsed)


def get_visible_fields(raw_config=None):
    config = normalize_extract_config(raw_config)
    return [item for item in config["fields"] if item.get("visible", True) and item["slot"] != "source_paragraph"]


def get_field_value_by_slot(record, field_defs, slot, default=None):
    if not isinstance(record, dict):
        return default

    names = [item["field_name"] for item in field_defs if item.get("slot") == slot]
    names.extend(SLOT_FALLBACK_KEYS.get(slot, []))

    for name in names:
        if name in record and record[name] not in [None, ""]:
            return record[name]
    return default
