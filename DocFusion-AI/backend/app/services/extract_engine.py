import json
import os
import re
from pathlib import Path

from ..core.paths import SERVICE_ASSETS_DIR
from .extract_config import get_field_value_by_slot, infer_slot

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data.json"
WORD_PATH = SERVICE_ASSETS_DIR / "word.json"
OUTPUT_PATH = BASE_DIR / "test_output.json"

DEFAULT_BASE_URL = os.getenv("EXTRACT_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
DEFAULT_MODEL = os.getenv("EXTRACT_MODEL", "qwen-plus")
MAX_FIELD_CANDIDATES = 12
ENTITY_CONTEXT_WINDOW = 1
MAX_ENTITY_CANDIDATES = 24
MAX_XLSX_ROWS = 40
EMPTY_VALUE = "略"
NUMBER_PATTERN = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(万亿元|亿元|万美元|亿美元|万元|元/人[·/]月|元/人次|元/间夜|元|亿千瓦时|亿千瓦|万台|万套|万辆|万公顷|公顷|万吨|亿吨|吨标准煤|万吨标准煤|万吨标煤|万立方米|立方米|万头|头|万张|张|万平方米|平方米|万册次|册次|亿册|册|亿人次|万人次|万人|人次|万人户|万户|万件/套|件/套|万个|个|万家|家|场|次|%|％|个百分点|‰)?"
)
DATE_PATTERN = re.compile(r"(\d{4}年\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月|\d{4}年|\d{4}-\d{1,2}-\d{1,2})")
FOOTNOTE_PATTERN = re.compile(r"\[\d+\]")
YEAR_ONLY_PATTERN = re.compile(r"^\d{4}$")
YOY_TEXT_PATTERN = re.compile(r"(同比|比上年|较上年|比年初|较年初|增长|下降|提高|减少|增产|减产)")
CATEGORY_PATTERN = re.compile(
    r"(全国|全市|全省|全县|东部地区|中部地区|西部地区|东北地区|京津冀地区|长江经济带地区|长三角地区|粤港澳大湾区|城镇居民|农村居民|城市|农村|第一产业|第二产业|第三产业|夏粮|早稻|秋粮|稻谷|小麦|玉米|大豆|棉花|油料|糖料|茶叶|猪肉|牛肉|羊肉|禽肉|养殖|捕捞|国有控股企业|股份制企业|外商及港澳台投资企业|私营企业|采矿业|制造业|电力、热力、燃气及水生产和供应业|公共图书馆|群众文化机构|旅行社|星级饭店|A级景区|县以上|县及县以下)"
)


def load_json_file(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_data_json(path=DATA_PATH):
    return load_json_file(path)


def load_word_json(path=WORD_PATH):
    return load_json_file(path)


def normalize_text(text):
    if text is None:
        return ""
    text = str(text).replace("\u3000", " ")
    text = text.replace("\r", "\n")
    text = FOOTNOTE_PATTERN.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = text.replace("：", ":")
    return text.strip()


def normalize_for_match(text):
    normalized = normalize_text(text).lower()
    return re.sub(r"[，,。；;！!？?\-_/（）()\[\]【】\"'`~·:：]", "", normalized)


def normalize_cell(cell):
    text = normalize_text(cell)
    if not text or text in {"?", "None", "nan", "NaN", "NULL", "null"}:
        return ""
    return text


def split_text_units(text):
    normalized = normalize_text(text)
    if not normalized:
        return []
    units = []
    for chunk in re.split(r"(?:\n|(?<=。)|(?<=；)|(?<=;)|(?<=！)|(?<=!)|(?<=？)|(?<=\?))", normalized):
        chunk = normalize_text(chunk)
        if not chunk:
            continue
        if chunk.startswith("- "):
            chunk = chunk[2:].strip()
        if "其中:" in chunk:
            parts = [normalize_text(item) for item in chunk.split("其中:") if normalize_text(item)]
            units.extend(parts)
            continue
        units.append(chunk)
    return units


def extract_json_text(raw_text):
    if not raw_text:
        return ""
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_text.strip(), re.S)
    return fenced.group(1).strip() if fenced else raw_text.strip()


def safe_load_json(raw_text):
    try:
        return json.loads(extract_json_text(raw_text))
    except Exception:
        return None


def ensure_paragraphs(data):
    paragraphs = data.get("paragraphs") or []
    cleaned = [normalize_text(item) for item in paragraphs if normalize_text(item)]
    if cleaned:
        return cleaned
    raw_text = normalize_text(data.get("raw_text"))
    if not raw_text:
        return []
    return [item.strip() for item in re.split(r"\n+", raw_text) if item.strip()]


def ensure_tables(data):
    tables = data.get("tables") or []
    cleaned_tables = []
    for table in tables:
        if not isinstance(table, list):
            continue
        cleaned_rows = []
        for row in table:
            if not isinstance(row, list):
                continue
            cleaned_row = [normalize_cell(cell) for cell in row]
            if any(cleaned_row):
                cleaned_rows.append(cleaned_row)
        if cleaned_rows:
            cleaned_tables.append(cleaned_rows)
    return cleaned_tables


def ensure_table_views(data):
    table_views = data.get("table_views") or []
    cleaned = []
    for item in table_views:
        if not isinstance(item, dict):
            continue
        rows = item.get("rows") or []
        if not isinstance(rows, list) or not rows:
            continue
        cleaned.append(item)
    return cleaned


def build_client():
    api_key = (
        os.getenv("EXTRACT_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    if not api_key or OpenAI is None:
        return None
    return OpenAI(api_key=api_key, base_url=DEFAULT_BASE_URL)


def detect_doc_type(data):
    explicit = normalize_text(data.get("doc_type")).lower()
    if explicit in {"xlsx", "docx", "txt", "md", "word"}:
        return "xlsx" if explicit == "xlsx" else explicit
    doc_id = normalize_text(data.get("doc_id")).lower()
    if "xlsx" in doc_id or "excel_" in doc_id:
        return "xlsx"
    if "docx" in doc_id or "word_" in doc_id:
        return "word"
    if doc_id.endswith(".md") or "_md" in doc_id:
        return "md"
    if doc_id.endswith(".txt") or "_txt" in doc_id:
        return "txt"
    tables = ensure_tables(data)
    if tables:
        return "xlsx"
    return "text"


def make_record_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index:03d}"


def make_locator(row_index: int | None, col_index: int | None) -> str | None:
    if row_index is None or col_index is None or row_index < 0 or col_index < 0:
        return None
    col = col_index + 1
    letters = []
    while col > 0:
        col, remainder = divmod(col - 1, 26)
        letters.append(chr(65 + remainder))
    return f"{''.join(reversed(letters))}{row_index + 1}"


def build_paragraph_source_text(paragraphs, paragraph_index):
    if not isinstance(paragraphs, list):
        return None
    if 0 <= paragraph_index < len(paragraphs):
        return normalize_text(paragraphs[paragraph_index])
    return None


def build_table_source_text(table_title, row_label, column_name, cell_value, locator):
    parts = []
    if table_title:
        parts.append(f"表格 {table_title}")
    if locator:
        parts.append(f"单元格 {locator}")
    if row_label:
        parts.append(f"行标识 {row_label}")
    if column_name:
        parts.append(f"字段 {column_name}")
    if cell_value:
        parts.append(f"值 {cell_value}")
    return " | ".join(parts)


def build_row_context(header, row, focus_col, max_pairs=6):
    pairs = []
    for index, value in enumerate(row):
        value = normalize_text(value)
        if not value or index == focus_col:
            continue
        key = normalize_text(header[index]) if index < len(header) else f"列{index + 1}"
        if not key:
            key = f"列{index + 1}"
        pairs.append(f"{key}={value}")
        if len(pairs) >= max_pairs:
            break
    return "；".join(pairs)


def infer_field_type(field_name, raw_type, slot=""):
    field_type = normalize_text(raw_type).lower()
    if field_type in {"int", "float", "double", "number", "numeric"}:
        return "numeric"
    if field_type in {"date", "datetime", "time"}:
        return "date"
    if field_type in {"category", "enum"}:
        return "category"
    if slot in {"value", "yoy", "source_paragraph"}:
        return "numeric"
    if slot == "time":
        return "date"
    if slot == "category":
        return "category"
    if any(token in field_name for token in ["时间", "日期", "年月", "时点"]):
        return "date"
    if any(token in field_name for token in ["数值", "同比", "环比", "数量", "金额", "比重", "增速", "增长率", "占比", "单位"]):
        return "numeric" if field_name != "单位" else "text"
    if any(token in field_name for token in ["分类", "类别", "地区", "行业"]):
        return "category"
    return "text"


def build_field_tasks(word_config):
    tasks = []
    for item in word_config.get("fields", []):
        field_name = normalize_text(item.get("field_name") or item.get("name") or item.get("word"))
        if not field_name:
            continue
        slot = infer_slot(field_name, item.get("slot"))
        tasks.append(
            {
                "name": field_name,
                "field_name": field_name,
                "slot": slot,
                "type": infer_field_type(field_name, item.get("type") or "string", slot=slot),
                "aliases": [normalize_text(alias) for alias in item.get("aliases", []) if normalize_text(alias)],
                "description": normalize_text(item.get("description") or ""),
                "multi": bool(item.get("multi", True)),
            }
        )
    return tasks


def set_slot_value(record, field_tasks, slot, value):
    for task in field_tasks:
        if task.get("slot") == slot:
            record[task["name"]] = value


def get_slot_value(record, field_tasks, slot, default=EMPTY_VALUE):
    return get_field_value_by_slot(record, field_tasks, slot, default=default)


def build_description_prompt(raw_text, field_tasks):
    payload = {
        "task": "阅读原文后，为每个字段补充一个适合召回与抽取的 description，并给出可用别名。",
        "requirements": [
            "只基于原文语义理解字段在当前文档中的可能含义。",
            "description 应简短、可检索，适合后续 paragraph 召回。",
            "aliases 给出 0 到 5 个可能原文表达。",
            "如果无法判断，也要保留字段并给出保守 description。",
            "只输出 JSON。",
        ],
        "fields": [{"name": item["name"], "type": item["type"]} for item in field_tasks],
        "document": raw_text[:12000],
        "output_schema": {
            "fields": [
                {
                    "name": "字段名",
                    "description": "字段在当前文档中的含义描述",
                    "aliases": ["别名1", "别名2"],
                }
            ]
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def default_field_description(field_task):
    defaults = {
        "category": "统计对象所属的类别、地区、层级或分组名称",
        "indicator": "描述统计口径或指标名称，例如收入、人数、数量、增速等",
        "value": "与指标对应的具体数值",
        "unit": "数值使用的单位，例如亿元、万人次、家、%",
        "time": "该统计口径对应的时间、时点或年份",
        "yoy": "同比增长、同比下降或同比变化幅度",
        "source_paragraph": "结果所在的主要段落编号",
    }
    return defaults.get(field_task.get("slot"), f"与“{field_task['name']}”相关的文本信息")


def default_field_aliases(field_task):
    defaults = {
        "category": ["全国", "东部地区", "中部地区", "西部地区", "城镇居民", "农村居民", "城市", "农村"],
        "indicator": ["收入", "利润", "人数", "人次", "数量", "总量", "比重", "事业费", "营业收入", "床位", "支出", "覆盖率"],
        "value": ["亿元", "万人次", "万人", "家", "%", "个百分点", "万张", "万平方米", "个"],
        "unit": ["万亿元", "亿元", "万元", "元", "家", "%", "个百分点", "万人次", "万人", "万张", "万平方米", "件/套"],
        "time": ["年末", "全年", "截至", "发布时间"],
        "yoy": ["同比", "增长", "下降", "提高", "持平", "比上年"],
    }
    return defaults.get(field_task.get("slot"), [])


def enrich_field_tasks_with_descriptions(data, field_tasks, client, model=DEFAULT_MODEL):
    raw_text = normalize_text(data.get("raw_text"))
    if not raw_text:
        raw_text = "\n".join(ensure_paragraphs(data))
    if client is None:
        enriched = []
        for item in field_tasks:
            clone = dict(item)
            if not clone.get("description"):
                clone["description"] = default_field_description(clone)
            clone["aliases"] = sorted(set(clone.get("aliases", []) + default_field_aliases(clone)))
            enriched.append(clone)
        return enriched
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是信息抽取预处理助手。只输出 JSON。"},
                {"role": "user", "content": build_description_prompt(raw_text, field_tasks)},
            ],
            temperature=0.1,
        )
        parsed = safe_load_json(completion.choices[0].message.content)
    except Exception:
        parsed = None

    description_map = {}
    if isinstance(parsed, dict):
        for item in parsed.get("fields", []):
            if not isinstance(item, dict):
                continue
            name = normalize_text(item.get("name"))
            if not name:
                continue
            aliases = item.get("aliases") or []
            if isinstance(aliases, str):
                aliases = [aliases]
            description_map[name] = {
                "description": normalize_text(item.get("description")),
                "aliases": [normalize_text(alias) for alias in aliases if normalize_text(alias)],
            }

    enriched = []
    for item in field_tasks:
        clone = dict(item)
        llm_info = description_map.get(clone["name"], {})
        aliases = clone.get("aliases", []) + llm_info.get("aliases", []) + default_field_aliases(clone)
        clone["aliases"] = sorted({alias for alias in aliases if alias and alias != clone["name"]})
        clone["description"] = llm_info.get("description") or clone.get("description") or default_field_description(clone)
        enriched.append(clone)
    return enriched


def score_paragraph_for_task(field_task, paragraph):
    normalized_paragraph = normalize_for_match(paragraph)
    score = 0.0
    matched_terms = []
    for term in [field_task["name"], *field_task.get("aliases", [])]:
        normalized_term = normalize_for_match(term)
        if normalized_term and normalized_term in normalized_paragraph:
            score += 2.0 if term == field_task["name"] else 1.2
            matched_terms.append(term)
    for token in re.split(r"[\s,，;；/]+", field_task.get("description", "")):
        normalized_token = normalize_for_match(token)
        if len(normalized_token) >= 2 and normalized_token in normalized_paragraph:
            score += 0.35
    if field_task["type"] == "numeric" and re.search(r"\d", paragraph):
        score += 0.8
    if field_task["type"] == "date" and DATE_PATTERN.search(paragraph):
        score += 0.9
    if field_task.get("slot") == "source_paragraph":
        score = 0.0
    return round(score, 4), sorted(set(matched_terms))


def score_unit_for_fields(unit_text, field_tasks):
    total = 0.0
    hit_fields = set()
    matched_terms = set()
    for task in field_tasks:
        if task.get("slot") == "source_paragraph":
            continue
        score, terms = score_paragraph_for_task(task, unit_text)
        if score <= 0:
            continue
        total += score
        hit_fields.add(task["name"])
        matched_terms.update(terms)
    if re.search(r"\d", unit_text):
        total += 0.8
    if any(token in unit_text for token in ["同比", "增长", "下降", "比上年"]):
        total += 0.6
    return round(total, 4), sorted(hit_fields), sorted(matched_terms)


def build_entity_candidates(field_tasks, paragraphs):
    unit_candidates = []
    for para_id, paragraph in enumerate(paragraphs):
        for unit_index, unit_text in enumerate(split_text_units(paragraph) or [paragraph]):
            score, fields, matched_terms = score_unit_for_fields(unit_text, field_tasks)
            if score <= 0:
                continue
            unit_candidates.append(
                {
                    "para_id": para_id,
                    "unit_index": unit_index,
                    "unit_text": unit_text,
                    "score": score,
                    "fields": fields,
                    "matched_terms": matched_terms,
                }
            )
    unit_candidates.sort(key=lambda item: (-item["score"], item["para_id"], item["unit_index"]))
    entities = []
    seen_signatures = set()
    for anchor in unit_candidates[:MAX_ENTITY_CANDIDATES]:
        para_ids = {anchor["para_id"]}
        for offset in range(-ENTITY_CONTEXT_WINDOW, ENTITY_CONTEXT_WINDOW + 1):
            candidate_id = anchor["para_id"] + offset
            if 0 <= candidate_id < len(paragraphs):
                para_ids.add(candidate_id)
        entity_paragraphs = [{"para_id": para_id, "text": paragraphs[para_id]} for para_id in sorted(para_ids)]
        signature = (anchor["para_id"], anchor["unit_index"], tuple(item["para_id"] for item in entity_paragraphs))
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        entities.append(
            {
                "anchor_para_id": anchor["para_id"],
                "anchor_unit_text": anchor["unit_text"],
                "score": anchor["score"],
                "fields": anchor["fields"],
                "matched_terms": anchor["matched_terms"],
                "paragraphs": entity_paragraphs,
            }
        )
    return entities


def build_entity_extraction_prompt(field_tasks, entity_candidate):
    payload = {
        "task": "你将看到同一个实体相关的一组段落。请基于这些段落抽取 1 条结构化结果。",
        "requirements": [
            "只输出一个实体对象，不要拆成多条。",
            "字段应尽量从段落原文中抽取，没有就返回空字符串。",
            "如果这组段落不足以形成有效实体，返回 matched=false。",
            "来源段落 填最主要证据所在的 para_id。",
            "优先围绕 anchor_unit_text 这条核心文本抽取，再参考相邻段落补全分类、时间、单位等。",
            "输出 record 时，键名必须严格使用 schema 中的字段名。",
            "不要输出 markdown，不要输出解释，只输出 JSON。",
        ],
        "schema": [{"field_name": item["name"], "type": item["type"], "description": item["description"], "aliases": item.get("aliases", [])} for item in field_tasks],
        "entity_candidate": entity_candidate,
        "output_schema": {
            "matched": False,
            "confidence": 0.0,
            "record": {item["name"]: "" for item in field_tasks},
            "evidence_para_id": -1,
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def extract_first_numeric_with_unit(text):
    if not text:
        return "", ""
    match = NUMBER_PATTERN.search(normalize_text(text))
    if not match:
        return "", ""
    return normalize_text(match.group(1)), normalize_text(match.group(2))


def extract_first_date(text):
    if not text:
        return ""
    match = DATE_PATTERN.search(normalize_text(text))
    return normalize_text(match.group(1)) if match else ""


def extract_year_from_doc(data):
    candidates = [
        normalize_text(data.get("doc_id")),
        " ".join(ensure_paragraphs(data)[:3]),
        normalize_text(data.get("raw_text"))[:200],
    ]
    for text in candidates:
        match = re.search(r"(20\d{2})年", text)
        if match:
            return f"{match.group(1)}年"
    for text in candidates:
        date_text = extract_first_date(text)
        if date_text:
            year_match = re.search(r"(20\d{2})", date_text)
            if year_match:
                return f"{year_match.group(1)}年"
    return EMPTY_VALUE


def is_numeric_text(text):
    text = normalize_text(text)
    if not text:
        return False
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return True
    return bool(NUMBER_PATTERN.fullmatch(text))


def clean_indicator_candidate(text):
    candidate = normalize_text(text)
    candidate = re.sub(r"^(全年|年末|截至|其中|全国|全市|全省|全县|共|有|实现|达到|为|其|该)", "", candidate)
    candidate = candidate.strip("，,。；;:：、 ")
    candidate = re.sub(r"(同比.*|比上年.*)$", "", candidate).strip()
    candidate = re.sub(r"[\d\-\.\s%％]+$", "", candidate).strip()
    if candidate in {"", "共", "有", "为", "达", "同比", "比上年"}:
        return ""
    return candidate


def infer_indicator_from_prefix(prefix):
    prefix = normalize_text(prefix)
    if not prefix:
        return ""
    prefix = re.sub(r"^(其中|以及|并|、)", "", prefix).strip()
    parts = re.split(r"[，,；;。]", prefix)
    candidate = clean_indicator_candidate(parts[-1] if parts else prefix)
    if candidate:
        return candidate
    return clean_indicator_candidate(prefix[-20:])


def find_numeric_candidates(text):
    normalized = normalize_text(text)
    candidates = []
    for match in NUMBER_PATTERN.finditer(normalized):
        value = normalize_text(match.group(1))
        unit = normalize_text(match.group(2))
        start, end = match.span()
        prefix = normalized[max(0, start - 30):start]
        suffix = normalized[end:min(len(normalized), end + 12)]
        label = infer_indicator_from_prefix(prefix)
        context = normalized[max(0, start - 40):min(len(normalized), end + 20)]
        is_ratio = unit in {"%", "％", "个百分点", "‰"}
        is_yoy = any(token in context for token in ["同比", "比上年", "较上年", "增长", "下降", "提高", "减少"]) and is_ratio
        candidates.append(
            {
                "value": value,
                "unit": unit,
                "label": label,
                "context": context,
                "is_yoy": is_yoy,
                "is_ratio": is_ratio,
            }
        )
    return candidates


def find_category(text):
    normalized = normalize_text(text)
    if not normalized:
        return ""
    match = CATEGORY_PATTERN.search(normalized)
    if match:
        return normalize_text(match.group(1))
    return ""


def split_metric_fragments(text):
    normalized = normalize_text(text)
    if not normalized:
        return []
    normalized = re.sub(r"(其中|分季度看|分区域看|按消费类型分|按常住地分|分经济类型看|分门类看|分行业看)[:：，,]?", "；", normalized)

    primary_parts = []
    for part in re.split(r"[。；;]", normalized):
        part = normalize_text(part)
        if not part:
            continue
        if part.startswith("- "):
            part = part[2:].strip()
        primary_parts.append(part)

    fragments = []
    for part in primary_parts:
        comma_parts = []
        buffer = ""
        for chunk in re.split(r"(，|,)", part):
            if chunk in {"，", ","}:
                buffer += chunk
                continue
            piece = normalize_text(chunk)
            if not piece:
                continue
            tentative = f"{buffer}{piece}".strip("，, ")
            if (
                buffer
                and re.search(r"\d", piece)
                and not YOY_TEXT_PATTERN.match(piece)
                and re.search(r"\d", buffer)
            ):
                left = normalize_text(buffer.strip("，, "))
                if left:
                    comma_parts.append(left)
                buffer = piece
            else:
                buffer = tentative
        tail = normalize_text(buffer.strip("，, "))
        if tail:
            comma_parts.append(tail)
        fragments.extend(item for item in comma_parts if item)
    return fragments


def find_relevant_category(text):
    normalized = normalize_text(text)
    if not normalized:
        return ""
    leading_match = re.search(
        r"^(?:其中|此外|另外|全年|年末|截至|按[^，,]+分|分[^，,]+看)?\s*(全国|全市|全省|全县|东部地区|中部地区|西部地区|东北地区|京津冀地区|长江经济带地区|长三角地区|粤港澳大湾区|城镇居民|农村居民|城市|农村|第一产业|第二产业|第三产业|夏粮|早稻|秋粮|稻谷|小麦|玉米|大豆|棉花|油料|糖料|茶叶|猪肉|牛肉|羊肉|禽肉|养殖|捕捞|国有控股企业|股份制企业|外商及港澳台投资企业|私营企业|采矿业|制造业|电力、热力、燃气及水生产和供应业|公共图书馆|群众文化机构|旅行社|星级饭店|A级景区|县以上|县及县以下)",
        normalized,
    )
    if leading_match:
        return normalize_text(leading_match.group(1))
    return ""


def clean_indicator_text(text, category=""):
    candidate = normalize_text(text)
    if not candidate:
        return ""
    if category and candidate.startswith(category):
        candidate = candidate[len(category):].strip()
    candidate = re.sub(r"^(其中|全年|全年来看|年末|截至|截至目前|初步核算|分季度看|分区域看|在规模以上工业中|全国共有|全市共有|全省共有|全国平均每万人|全国人均|共|有|达到|实现|为|的|占)", "", candidate)
    candidate = re.sub(r"(比上年.*|同比.*|较上年.*|较年初.*|比年初.*)$", "", candidate).strip()
    candidate = candidate.strip("，,。；;:：、 ")
    candidate = re.sub(r"(共有|拥有|接待|实现|完成|达到)$", "", candidate).strip()
    candidate = re.sub(r"\s+", "", candidate)
    if len(candidate) <= 1:
        return ""
    return candidate


def infer_indicator_for_match(fragment, match, category=""):
    prefix = normalize_text(fragment[:match.start()])
    prefix = re.sub(r".*[，,:：]", "", prefix)
    candidate = clean_indicator_text(prefix, category=category)
    if candidate:
        return candidate
    window_prefix = normalize_text(fragment[max(0, match.start() - 24):match.start()])
    return clean_indicator_text(window_prefix, category=category)


def find_yoy_after_index(fragment, start_index):
    suffix = normalize_text(fragment[start_index:])
    patterns = [
        r"(?:同比|比上年|较上年)(?:增长|下降|提高|减少|增产|减产)?\s*(-?\d+(?:\.\d+)?)\s*(%|％|个百分点|‰)?",
        r"(?:增长|下降|提高|减少|增产|减产)\s*(-?\d+(?:\.\d+)?)\s*(%|％|个百分点|‰)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, suffix)
        if match:
            value = normalize_text(match.group(1))
            unit = normalize_text(match.group(2) or "%")
            return f"{value}{unit}"
    return ""


def build_record_from_fragment(fragment, para_id, fallback_time, field_tasks, inherited_category=""):
    normalized = normalize_text(fragment)
    if not normalized:
        return []
    if normalized.startswith("发布时间") or DATE_PATTERN.fullmatch(normalized):
        return []
    row_time = extract_first_date(normalized) or fallback_time
    category = find_relevant_category(normalized) or inherited_category or EMPTY_VALUE
    metrics = []
    for match in NUMBER_PATTERN.finditer(normalized):
        value = normalize_text(match.group(1))
        unit = normalize_text(match.group(2))
        if not value:
            continue
        if YEAR_ONLY_PATTERN.fullmatch(value) and unit in {"", "年"}:
            continue
        if row_time and re.search(rf"{re.escape(value)}(?:年|月|日)", normalized[max(0, match.start() - 2):match.end() + 1]):
            continue
        metric_text = normalize_text(normalized[max(0, match.start() - 10):min(len(normalized), match.end() + 18)])
        if match.start() > 0 and normalized[max(0, match.start() - 4):match.start()].endswith("第"):
            continue
        is_ratio = unit in {"%", "％", "个百分点", "‰"}
        local_prefix = normalize_text(normalized[max(0, match.start() - 10):match.start()])
        if is_ratio and YOY_TEXT_PATTERN.search(local_prefix):
            continue
        indicator = infer_indicator_for_match(normalized, match, category=category if category != EMPTY_VALUE else "")
        if not indicator:
            continue
        yoy = find_yoy_after_index(normalized, match.end())
        if is_ratio and YOY_TEXT_PATTERN.search(metric_text):
            yoy = f"{value}{unit or '%'}"
            value = ""
            unit = ""

        record = build_fallback_record(field_tasks, para_id=para_id, time_value=row_time)
        set_slot_value(record, field_tasks, "category", category or EMPTY_VALUE)
        set_slot_value(record, field_tasks, "indicator", indicator or EMPTY_VALUE)
        set_slot_value(record, field_tasks, "value", value or EMPTY_VALUE)
        set_slot_value(record, field_tasks, "unit", unit or EMPTY_VALUE)
        set_slot_value(record, field_tasks, "yoy", yoy or EMPTY_VALUE)
        record["record_id"] = make_record_id("text", para_id + 1)
        record["source_kind"] = "paragraph"
        record["source_text"] = normalized
        record["confidence"] = 0.72
        metrics.append(record)
    return metrics


def extract_from_text_fragments(data, field_tasks):
    paragraphs = ensure_paragraphs(data)
    fallback_time = extract_year_from_doc(data)
    records = []
    for para_id, paragraph in enumerate(paragraphs):
        paragraph_text = normalize_text(paragraph)
        if not re.search(r"\d", paragraph_text):
            continue
        inherited_category = find_relevant_category(paragraph_text)
        for fragment in split_metric_fragments(paragraph_text):
            records.extend(
                build_record_from_fragment(
                    fragment,
                    para_id=para_id,
                    fallback_time=extract_first_date(paragraph_text) or fallback_time,
                    field_tasks=field_tasks,
                    inherited_category=inherited_category,
                )
            )
    return sanitize_and_merge_results(records, field_tasks)


def find_indicator(text):
    normalized = normalize_text(text)
    if not normalized:
        return ""
    candidates = []
    patterns = [
        r"([\u4e00-\u9fa5A-Za-z0-9（）()、·\-]+?)(?:为|达|有|共|占|收入|支出|实现)\s*-?\d",
        r"([\u4e00-\u9fa5A-Za-z0-9（）()、·\-]+?)同比(?:增长|下降|提高|减少)",
        r"([\u4e00-\u9fa5A-Za-z0-9（）()、·\-]+?)(?:占比|比重|增长率|覆盖率)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        candidate = clean_indicator_candidate(match.group(1))
        if candidate:
            candidates.append(candidate)
    for numeric in find_numeric_candidates(normalized):
        if numeric["label"]:
            candidates.append(numeric["label"])
    return candidates[0] if candidates else ""


def find_yoy(text):
    normalized = normalize_text(text)
    if not normalized:
        return ""
    patterns = [
        r"同比(?:增长|下降|提高|减少)?\s*(-?\d+(?:\.\d+)?)\s*(%|％|个百分点)",
        r"比上年(?:增长|下降|提高|减少)?\s*(-?\d+(?:\.\d+)?)\s*(%|％|个百分点)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            value = normalize_text(match.group(1))
            unit = normalize_text(match.group(2) or "%")
            return f"{value}{unit if unit else ''}"
    return ""


def find_value_and_unit(text):
    candidates = find_numeric_candidates(text)
    filtered = []
    for item in candidates:
        if re.fullmatch(r"\d{4}", item["value"]) and not item["unit"] and len(candidates) > 1:
            continue
        filtered.append(item)
    for item in filtered:
        if item["is_yoy"]:
            continue
        if item["value"]:
            return item["value"], item["unit"], item["label"]
    for item in filtered:
        if item["value"]:
            return item["value"], item["unit"], item["label"]
    return "", "", ""


def choose_best_text_units(entity_candidate):
    units = []
    for para in entity_candidate["paragraphs"]:
        for unit in split_text_units(para["text"]):
            if unit == entity_candidate["anchor_unit_text"]:
                units.insert(0, {"para_id": para["para_id"], "text": unit})
            else:
                units.append({"para_id": para["para_id"], "text": unit})
    if not units:
        units = [{"para_id": para["para_id"], "text": para["text"]} for para in entity_candidate["paragraphs"]]
    return units


def fallback_extract_one_entity(field_tasks, entity_candidate):
    text_units = choose_best_text_units(entity_candidate)
    merged_text = " ".join(item["text"] for item in text_units)
    record = {task["name"]: "" for task in field_tasks}
    anchor_text = normalize_text(entity_candidate.get("anchor_unit_text", ""))
    anchor_para_id = entity_candidate["anchor_para_id"]

    category = find_category(anchor_text) or find_category(merged_text)
    value, unit, derived_indicator = find_value_and_unit(anchor_text)
    if not value:
        value, unit, derived_indicator = find_value_and_unit(merged_text)
    indicator = find_indicator(anchor_text) or derived_indicator or find_indicator(merged_text)
    time_value = extract_first_date(anchor_text) or extract_first_date(merged_text)
    yoy = find_yoy(anchor_text) or find_yoy(merged_text)

    if yoy and value and unit in {"%", "％", "个百分点"} and yoy.startswith(value):
        value, unit = "", ""
    if not indicator and value:
        indicator = derived_indicator

    for task in field_tasks:
        slot = task.get("slot")
        if slot == "source_paragraph":
            continue
        if slot == "category":
            record[task["name"]] = category
        elif slot == "indicator":
            record[task["name"]] = indicator
        elif slot == "value":
            record[task["name"]] = value
        elif slot == "unit":
            record[task["name"]] = unit
        elif slot == "time":
            record[task["name"]] = time_value
        elif slot == "yoy":
            record[task["name"]] = yoy
        elif task["type"] == "date":
            record[task["name"]] = time_value
        elif task["type"] == "numeric":
            record[task["name"]] = value

    set_slot_value(record, field_tasks, "source_paragraph", anchor_para_id)
    is_meaningful = any(
        normalize_text(record.get(task["name"]))
        for task in field_tasks
        if task.get("slot") != "source_paragraph"
    )
    return {
        "matched": is_meaningful,
        "confidence": 0.62 if is_meaningful else 0.0,
        "record": record,
        "evidence_para_id": anchor_para_id,
    }


def llm_extract_one_entity(field_tasks, entity_candidate, client, model=DEFAULT_MODEL):
    if client is None:
        return fallback_extract_one_entity(field_tasks, entity_candidate)
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是实体级信息抽取助手。只输出 JSON。"},
                {"role": "user", "content": build_entity_extraction_prompt(field_tasks, entity_candidate)},
            ],
            temperature=0.1,
        )
        parsed = safe_load_json(completion.choices[0].message.content)
        if isinstance(parsed, dict):
            parsed.setdefault("matched", bool(parsed.get("record")))
            parsed.setdefault("confidence", 0.0)
            parsed.setdefault("record", {})
            parsed.setdefault("evidence_para_id", entity_candidate["anchor_para_id"])
            return parsed
    except Exception:
        pass
    return fallback_extract_one_entity(field_tasks, entity_candidate)


def normalize_record_value(field_task, value, record):
    value = normalize_text(value)
    slot = field_task.get("slot")
    name = field_task["name"]
    if slot == "source_paragraph":
        try:
            return int(value)
        except Exception:
            return record.get(name, -1)
    if field_task["type"] == "numeric" and slot in {"value", "yoy"}:
        numeric, _ = extract_first_numeric_with_unit(value or record.get(name, ""))
        return numeric or value or EMPTY_VALUE
    if slot == "unit":
        _, unit = extract_first_numeric_with_unit(value)
        return unit or value or EMPTY_VALUE
    if field_task["type"] == "date" or slot == "time":
        return extract_first_date(value) or value or EMPTY_VALUE
    return value or EMPTY_VALUE


def apply_empty_defaults(record, field_tasks):
    completed = {}
    valid_names = {task["name"] for task in field_tasks}
    meta_keys = {
        "record_id",
        "source_kind",
        "source_text",
        "source_table_id",
        "source_row",
        "source_col",
        "source_header",
        "source_locator",
        "source_context",
        "confidence",
        "extractor_type",
    }
    for name, value in record.items():
        if name.startswith("_"):
            if name == "_confidence":
                completed["confidence"] = float(value)
            continue
        if name in meta_keys:
            completed[name] = value
            continue
        if name not in valid_names:
            continue
        if next((task for task in field_tasks if task["name"] == name and task.get("slot") == "source_paragraph"), None):
            completed[name] = value if isinstance(value, int) else -1
        else:
            completed[name] = normalize_text(value) or EMPTY_VALUE
    for task in field_tasks:
        if task["name"] not in completed:
            completed[task["name"]] = -1 if task.get("slot") == "source_paragraph" else EMPTY_VALUE
    return completed


def sanitize_entity_record(field_tasks, raw_record, entity_candidate, paragraphs, llm_confidence):
    record = {task["name"]: "" for task in field_tasks}
    for task in field_tasks:
        raw_value = raw_record.get(task["name"], "") if isinstance(raw_record, dict) else ""
        record[task["name"]] = normalize_record_value(task, raw_value, record)

    evidence_para_id = get_slot_value(raw_record, field_tasks, "source_paragraph", default=entity_candidate["anchor_para_id"]) if isinstance(raw_record, dict) else entity_candidate["anchor_para_id"]
    try:
        evidence_para_id = int(evidence_para_id)
    except Exception:
        evidence_para_id = entity_candidate["anchor_para_id"]
    if evidence_para_id < 0 or evidence_para_id >= len(paragraphs):
        evidence_para_id = entity_candidate["anchor_para_id"]

    set_slot_value(record, field_tasks, "source_paragraph", evidence_para_id)
    record["source_kind"] = "paragraph"
    record["source_text"] = build_paragraph_source_text(paragraphs, evidence_para_id)
    record["record_id"] = raw_record.get("record_id") or make_record_id("text", evidence_para_id + 1)
    record["extractor_type"] = "hybrid"
    record["_confidence"] = round(
        min(
            0.25 + min(entity_candidate["score"] / 6.0, 0.45) + min(max(float(llm_confidence), 0.0), 1.0) * 0.2,
            0.99,
        ),
        4,
    )
    record["_entity_para_ids"] = [item["para_id"] for item in entity_candidate["paragraphs"]]
    return apply_empty_defaults(record, field_tasks)


def sanitize_and_merge_results(results, field_tasks):
    cleaned = []
    seen = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        item = apply_empty_defaults(item, field_tasks)
        if all(item.get(task["name"], EMPTY_VALUE) == EMPTY_VALUE for task in field_tasks if task.get("slot") != "source_paragraph"):
            continue
        dedupe_key = (
            get_slot_value(item, field_tasks, "category"),
            get_slot_value(item, field_tasks, "indicator"),
            get_slot_value(item, field_tasks, "value"),
            get_slot_value(item, field_tasks, "unit"),
            get_slot_value(item, field_tasks, "time"),
            get_slot_value(item, field_tasks, "yoy"),
            item.get("source_kind"),
            item.get("source_table_id"),
            item.get("source_row"),
            item.get("source_col"),
            get_slot_value(item, field_tasks, "source_paragraph"),
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        cleaned.append(item)
    cleaned.sort(key=lambda item: get_slot_value(item, field_tasks, "source_paragraph", default=-1))
    return cleaned


def build_fallback_record(field_tasks, para_id=-1, time_value=EMPTY_VALUE):
    record = {
        "record_id": None,
        "source_kind": "paragraph" if para_id >= 0 else "unknown",
        "source_text": None,
        "source_table_id": None,
        "source_row": None,
        "source_col": None,
        "source_header": None,
        "source_locator": None,
        "source_context": None,
        "confidence": None,
        "extractor_type": "rule",
    }
    for task in field_tasks:
        if task.get("slot") == "source_paragraph":
            record[task["name"]] = para_id
        elif task.get("slot") == "time":
            record[task["name"]] = time_value or EMPTY_VALUE
        else:
            record[task["name"]] = EMPTY_VALUE
    return record


def extract_from_text_docs(data, field_tasks, client=None, model=DEFAULT_MODEL):
    paragraphs = ensure_paragraphs(data)
    if not paragraphs:
        return []
    rule_based_records = extract_from_text_fragments(data, field_tasks)
    entity_candidates = build_entity_candidates(field_tasks, paragraphs)
    all_records = []
    for entity_candidate in entity_candidates:
        extracted = llm_extract_one_entity(field_tasks, entity_candidate, client=client, model=model)
        if not extracted.get("matched"):
            continue
        all_records.append(
            sanitize_entity_record(
                field_tasks,
                extracted.get("record", {}),
                entity_candidate,
                paragraphs,
                extracted.get("confidence", 0.0),
            )
        )
    merged_records = sanitize_and_merge_results(rule_based_records + all_records, field_tasks)
    if merged_records:
        return merged_records

    year = extract_year_from_doc(data)
    for para_id, paragraph in enumerate(paragraphs):
        value, unit, derived_indicator = find_value_and_unit(paragraph)
        yoy = find_yoy(paragraph)
        category = find_category(paragraph)
        indicator = find_indicator(paragraph) or derived_indicator
        if yoy and value and unit in {"%", "％", "个百分点"} and yoy.startswith(value):
            value, unit = "", ""
        if not any([value, yoy, indicator, category]):
            continue
        record = build_fallback_record(field_tasks, para_id=para_id, time_value=extract_first_date(paragraph) or year)
        set_slot_value(record, field_tasks, "category", category or EMPTY_VALUE)
        set_slot_value(record, field_tasks, "indicator", indicator or EMPTY_VALUE)
        set_slot_value(record, field_tasks, "value", value or EMPTY_VALUE)
        set_slot_value(record, field_tasks, "unit", unit or EMPTY_VALUE)
        set_slot_value(record, field_tasks, "yoy", yoy or EMPTY_VALUE)
        record["record_id"] = make_record_id("text", para_id + 1)
        record["source_kind"] = "paragraph"
        record["source_text"] = normalize_text(paragraph)
        record["confidence"] = 0.58
        all_records.append(record)
    return sanitize_and_merge_results(all_records, field_tasks)


def choose_primary_table(tables):
    if not tables:
        return []
    return max(tables, key=lambda table: (len(table), max((len(row) for row in table), default=0)))


def choose_primary_table_view(table_views):
    if not table_views:
        return None
    return max(
        table_views,
        key=lambda item: (
            int(item.get("row_count") or 0),
            int(item.get("column_count") or 0),
        ),
    )


def infer_table_header(table):
    for row in table:
        filled = [normalize_cell(cell) for cell in row]
        if sum(1 for cell in filled if cell) >= 2:
            return filled
    return []


def infer_category_from_row(header, row):
    for idx, cell in enumerate(row):
        if not cell or is_numeric_text(cell):
            continue
        head = normalize_text(header[idx]) if idx < len(header) else ""
        if any(token in head for token in ["分类", "类别", "地区", "行业", "名称", "项目", "指标"]):
            return cell
    for cell in row:
        if cell and not is_numeric_text(cell):
            return cell
    return ""


def infer_time_from_header_cells(header, row, fallback_time):
    for text in list(header) + list(row):
        date = extract_first_date(text)
        if date:
            return date
    return fallback_time


def should_skip_xlsx_column(column_name):
    column_name = normalize_text(column_name).lower()
    if not column_name:
        return True
    skip_tokens = ["id", "编号", "编码", "代码", "序号"]
    return any(token in column_name for token in skip_tokens)


def should_skip_xlsx_cell(cell):
    cell = normalize_text(cell)
    if not cell:
        return True
    if re.fullmatch(r"\[[^\]]+\)", cell):
        return True
    if re.fullmatch(r"[A-Za-z]\d+(?:\.\d+)?", cell):
        return True
    return False


def extract_from_xlsx(data, field_tasks):
    table_views = ensure_table_views(data)
    table_view = choose_primary_table_view(table_views)
    tables = ensure_tables(data)
    table = choose_primary_table(tables)
    if not table_view and not table:
        return [build_fallback_record(field_tasks, time_value=extract_year_from_doc(data))]
    if table_view:
        table = [[cell.get("value", "") for cell in row.get("cells", [])] for row in table_view.get("rows", [])]

    header = infer_table_header(table)
    if not header:
        return [build_fallback_record(field_tasks, time_value=extract_year_from_doc(data))]

    fallback_time = extract_year_from_doc(data)
    records = []
    header_width = len(header)
    rows = table[1:] if len(table) > 1 else []
    table_id = table_view.get("table_id") if table_view else "table_001"
    table_title = table_view.get("title") if table_view else table_id

    for row_index, raw_row in enumerate(rows[:MAX_XLSX_ROWS], start=1):
        row = [normalize_cell(cell) for cell in raw_row]
        if not any(row):
            continue
        if len(row) < header_width:
            row = row + [""] * (header_width - len(row))
        category = infer_category_from_row(header, row) or EMPTY_VALUE
        row_time = infer_time_from_header_cells(header, row, fallback_time)
        for col_index, cell in enumerate(row):
            if not cell:
                continue
            if should_skip_xlsx_cell(cell):
                continue
            value, unit = extract_first_numeric_with_unit(cell)
            if not value:
                continue
            column_name = normalize_text(header[col_index]) if col_index < len(header) else ""
            if not column_name:
                continue
            if should_skip_xlsx_column(column_name):
                continue
            if any(token in column_name for token in ["时间", "日期", "年月", "时点"]) and extract_first_date(cell):
                continue
            record = build_fallback_record(field_tasks, para_id=row_index, time_value=row_time)
            row_label = category if category != EMPTY_VALUE else ""
            record["record_id"] = make_record_id("sheet", len(records) + 1)
            record["source_kind"] = "table_cell"
            record["source_table_id"] = table_id
            record["source_row"] = row_index
            record["source_col"] = col_index
            record["source_header"] = column_name
            record["source_locator"] = make_locator(row_index, col_index)
            record["source_text"] = build_table_source_text(table_title, row_label, column_name, cell, record["source_locator"])
            record["source_context"] = build_row_context(header, row, col_index)
            record["confidence"] = 0.83
            if any(token in column_name for token in ["同比", "增长", "下降", "增速", "变化率"]):
                set_slot_value(record, field_tasks, "category", category)
                set_slot_value(record, field_tasks, "indicator", re.sub(r"(同比|增长率|增速|变化率)", "", column_name).strip() or category)
                set_slot_value(record, field_tasks, "yoy", f"{value}{unit or '%'}" if unit else value)
            else:
                set_slot_value(record, field_tasks, "category", category)
                set_slot_value(record, field_tasks, "indicator", column_name)
                set_slot_value(record, field_tasks, "value", value)
                set_slot_value(record, field_tasks, "unit", unit or EMPTY_VALUE)
            records.append(record)

    if not records:
        # 对超宽表直接返回字段命中情况，避免复杂建模。
        first_row = table[1] if len(table) > 1 else header
        record = build_fallback_record(field_tasks, para_id=1, time_value=fallback_time)
        set_slot_value(record, field_tasks, "category", infer_category_from_row(header, first_row) or normalize_text(data.get("doc_id")) or EMPTY_VALUE)
        set_slot_value(record, field_tasks, "indicator", "表格字段概览")
        set_slot_value(record, field_tasks, "value", str(len(header)))
        set_slot_value(record, field_tasks, "unit", "列")
        record["record_id"] = make_record_id("sheet", 1)
        record["source_kind"] = "table"
        record["source_table_id"] = table_id
        record["source_text"] = f"表格 {table_title} 共 {len(header)} 列"
        record["confidence"] = 0.4
        return [record]
    return sanitize_and_merge_results(records, field_tasks)


def extract(data, word_config=None, client=None, model=DEFAULT_MODEL):
    word_config = load_word_json() if word_config is None else word_config
    field_tasks = build_field_tasks(word_config)
    client = build_client() if client is None else client
    field_tasks = enrich_field_tasks_with_descriptions(data, field_tasks, client=client, model=model)
    doc_type = detect_doc_type(data)

    if doc_type == "xlsx":
        results = extract_from_xlsx(data, field_tasks)
    else:
        results = extract_from_text_docs(data, field_tasks, client=client, model=model)

    if not results:
        results = [build_fallback_record(field_tasks, time_value=extract_year_from_doc(data))]

    return {
        "doc_id": normalize_text(data.get("doc_id", "")),
        "doc_type": doc_type,
        "table_id": normalize_text(word_config.get("table_id", "")),
        "results": sanitize_and_merge_results(results, field_tasks),
    }


def main():
    data = load_data_json()
    word_config = load_word_json()
    result = extract(data, word_config=word_config)
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
