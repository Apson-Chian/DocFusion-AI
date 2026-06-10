import copy
import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from threading import local
from typing import Any

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:  # pragma: no cover
    SentenceTransformer = None

try:
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:  # pragma: no cover
    cosine_similarity = None

from ..services.llm_client import DEFAULT_MODEL, build_client, call_llm_json
from ._extract_config import infer_slot, normalize_extract_config, normalize_text as normalize_config_text


MAX_FIELD_META_DOC_CHARS = 14000
ROW_PARSE_BATCH_SIZE = 10
ROW_PARSE_BATCH_CHAR_LIMIT = 7000
MAX_XLSX_ROW_PARAGRAPHS = 120
ROW_MATCH_CONTEXT_WINDOW = 1
MAX_ROW_CANDIDATES_FOR_MATCH = 10
ROW_PARSE_WORKERS = max(1, int(os.getenv("EXTRACT_ROW_BATCH_WORKERS", "5")))
MIN_ROW_FIELD_CONFIDENCE = 0.72
MIN_ROW_MATCH_CONFIDENCE = 0.82
FIELD_MATCH_TOP_K = 3
MAX_FIELD_OPTIONS = 6
MIN_FIELD_OPTION_SCORE = 0.9
AUTO_SELECT_ROW_FIELD_SCORE = 7.2
MIN_ROW_FIELD_DECISION_CONFIDENCE = 0.64
MULTI_VALUE_OPTION_SCORE_GAP = float(os.getenv("EXTRACT_MULTI_VALUE_OPTION_SCORE_GAP", "1.1"))
MULTI_VALUE_MIN_SCORE = float(os.getenv("EXTRACT_MULTI_VALUE_MIN_SCORE", "6.6"))
FIELD_MATCH_MODEL = os.getenv("EXTRACT_FIELD_MATCH_MODEL", "shibing624/text2vec-base-chinese")
ENABLE_FIELD_MATCH_EMBEDDING = os.getenv("EXTRACT_ENABLE_FIELD_MATCH_EMBEDDING", "0") == "1"
STRICT_FIELD_MATCH_DOC_TYPES = {"xlsx"}
RELAXED_TEXT_ANCHOR_SCORE = 0.55
THREAD_LOCAL_STATE = local()
SEED_FIELD_SLOT_PRIORITY = {
    "category": 0,
    "indicator": 1,
    "time": 2,
    "value": 3,
    "unit": 4,
    "yoy": 5,
    "source_paragraph": 6,
}
NUMBER_PATTERN = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(万亿元|万亿|亿元|亿美元|亿元/年|万元|元/人\[·/]月|元/人次|元/间夜|元|亿千瓦时|亿千瓦|万台|万套|万辆|万公顷|公顷|万吨|亿吨|吨标准煤|万吨标准煤|万吨标煤|万立方米|立方米|万头|头|亿张|万张|张|万平方米|平方米|万册次|册次|亿册|册|亿人次|万人次|亿次|万次|亿人|万人|人次|万人户|万户|亿户|户|万件/套|件/套|万份|份|万名|名|万项|项|万个|个|万家|家|场|天|床日|起|例|所|次|岁|公里|千米|米|/10万|亿|万|人|%|％|个百分点|‰)?"
)
DATE_PATTERN = re.compile(r"(\d{4}\s*年\d{1,2}\s*月\d{1,2}\s*日|\d{4}\s*年\d{1,2}\s*月|\d{4}\s*年|\d{4}-\d{1,2}-\d{1,2})")
FOOTNOTE_PATTERN = re.compile(r"\[\d+\]")
YEAR_ONLY_PATTERN = re.compile(r"^\d{4}$")
YOY_TEXT_PATTERN = re.compile(r"(同比|比上年|较上年|比年初|较年初|增长|下降|提高|减少|增产|减产)")
CATEGORY_PATTERN = re.compile(
    r"(全国|全市|全省|全县|东部地区|中部地区|西部地区|东北地区|京津冀地区|长江经济带地区|长三角地区|粤港澳大湾区|城镇居民|农村居民|城市|农村|第一产业|第二产业|第三产业|夏粮|早稻|秋粮|稻谷|小麦|玉米|大豆|棉花|油料|糖料|茶叶|猪肉|牛肉|羊肉|禽肉|养殖|捕捞|国有控股企业|股份制企业|外商及港澳台投资企业|私营企业|采矿业|制造业|电力、热力、燃气及水生产和供应业|公共图书馆|群众文化机构|旅行社|星级饭店|A级景区|县以上|县及县以下)"
)
NON_TABLE_RULE_CONFIDENCE = 0.9
NON_TABLE_NOISE_TEXT_PATTERN = re.compile(r"^(?:\*+\s*)?(?:注解|单位|图\s*\d+|表\s*\d+)(?:[:：].*)?$", re.I)
INDICATOR_SUFFIXES = (
    "销售额",
    "销售总额",
    "总额",
    "金额",
    "总数",
    "数量",
    "数值",
    "比重",
    "占比",
    "增长率",
    "变化率",
    "增幅",
    "增加值",
    "产值",
    "利润",
    "费用",
    "人次",
    "人次数",
    "床位数",
    "机构总数",
    "诊疗量",
    "入院人次",
    "出院人次",
    "总产量",
    "产量",
    "收入",
    "支出",
    "规模",
)
CATEGORY_VALUE_HINT_PATTERN = re.compile(
    r"(全国|全市|全省|全县|地区|区域|行业|市场|企业|机构|医院|药店|门店|居民|人群|产业|部门|学校|人员|对象|终端|渠道|城市|农村)"
)
PLACE_NAME_SUFFIX_PATTERN = r"(?:特别行政区|自治州|高新区|开发区|市区|新区|地区|省|市|县|区|镇|乡|街道|村)"
PLACE_NAME_PATTERN = rf"[\u4e00-\u9fa5A-Za-z0-9·]{{2,16}}{PLACE_NAME_SUFFIX_PATTERN}"
PLACE_LIST_PATTERN = re.compile(rf"(?P<places>{PLACE_NAME_PATTERN}(?:、{PLACE_NAME_PATTERN}){{0,7}})")
CONTINUATION_PARAGRAPH_START_PATTERN = re.compile(
    r"^(?:分别为|分别达到|分别占|分别下降|分别上升|分别增长|为|达|占|同比|较上年|比上年|其中|其优良率|优良率|达标率|占比|比重)"
)
CONTINUATION_PARAGRAPH_END_PATTERN = re.compile(
    r"(?:优良率|达标率|占比|比重|比例|比率|同比|增长率|下降率|变化率|分别|分别为|为)$"
)
PARALLEL_LABEL_VALUES_PATTERN = re.compile(
    r"(?P<label>[\u4e00-\u9fa5A-Za-z0-9·（）()/%\.\-]{1,40}?)(?:分别|依次)?为(?P<values>[^，,。；;]+(?:、[^，,。；;]+)+)"
)


def normalize_text(text: Any) -> str:
    if text is None:
        return ""
    text = str(text).replace("\u3000", " ")
    text = text.replace("\r", "\n")
    text = FOOTNOTE_PATTERN.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = text.replace("：", ":")
    return text.strip()


def normalize_for_match(text: Any) -> str:
    text = normalize_text(text).lower()
    return re.sub(r"[\s，,。；;！!？?\-_/（）()\[\]【】\"'`~·:：|]+", "", text)


def is_strict_table_doc(doc_type: Any) -> bool:
    return normalize_text(doc_type).lower() in STRICT_FIELD_MATCH_DOC_TYPES


def soft_text_similarity(left: Any, right: Any) -> float:
    left_norm = normalize_for_match(left)
    right_norm = normalize_for_match(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    overlap = len(set(left_norm) & set(right_norm)) / max(len(set(left_norm) | set(right_norm)), 1)
    sequence = SequenceMatcher(a=left_norm, b=right_norm).ratio()
    if left_norm in right_norm or right_norm in left_norm:
        return min(1.0, max(sequence, overlap, 0.9))
    return min(1.0, 0.58 * sequence + 0.42 * overlap)


def safe_key(text: str, used: set[str]) -> str:
    text = normalize_text(text).lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = f"field_{len(used) + 1}"
    base = text
    suffix = 2
    while text in used:
        text = f"{base}_{suffix}"
        suffix += 1
    used.add(text)
    return text


def make_row_id() -> str:
    return uuid.uuid4().hex[:12]


def unique_strings(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        item = normalize_text(value)
        signature = normalize_for_match(item)
        if not item or not signature or signature in seen:
            continue
        seen.add(signature)
        result.append(item)
    return result


SLOT_DEFAULT_ALIASES = {
    "category": ["分类", "对象", "范围", "主体", "地区", "市场", "企业", "机构", "行业", "药店"],
    "indicator": ["指标", "项目", "名称", "规模", "总数", "销售额", "销售总额", "收入", "总量", "数量"],
    "value": ["数值", "数量", "金额", "总额"],
    "unit": ["单位", "口径", "量纲"],
    "time": ["时间", "时点", "年度", "年末", "年底"],
    "yoy": ["同比", "增速", "增长率", "变化率", "变动率", "涨跌幅"],
}
RATIO_VALUE_ALIASES = ["比例", "比率", "百分比", "占比", "比重", "率", "达标率", "优良率"]
COUNT_VALUE_ALIASES = ["数值", "数量", "总数", "个数", "天数", "次数", "家数", "人数", "床位数"]
AMOUNT_VALUE_ALIASES = ["数值", "金额", "总额", "规模", "销售额", "收入", "产值"]
NON_DISTINCT_FIELD_TOPICS = {
    "经营",
    "统计",
    "规模",
    "时间",
    "范围",
    "数量",
    "金额",
    "数值",
    "增幅",
    "增减",
    "表现",
    "情况",
    "水平",
}


def normalize_llm_field_meta_payload(parsed: Any) -> dict[str, Any]:
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"fields": [item for item in parsed if isinstance(item, dict)]}
    return {}


def normalize_llm_row_payload(parsed: Any) -> dict[str, Any]:
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        return {"items": [item for item in parsed if isinstance(item, dict)]}
    return {}


def alias_has_literal_value(alias: str) -> bool:
    normalized = normalize_text(alias)
    if not normalized:
        return False
    if DATE_PATTERN.fullmatch(normalized):
        return True
    parsed = parse_number_unit(normalized)
    if parsed.get("number"):
        return True
    if re.search(r"\d", normalized):
        return True
    return False


def is_semantic_alias(alias: str, slot: str, field_name: str) -> bool:
    normalized = normalize_text(alias)
    if not normalized:
        return False
    if normalize_for_match(normalized) == normalize_for_match(field_name):
        return False
    if len(normalized) <= 1 and slot != "unit":
        return False
    if alias_has_literal_value(normalized):
        return False
    if slot == "time" and normalized.endswith("年") and re.fullmatch(r"\d{4}年", normalized):
        return False
    return True


def is_ratio_like_alias(alias: Any) -> bool:
    normalized = normalize_text(alias)
    return any(token in normalized for token in ["比例", "比率", "百分比", "占比", "比重", "率", "百分点"])


def is_ratio_like_field_name(field_name: Any) -> bool:
    normalized = normalize_text(field_name)
    return any(token in normalized for token in ["比例", "比率", "百分比", "占比", "比重", "率", "达标率", "优良率"])


def is_amount_like_field_name(field_name: Any) -> bool:
    normalized = normalize_text(field_name)
    return any(token in normalized for token in ["金额", "总额", "规模", "销售额", "收入", "产值", "费用", "支出"])


def extract_distinct_field_topic(field_name: Any) -> str:
    normalized = normalize_text(field_name)
    for suffix in ["天数", "比例", "比率", "百分比", "率", "数值", "数量", "金额", "总额", "规模"]:
        if normalized.endswith(suffix):
            topic = normalize_text(normalized[:-len(suffix)])
            if len(topic) >= 2 and topic not in NON_DISTINCT_FIELD_TOPICS:
                return topic
    return ""


def get_relaxed_slot_aliases(slot: str, field_name: str) -> list[str]:
    if slot != "value":
        return SLOT_DEFAULT_ALIASES.get(slot, [])
    if is_ratio_like_field_name(field_name):
        if "优良" in normalize_text(field_name):
            return ["优良率", "优良比例", "优良比率", "空气质量优良率"]
        if "达标" in normalize_text(field_name):
            return ["达标率", "达标比例", "达标比率"]
        return RATIO_VALUE_ALIASES
    if "优良" in normalize_text(field_name) and "天数" in normalize_text(field_name):
        return ["优良天数", "优良天", "优良日数"]
    if "达标" in normalize_text(field_name) and "天数" in normalize_text(field_name):
        return ["达标天数", "达标天", "达标日数"]
    if is_amount_like_field_name(field_name):
        return AMOUNT_VALUE_ALIASES
    return COUNT_VALUE_ALIASES


def get_structured_field_aliases(field_name: str) -> list[str]:
    normalized = normalize_text(field_name)
    aliases = []
    if any(token in normalized for token in ["编号", "编码", "单号", "ID", "id"]):
        aliases.extend(["id", "code", "编号", "编码"])
        if "订单" in normalized:
            aliases.extend(["order_id", "订单号", "单号"])
    if "状态" in normalized:
        aliases.extend(["status"])
        if "支付" in normalized:
            aliases.extend(["pay_status", "order_status", "支付状态"])
    if any(token in normalized for token in ["数量", "件数", "个数"]):
        aliases.extend(["qty", "num", "count"])
    if "折扣" in normalized:
        aliases.extend(["discount", "spe_discount"])
    return aliases


def sanitize_aliases(field_name: str, slot: str, aliases: list[str], *, relaxed_semantic_match: bool = False) -> list[str]:
    filtered = [
        alias
        for alias in aliases
        if is_semantic_alias(alias, slot, field_name)
        and (slot not in {"indicator", "category"} or is_plausible_slot_value({"slot": slot}, alias))
    ]
    filtered.extend(get_structured_field_aliases(field_name))
    if slot == "value":
        if is_ratio_like_field_name(field_name):
            filtered = [alias for alias in filtered if is_ratio_like_alias(alias)]
        else:
            filtered = [alias for alias in filtered if not is_ratio_like_alias(alias)]
    if relaxed_semantic_match:
        filtered.extend(get_relaxed_slot_aliases(slot, field_name))
    return unique_strings(filtered)


def looks_like_numeric_literal(text: Any) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    parsed = parse_number_unit(normalized)
    if not parsed.get("number"):
        return False
    normalized_value = parsed.get("normalized") or parsed.get("number")
    return bool(normalized_value and normalize_for_match(normalized_value) == normalize_for_match(normalized))


def looks_like_indicator_value(text: Any) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    return normalized.endswith(INDICATOR_SUFFIXES) or any(keyword in normalized for keyword in ["同比", "增速", "增长率", "变化率", "占比", "比重"])


def looks_like_category_value(text: Any) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    return CATEGORY_VALUE_HINT_PATTERN.search(normalized) is not None


def field_expects_numeric_like_value(field_task: dict[str, Any]) -> bool:
    field_name = normalize_text(field_task.get("field_name"))
    description = normalize_text(field_task.get("description"))
    signature = f"{field_name} {description}".strip()
    if any(token in description for token in ["指标名称", "字段名称", "名称", "项目名", "标签名"]):
        return False
    if any(token in field_name for token in ["指标", "名称", "项目"]) and not any(
        metric_token in field_name
        for metric_token in ["值", "数值", "指标值", "金额", "数量", "规模", "天数", "次数", "浓度", "费用", "支出"]
    ):
        return False
    return any(
        keyword in signature
        for keyword in [
            "规模",
            "金额",
            "数量",
            "天数",
            "次数",
            "床位",
            "浓度",
            "费用",
            "支出",
            "人数",
            "人次",
            "总额",
            "销售额",
            "收入",
            "产值",
            "数值",
            "指标值",
            "体量",
        ]
    )


def looks_like_year_code(text: Any) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    parsed = parse_number_unit(normalized)
    unit = normalize_text(parsed.get("unit"))
    if unit and unit not in {"年"}:
        return False
    number = normalize_text(parsed.get("number") or normalized).lstrip("-")
    if not YEAR_ONLY_PATTERN.fullmatch(number):
        return False
    try:
        year_value = int(number)
    except Exception:
        return False
    return 1900 <= year_value <= 2100


def is_ratio_literal(text: Any) -> bool:
    parsed = parse_number_unit(normalize_text(text))
    unit = normalize_text(parsed.get("unit"))
    if unit in {"%", "％", "百分点", "‰"}:
        return True
    normalized = normalize_text(parsed.get("normalized") or parsed.get("raw"))
    return any(token in normalized for token in ["%", "％", "百分点", "‰"])


def field_prefers_ratio_literal(field_task: dict[str, Any]) -> bool:
    signature = normalize_text(field_task.get("field_name")) + " " + normalize_text(field_task.get("description"))
    return any(
        keyword in signature
        for keyword in ["占比", "比例", "百分比", "比重", "率", "增速", "增长率", "变化率", "优良率", "达标率", "百分点"]
    )


def field_prefers_count_unit(field_task: dict[str, Any]) -> bool:
    signature = normalize_text(field_task.get("field_name")) + " " + normalize_text(field_task.get("description"))
    return any(
        keyword in signature
        for keyword in ["天数", "次数", "家数", "人数", "床位数", "个数", "总数", "数量", "户数", "店数", "机构数"]
    )


def has_yoy_signal(*texts: Any) -> bool:
    return any(YOY_TEXT_PATTERN.search(normalize_text(text)) for text in texts if normalize_text(text))


def is_plausible_slot_value(field_task: dict[str, Any], raw_value: Any) -> bool:
    normalized = normalize_text(raw_value)
    if not normalized:
        return False

    slot = normalize_text(field_task.get("slot"))
    if slot != "time" and looks_like_year_code(normalized):
        return False
    if slot == "category" and (DATE_PATTERN.fullmatch(normalized) or looks_like_numeric_literal(normalized)):
        return False
    if slot == "indicator" and (DATE_PATTERN.fullmatch(normalized) or looks_like_numeric_literal(normalized)):
        if not field_expects_numeric_like_value(field_task):
            return False
        if is_ratio_literal(normalized) and not field_prefers_ratio_literal(field_task):
            return False
    if slot == "indicator" and looks_like_category_value(normalized) and not looks_like_indicator_value(normalized):
        return False
    if slot == "category":
        if normalized.endswith(INDICATOR_SUFFIXES):
            return False
        if looks_like_indicator_value(normalized) and not looks_like_category_value(normalized):
            return False
    return True


def field_prefers_metric_literal(field_task: dict[str, Any]) -> bool:
    slot = normalize_text(field_task.get("slot"))
    return slot in {"value", "yoy"} or field_expects_numeric_like_value(field_task)


def is_preferred_seed_value(field_task: dict[str, Any], value: Any) -> bool:
    normalized = normalize_text(value)
    if not normalized:
        return False
    slot = normalize_text(field_task.get("slot"))
    if slot == "time":
        return bool(DATE_PATTERN.search(normalized))
    if field_prefers_metric_literal(field_task):
        if is_ratio_literal(normalized) and not field_prefers_ratio_literal(field_task):
            return False
        return looks_like_numeric_literal(normalized)
    return True


def score_seed_cell(field_task: dict[str, Any], cell: dict[str, Any]) -> float:
    value = normalize_text(cell.get("value"))
    source = cell.get("source") or {}
    score = float(source.get("confidence") or 0.0)
    if field_prefers_metric_literal(field_task):
        score += 4.0 if looks_like_numeric_literal(value) else -3.0
        if is_ratio_literal(value) and not field_prefers_ratio_literal(field_task):
            score -= 3.4
    elif not looks_like_numeric_literal(value):
        score += 1.0
    if normalize_text(cell.get("number")):
        score += 0.9
    if normalize_text(cell.get("unit")):
        score += 0.35
    return round(score, 4)


def field_allows_multi_value_clone(field_task: dict[str, Any]) -> bool:
    slot = normalize_text(field_task.get("slot"))
    return slot not in {"category", "time", "source_paragraph"}


class FieldAssistMatcher:
    def __init__(
        self,
        field_tasks: list[dict[str, Any]],
        model_name: str = FIELD_MATCH_MODEL,
    ) -> None:
        self.field_tasks = field_tasks
        self.model_name = model_name
        self.field_map = {field["name"]: field for field in field_tasks}
        self.field_keys = [field["name"] for field in field_tasks]
        self.field_terms = {
            field["name"]: unique_strings([field["field_name"], *field.get("aliases", [])]) or [field["field_name"]]
            for field in field_tasks
        }
        self.field_descriptions = {
            field["name"]: normalize_text(field.get("description"))
            for field in field_tasks
        }
        self.field_text_map = {
            field["name"]: (
                "；".join(
                    unique_strings(
                        [
                            field["field_name"],
                            *field.get("aliases", []),
                            normalize_text(field.get("description")),
                        ]
                    )
                )
                or field["field_name"]
            )
            for field in field_tasks
        }
        self.field_texts = [self.field_text_map[field_key] for field_key in self.field_keys]
        self.model = None
        self.field_embeddings = None
        self.vector_enabled = False

        should_try_vector = ENABLE_FIELD_MATCH_EMBEDDING or os.path.isdir(model_name)
        if should_try_vector and SentenceTransformer is not None and np is not None and cosine_similarity is not None:
            try:
                self.model = SentenceTransformer(model_name)
                self.field_embeddings = self.model.encode(self.field_texts)
                self.vector_enabled = True
            except Exception:
                self.model = None
                self.field_embeddings = None
                self.vector_enabled = False

    def _fallback_similarity(self, left: str, right: str) -> float:
        return soft_text_similarity(left, right)

    def _fallback_field_score(self, query: str, field_key: str) -> float:
        scores = []
        for term in self.field_terms.get(field_key, []):
            scores.append(self._fallback_similarity(query, term))
        description = self.field_descriptions.get(field_key)
        if description:
            scores.append(self._fallback_similarity(query, description) * 0.72)
        joined_text = self.field_text_map.get(field_key)
        if joined_text:
            scores.append(self._fallback_similarity(query, joined_text) * 0.88)
        return max(scores or [0.0])

    def _score_field(self, query: str, field_key: str) -> float:
        lexical_score = self._fallback_field_score(query, field_key)
        if self.vector_enabled and self.model is not None and self.field_embeddings is not None and np is not None and cosine_similarity is not None:
            try:
                embedding = self.model.encode([query])[0]
                similarities = cosine_similarity(np.asarray(embedding).reshape(1, -1), self.field_embeddings)[0]
                index = self.field_keys.index(field_key)
                return max(float(similarities[index]), lexical_score)
            except Exception:
                pass
        return lexical_score

    def top_matches(self, phrase: str, top_k: int = FIELD_MATCH_TOP_K) -> list[dict[str, Any]]:
        phrase = normalize_text(phrase)
        if not phrase:
            return []

        if self.vector_enabled and self.model is not None and self.field_embeddings is not None and np is not None and cosine_similarity is not None:
            try:
                embedding = self.model.encode([phrase])[0]
                similarities = cosine_similarity(np.asarray(embedding).reshape(1, -1), self.field_embeddings)[0]
                ranked = sorted(
                    [
                        {
                            "field_key": self.field_keys[index],
                            "field_name": self.field_map[self.field_keys[index]]["field_name"],
                            "score": round(max(float(score), self._fallback_field_score(phrase, self.field_keys[index])), 4),
                        }
                        for index, score in enumerate(similarities)
                    ],
                    key=lambda item: (-item["score"], item["field_name"]),
                )
                return ranked[:top_k]
            except Exception:
                pass

        ranked = []
        for field_key in self.field_keys:
            ranked.append(
                {
                    "field_key": field_key,
                    "field_name": self.field_map[field_key]["field_name"],
                    "score": round(self._fallback_field_score(phrase, field_key), 4),
                }
            )
        ranked.sort(key=lambda item: (-item["score"], item["field_name"]))
        return ranked[:top_k]

    def summarize(self, phrase: str, target_field_key: str) -> dict[str, Any]:
        phrase = normalize_text(phrase)
        target_field = self.field_map.get(target_field_key)
        if target_field is None:
            return {
                "phrase": phrase,
                "target_field": None,
                "target_score": 0.0,
                "is_target_top": False,
                "top_matches": [],
            }

        top_matches = self.top_matches(phrase, top_k=FIELD_MATCH_TOP_K)
        target_score = 0.0
        for item in top_matches:
            if item["field_key"] == target_field_key:
                target_score = float(item["score"])
                break
        if target_score <= 0:
            target_score = round(self._score_field(phrase, target_field_key), 4)
            top_matches = sorted(
                [*top_matches, {"field_key": target_field_key, "field_name": target_field["field_name"], "score": target_score}],
                key=lambda item: (-float(item["score"]), item["field_name"]),
            )[:FIELD_MATCH_TOP_K]

        return {
            "phrase": phrase,
            "target_field": target_field["field_name"],
            "target_score": round(float(target_score), 4),
            "is_target_top": bool(top_matches and top_matches[0]["field_key"] == target_field_key),
            "top_matches": top_matches,
        }


def build_row_paragraph_text(header: list[str], row_values: list[str]) -> str:
    labeled = []
    has_header = any(normalize_text(item) for item in header)
    for index, value in enumerate(row_values):
        cell_value = normalize_text(value)
        if not cell_value:
            continue
        if has_header and index < len(header) and normalize_text(header[index]):
            labeled.append(f"{normalize_text(header[index])}: {cell_value}")
        else:
            labeled.append(cell_value)
    return " | ".join(labeled)


def limit_table_rows_for_extraction(rows: list[dict[str, Any]], max_rows: int = MAX_XLSX_ROW_PARAGRAPHS) -> list[dict[str, Any]]:
    if len(rows) <= max_rows:
        return rows
    if max_rows <= 0:
        return []

    # Keep a stable preview of the head rows and evenly sample the rest so
    # very large spreadsheets do not explode into thousands of paragraph batches.
    head_count = min(40, max_rows)
    if head_count == max_rows:
        return rows[:max_rows]

    remaining = max_rows - head_count
    start = head_count
    span = len(rows) - start
    if span <= 0 or remaining <= 0:
        return rows[:max_rows]

    step = span / remaining
    sampled = rows[:head_count]
    used_indexes = set(range(head_count))
    for offset in range(remaining):
        index = start + int(offset * step)
        index = min(max(index, start), len(rows) - 1)
        while index in used_indexes and index + 1 < len(rows):
            index += 1
        if index in used_indexes:
            continue
        used_indexes.add(index)
        sampled.append(rows[index])

    return sampled[:max_rows]


def ensure_paragraphs(data: dict) -> list[dict[str, Any]]:
    paragraphs = []
    doc_type = normalize_text(data.get("doc_type")).lower()

    paragraph_items = data.get("paragraph_items") or []
    if isinstance(paragraph_items, list) and paragraph_items:
        for item in paragraph_items:
            if not isinstance(item, dict):
                continue
            text = normalize_text(item.get("text"))
            if not text:
                continue
            paragraphs.append(
                {
                    "paragraph_id": len(paragraphs),
                    "text": text,
                    "origin": item.get("origin") or "paragraph_items",
                    "source_kind": "paragraph",
                    "source_table_id": None,
                    "source_row": None,
                    "source_col": None,
                    "source_header": None,
                    "source_locator": None,
                }
            )
    else:
        raw_paragraphs = data.get("paragraphs") or []
        if isinstance(raw_paragraphs, list) and raw_paragraphs:
            for index, item in enumerate(raw_paragraphs):
                text = normalize_text(item)
                if not text:
                    continue
                paragraphs.append(
                    {
                        "paragraph_id": len(paragraphs),
                        "text": text,
                        "origin": f"paragraphs[{index}]",
                        "source_kind": "paragraph",
                        "source_table_id": None,
                        "source_row": None,
                        "source_col": None,
                        "source_header": None,
                        "source_locator": None,
                    }
                )
        else:
            raw_text = normalize_text(data.get("raw_text"))
            if raw_text:
                for index, chunk in enumerate(re.split(r"\n\s*\n+|\n+", raw_text)):
                    text = normalize_text(chunk)
                    if not text:
                        continue
                    paragraphs.append(
                        {
                            "paragraph_id": len(paragraphs),
                            "text": text,
                            "origin": f"raw_text[{index}]",
                            "source_kind": "paragraph",
                            "source_table_id": None,
                            "source_row": None,
                            "source_col": None,
                            "source_header": None,
                            "source_locator": None,
                        }
                    )

    table_views = data.get("table_views") or []
    if isinstance(table_views, list):
        for table in table_views:
            if not isinstance(table, dict):
                continue
            header = [normalize_text(item) for item in (table.get("header") or [])]
            rows = table.get("rows") or []
            if doc_type == "xlsx" and isinstance(rows, list):
                rows = limit_table_rows_for_extraction(rows)
            for row in rows:
                if not isinstance(row, dict):
                    continue
                row_index = row.get("row_index")
                if row_index == 0:
                    continue
                cells = row.get("cells") or []
                if not isinstance(cells, list):
                    continue
                row_values = []
                for cell in cells:
                    if not isinstance(cell, dict):
                        continue
                    row_values.append(normalize_text(cell.get("value")))
                text = build_row_paragraph_text(header, row_values)
                if not text:
                    continue
                paragraphs.append(
                    {
                        "paragraph_id": len(paragraphs),
                        "text": text,
                        "origin": f"{table.get('table_id') or 'table'}:row:{row_index}",
                        "source_kind": "table_row",
                        "source_table_id": table.get("table_id"),
                        "source_row": row_index,
                        "source_col": None,
                        "source_header": None,
                        "source_locator": None,
                    }
                )
    return paragraphs


def should_merge_continuation_paragraph(current_text: Any, next_text: Any) -> bool:
    left = normalize_text(current_text)
    right = normalize_text(next_text)
    if not left or not right:
        return False
    if len(right) > 120 and CONTINUATION_PARAGRAPH_START_PATTERN.search(right) is None:
        return False
    if CONTINUATION_PARAGRAPH_START_PATTERN.search(right):
        return True
    if CONTINUATION_PARAGRAPH_END_PATTERN.search(left):
        return True
    return False


def merge_non_table_continuation_paragraphs(paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not paragraphs:
        return []

    merged = []
    index = 0
    while index < len(paragraphs):
        paragraph = copy.deepcopy(paragraphs[index])
        if paragraph.get("source_kind") != "paragraph":
            merged.append(paragraph)
            index += 1
            continue

        combined_text = normalize_text(paragraph.get("text"))
        next_index = index + 1
        while next_index < len(paragraphs):
            next_paragraph = paragraphs[next_index]
            if next_paragraph.get("source_kind") != "paragraph":
                break
            next_text = normalize_text(next_paragraph.get("text"))
            if not should_merge_continuation_paragraph(combined_text, next_text):
                break
            combined_text = f"{combined_text}{next_text}"
            next_index += 1

        paragraph["text"] = combined_text
        merged.append(paragraph)
        index = next_index
    return merged


def iter_paragraph_batches(
    paragraphs: list[dict[str, Any]],
    batch_size: int = ROW_PARSE_BATCH_SIZE,
    char_limit: int = ROW_PARSE_BATCH_CHAR_LIMIT,
) -> list[list[dict[str, Any]]]:
    batches = []
    current = []
    char_count = 0
    for paragraph in paragraphs:
        text_length = len(paragraph["text"])
        if current and (len(current) >= batch_size or char_count + text_length > char_limit):
            batches.append(current)
            current = []
            char_count = 0
        current.append(paragraph)
        char_count += text_length
    if current:
        batches.append(current)
    return batches


def collect_context_window(paragraphs: list[dict[str, Any]], paragraph_id: int, window: int = 1) -> str:
    id_to_index = {paragraph["paragraph_id"]: index for index, paragraph in enumerate(paragraphs)}
    if paragraph_id not in id_to_index:
        return ""
    index = id_to_index[paragraph_id]
    left = max(0, index - window)
    right = min(len(paragraphs), index + window + 1)
    parts = []
    for paragraph in paragraphs[left:right]:
        parts.append(f"[paragraph_id={paragraph['paragraph_id']}] {paragraph['text']}")
    return "\n\n".join(parts)


def extract_frontend_fields(frontend_form: dict | None) -> list[dict[str, Any]]:
    config = normalize_extract_config(frontend_form)
    if config["fields"]:
        return [
            {
                "column_index": index,
                "field_name": item["field_name"],
                "slot": item["slot"],
                "aliases": unique_strings(item.get("aliases", [])),
                "description": normalize_text(item.get("description")),
                "visible": bool(item.get("visible", True)),
            }
            for index, item in enumerate(config["fields"])
        ]
    return []


def build_field_meta_prompt(
    raw_text: str,
    fields: list[dict[str, Any]],
    relaxed_semantic_match: bool = False,
) -> str:
    requirements = [
        "字段只能来自 fields，不能新增、不能改名。",
        "key 仅作为内部键名，使用简短 snake_case。",
        "description 只描述该列真正想抽取的值，不要描述整行、整段或整张表。",
        "aliases 只保留与该列同义、且能直接帮助命中原文的表达。",
        "aliases 不能填写具体数值、日期、百分比、示例值或整句原文。",
        "不要输出任何默认值、示例值、占位值。",
        "只输出 JSON。",
    ]
    if relaxed_semantic_match:
        requirements.insert(4, "对于文本类文档，aliases 尽量覆盖原文常见近义表达、缩略表达和口语化表达，不要求与列名完全同字。")

    payload = {
        "task": "只根据用户给定的列名，补充后续抽取和归并所需的最小字段语义信息。",
        "requirements": requirements,
        "fields": [
            {
                "label": field["field_name"],
                "slot": field["slot"],
                "description": field.get("description", ""),
                "aliases": field.get("aliases", []),
            }
            for field in fields
        ],
        "document": normalize_text(raw_text)[:MAX_FIELD_META_DOC_CHARS],
        "output_schema": {
            "fields": [
                {
                    "label": "原列名",
                    "key": "internal_snake_case",
                    "description": "列语义",
                    "aliases": ["同义表达1", "同义表达2"],
                }
            ]
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_field_tasks_from_frontend(
    frontend_form: dict | None,
    raw_text: str,
    client=None,
    model: str = DEFAULT_MODEL,
    relaxed_semantic_match: bool = False,
) -> list[dict[str, Any]]:
    frontend_fields = extract_frontend_fields(frontend_form)
    if not frontend_fields:
        raise ValueError("前端未传入有效表头")

    parsed = normalize_llm_field_meta_payload(
        call_llm_json(
        client=client,
        user_content=build_field_meta_prompt(raw_text, frontend_fields, relaxed_semantic_match=relaxed_semantic_match),
        system_content="你是字段初始化助手。你的输出将直接驱动后续的行级识别与主键归并，只输出 JSON。",
        model=model,
        temperature=0.0,
        )
    )

    llm_fields = {}
    for item in parsed.get("fields", []) or []:
        if not isinstance(item, dict):
            continue
        label = normalize_text(item.get("label"))
        if label:
            llm_fields[label] = item

    used = set()
    field_tasks = []
    for field in frontend_fields:
        label = field["field_name"]
        llm_item = llm_fields.get(label, {})
        field_tasks.append(
            {
                "column_index": field["column_index"],
                "field_name": label,
                "name": safe_key(llm_item.get("key") or label, used),
                "slot": field.get("slot") or infer_slot(label),
                "aliases": sanitize_aliases(
                    label,
                    field.get("slot") or infer_slot(label),
                    [*field.get("aliases", []), *(llm_item.get("aliases", []) or [])],
                    relaxed_semantic_match=relaxed_semantic_match,
                ),
                "description": normalize_text(llm_item.get("description") or field.get("description")),
                "visible": bool(field.get("visible", True)),
            }
        )
    return field_tasks


def choose_primary_table_view(data: dict[str, Any]) -> dict[str, Any] | None:
    table_views = data.get("table_views") or []
    candidates = [item for item in table_views if isinstance(item, dict)]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            int(item.get("row_count") or 0),
            int(item.get("column_count") or 0),
        ),
    )


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def build_table_header_candidates(table_view: dict[str, Any]) -> list[dict[str, Any]]:
    header = [normalize_text(item) for item in (table_view.get("header") or [])]
    rows = table_view.get("rows") or []
    first_data_row = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if safe_int(row.get("row_index"), 0) <= 0:
            continue
        first_data_row = row
        break

    candidates = []
    for col_index, header_name in enumerate(header):
        if not header_name:
            continue
        sample_value = ""
        locator = None
        if isinstance(first_data_row, dict):
            for cell in first_data_row.get("cells") or []:
                if not isinstance(cell, dict):
                    continue
                if safe_int(cell.get("col_index"), -1) != col_index:
                    continue
                sample_value = normalize_text(cell.get("value"))
                locator = normalize_text(cell.get("locator"))
                break
        candidates.append(
            {
                "header": header_name,
                "col_index": col_index,
                "sample_value": sample_value,
                "locator": locator,
            }
        )
    return candidates


def build_xlsx_header_mapping_prompt(field_tasks: list[dict[str, Any]], header_candidates: list[dict[str, Any]]) -> str:
    payload = {
        "task": "将用户希望抽取的字段，映射到表格中的原始表头。只选择最匹配的原始表头；如果没有合适表头，返回空字符串。",
        "requirements": [
            "只能从 source_headers 中选择 header，不能自造表头。",
            "优先理解语义相近关系，而不是只看字面完全一致。",
            "同一个用户字段最多映射一个原始表头。",
            "如果用户字段是数值列，应优先选择真正承载数值的原始表头。",
            "只输出 JSON。",
        ],
        "target_fields": [
            {
                "field_name": field["field_name"],
                "slot": field.get("slot"),
                "aliases": field.get("aliases", []),
                "description": field.get("description", ""),
            }
            for field in field_tasks
        ],
        "source_headers": [
            {
                "header": item["header"],
                "sample_value": item.get("sample_value", ""),
                "col_index": item["col_index"],
            }
            for item in header_candidates
        ],
        "output_schema": {
            "mappings": [
                {
                    "field_name": "用户字段名",
                    "source_header": "匹配到的原始表头，未匹配则为空字符串",
                    "reason": "简短原因",
                    "confidence": 0.0,
                }
            ]
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def fallback_match_field_to_header(field_task: dict[str, Any], header_candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    best = None
    best_score = 0.0
    terms = unique_strings([field_task["field_name"], *field_task.get("aliases", []), field_task.get("description", "")])
    slot = normalize_text(field_task.get("slot"))
    for item in header_candidates:
        header = item["header"]
        sample_value = normalize_text(item.get("sample_value"))
        score = 0.0
        for term in terms:
            score = max(score, soft_text_similarity(term, header))
        if slot == "value" and sample_value:
            parsed = parse_number_unit(sample_value)
            if parsed.get("number"):
                score += 0.16
        if slot == "category" and header in {"地区", "区域", "城市", "省份", "市区", "行业", "类别"}:
            score += 0.22
        if slot == "indicator" and any(token in header for token in ["名称", "职位", "单位", "部门", "项目", "指标", "司局"]):
            score += 0.18
        if slot == "value" and any(token in header for token in ["人数", "数量", "金额", "数值", "总额", "比例", "比率", "同比"]):
            score += 0.18
        if score > best_score:
            best_score = score
            best = item
    if best is None or best_score < 0.45:
        return None
    return {"source_header": best["header"], "confidence": round(best_score, 4), "reason": "fallback lexical match"}


def resolve_xlsx_header_mappings(
    field_tasks: list[dict[str, Any]],
    header_candidates: list[dict[str, Any]],
    client=None,
    model: str = DEFAULT_MODEL,
) -> dict[str, dict[str, Any]]:
    mapping_by_field: dict[str, dict[str, Any]] = {}
    if client is not None and header_candidates:
        parsed = normalize_llm_field_meta_payload(
            call_llm_json(
                client=client,
                user_content=build_xlsx_header_mapping_prompt(field_tasks, header_candidates),
                system_content="你是表格表头对齐助手。你只负责把用户字段映射到原始表头，只输出 JSON。",
                model=model,
                temperature=0.0,
            )
        )
        for item in parsed.get("mappings", []) or []:
            if not isinstance(item, dict):
                continue
            field_name = normalize_text(item.get("field_name"))
            source_header = normalize_text(item.get("source_header"))
            if not field_name or not source_header:
                continue
            mapping_by_field[field_name] = {
                "source_header": source_header,
                "reason": normalize_text(item.get("reason")),
                "confidence": float(item.get("confidence") or 0.0),
            }

    used_headers = set()
    resolved = {}
    for field in field_tasks:
        field_name = field["field_name"]
        mapped = mapping_by_field.get(field_name)
        if mapped and mapped.get("source_header") and mapped["source_header"] not in used_headers:
            resolved[field_name] = mapped
            used_headers.add(mapped["source_header"])
            continue
        fallback = fallback_match_field_to_header(field, header_candidates)
        if fallback and fallback.get("source_header") not in used_headers:
            resolved[field_name] = fallback
            used_headers.add(fallback["source_header"])
        else:
            resolved[field_name] = {"source_header": "", "reason": "no suitable header", "confidence": 0.0}
    return resolved


def build_table_row_source_text(table_title: str, header_candidates: list[dict[str, Any]], row: dict[str, Any]) -> str:
    values_by_col = {}
    for cell in row.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        values_by_col[safe_int(cell.get("col_index"), -1)] = normalize_text(cell.get("value"))
    pairs = []
    for item in header_candidates:
        header = item["header"]
        value = values_by_col.get(item["col_index"], "")
        if value:
            pairs.append(f"{header}: {value}")
    joined = " | ".join(pairs)
    return joined or normalize_text(table_title)


def extract_from_xlsx_by_header_mapping(
    data: dict[str, Any],
    field_tasks: list[dict[str, Any]],
    client=None,
    model: str = DEFAULT_MODEL,
    progress_callback=None,
) -> list[dict[str, Any]]:
    table_view = choose_primary_table_view(data)
    if not isinstance(table_view, dict):
        return []

    header_candidates = build_table_header_candidates(table_view)
    if not header_candidates:
        return []

    if progress_callback is not None:
        progress_callback(percent=46, message="正在对齐用户表头与原始表头")
    header_mappings = resolve_xlsx_header_mappings(field_tasks, header_candidates, client=client, model=model)
    header_by_name = {item["header"]: item for item in header_candidates}

    rows = []
    table_title = normalize_text(table_view.get("title") or table_view.get("table_id"))
    row_items = [row for row in (table_view.get("rows") or []) if isinstance(row, dict) and int(row.get("row_index") or 0) > 0]
    total_rows = max(1, len(row_items))
    for index, row in enumerate(row_items):
        if progress_callback is not None and index % 25 == 0:
            progress_callback(
                percent=52 + min(34, (index / total_rows) * 34),
                current=index,
                total=total_rows,
                message="正在按已对齐表头逐行抽取",
            )
        cells = [cell for cell in (row.get("cells") or []) if isinstance(cell, dict)]
        values_by_col = {safe_int(cell.get("col_index"), -1): normalize_text(cell.get("value")) for cell in cells}
        source_text = build_table_row_source_text(table_title, header_candidates, row)
        result_row = {
            "record_id": make_row_id(),
            "__key_fields__": [],
            "__field_keys__": {},
            "__sources__": {},
            "__field_options__": {},
            "__decision_trace__": {},
        }
        filled = 0
        for field in field_tasks:
            label = field["field_name"]
            field_key = field["name"]
            result_row["__field_keys__"][label] = field_key
            mapping = header_mappings.get(label) or {}
            source_header = normalize_text(mapping.get("source_header"))
            header_info = header_by_name.get(source_header)
            value = None
            source_payload = None
            if header_info is not None:
                raw_value = values_by_col.get(header_info["col_index"], "")
                slot = normalize_text(field.get("slot"))
                if slot == "value":
                    parsed = parse_number_unit(raw_value)
                    value = normalize_text(parsed.get("number") or raw_value)
                elif slot == "unit":
                    parsed = parse_number_unit(raw_value)
                    value = normalize_text(parsed.get("unit"))
                else:
                    value = normalize_text(raw_value)
                source_payload = {
                    "paragraph_id": None,
                    "paragraph_text": source_text,
                    "evidence": f"{source_header}: {normalize_text(raw_value)}" if raw_value else source_header,
                    "confidence": round(float(mapping.get("confidence") or 0.88), 4),
                    "source_kind": "table_row",
                    "source_table_id": table_view.get("table_id"),
                    "source_row": row.get("row_index"),
                    "source_col": header_info["col_index"],
                    "source_header": source_header,
                    "source_locator": next(
                        (
                            normalize_text(cell.get("locator"))
                            for cell in cells
                            if safe_int(cell.get("col_index"), -1) == header_info["col_index"]
                        ),
                        "",
                    ),
                }
            result_row[label] = value or None
            if source_payload:
                result_row["__sources__"][label] = source_payload
                result_row["__decision_trace__"][label] = {
                    "mode": "header_mapping",
                    "selected_option_id": source_header or None,
                    "alternatives": [],
                    "confidence": round(float(mapping.get("confidence") or 0.0), 4),
                    "reason": normalize_text(mapping.get("reason")) or "表头映射后直接抽取",
                }
            if value:
                filled += 1
                if field.get("slot") in {"category", "indicator"}:
                    result_row["__key_fields__"].append(label)
        if filled > 0:
            rows.append(result_row)
    return rows


def parse_number_unit(text: str) -> dict[str, str]:
    text = normalize_text(text)
    if not text:
        return {"raw": "", "number": "", "unit": "", "normalized": ""}
    match = NUMBER_PATTERN.search(text)
    if not match:
        return {"raw": text, "number": "", "unit": "", "normalized": text}
    number = normalize_text(match.group(1))
    unit = normalize_text(match.group(2))
    normalized = f"{number}{unit}" if unit else number
    return {"raw": text, "number": number, "unit": unit, "normalized": normalized}


def normalize_cell_value(raw_value: str) -> dict[str, str]:
    raw_value = normalize_text(raw_value)
    parsed = parse_number_unit(raw_value)
    return {
        "raw": raw_value,
        "value": raw_value,
        "number": parsed["number"],
        "unit": parsed["unit"],
        "normalized": parsed["normalized"] or raw_value,
    }


def get_field_terms(field_task: dict[str, Any]) -> list[str]:
    return unique_strings([field_task["field_name"], *field_task.get("aliases", [])])


def collect_anchor_fragments(*texts: Any) -> list[str]:
    fragments = []
    for raw_text in texts:
        text = normalize_text(raw_text)
        if not text:
            continue
        fragments.append(text)
        for chunk in re.split(r"[|；;。。，,\n]+", text):
            chunk = normalize_text(chunk)
            if not chunk:
                continue
            fragments.append(chunk)
            if ":" in chunk:
                fragments.append(normalize_text(chunk.split(":", 1)[0]))
            label_match = re.match(r"^\s*(.{1,24}?)(?::|：|(?:为|是|有|达|共|约|超)|\d)", chunk)
            if label_match:
                fragments.append(normalize_text(label_match.group(1)))
    return unique_strings(fragments)


def score_field_term_similarity(field_task: dict[str, Any], text: Any) -> float:
    candidate = normalize_text(text)
    if not candidate:
        return 0.0
    terms = get_field_terms(field_task)
    scores = [soft_text_similarity(candidate, term) for term in terms] if terms else [1.0]
    description = normalize_text(field_task.get("description"))
    if description:
        scores.append(soft_text_similarity(candidate, description) * 0.72)
    topic = extract_distinct_field_topic(field_task.get("field_name"))
    if topic and topic not in candidate:
        scores = [score * 0.55 for score in scores]
    return max(scores or [0.0])


def get_field_anchor_score(field_task: dict[str, Any], paragraph_text: str, evidence: str = "") -> float:
    combined = normalize_for_match(f"{paragraph_text} {evidence}")
    terms = [normalize_for_match(term) for term in get_field_terms(field_task)]
    terms = [term for term in terms if len(term) >= 2]
    if not terms:
        return 1.0
    topic = normalize_for_match(extract_distinct_field_topic(field_task.get("field_name")))
    if any(term in combined for term in terms) and (not topic or topic in combined):
        return 1.0
    best_score = 0.0
    for fragment in collect_anchor_fragments(evidence, paragraph_text):
        best_score = max(best_score, score_field_term_similarity(field_task, fragment))
    return best_score


def paragraph_has_field_anchor(
    field_task: dict[str, Any],
    paragraph_text: str,
    evidence: str = "",
    *,
    strict_anchor: bool = True,
) -> bool:
    anchor_score = get_field_anchor_score(field_task, paragraph_text, evidence)
    if strict_anchor:
        return anchor_score >= 1.0
    return anchor_score >= RELAXED_TEXT_ANCHOR_SCORE


def looks_like_full_row_dump(value: str, paragraph_text: str) -> bool:
    value_text = normalize_text(value)
    paragraph_text = normalize_text(paragraph_text)
    if not value_text or not paragraph_text:
        return False
    if normalize_for_match(value_text) == normalize_for_match(paragraph_text) and len(paragraph_text) > 18:
        return True
    if len(value_text) > 90 and len(value_text) >= max(36, int(len(paragraph_text) * 0.75)):
        return True
    return False


def build_field_source(paragraph: dict[str, Any], evidence: str, confidence: float) -> dict[str, Any]:
    return {
        "paragraph_id": paragraph.get("paragraph_id"),
        "paragraph_text": paragraph.get("text"),
        "evidence": normalize_text(evidence),
        "confidence": float(confidence),
        "source_kind": paragraph.get("source_kind"),
        "source_table_id": paragraph.get("source_table_id"),
        "source_row": paragraph.get("source_row"),
        "source_col": paragraph.get("source_col"),
        "source_header": paragraph.get("source_header"),
        "source_locator": paragraph.get("source_locator"),
    }


def build_rule_match_hint(field_task: dict[str, Any], phrase: str) -> dict[str, Any]:
    return {
        "phrase": normalize_text(phrase),
        "target_field": field_task["field_name"],
        "target_score": 1.0,
        "is_target_top": True,
        "top_matches": [
            {
                "field_key": field_task["name"],
                "field_name": field_task["field_name"],
                "score": 1.0,
            }
        ],
    }


def build_rule_field_cell(
    field_task: dict[str, Any],
    paragraph: dict[str, Any],
    value: Any,
    evidence: Any,
    confidence: float = NON_TABLE_RULE_CONFIDENCE,
) -> dict[str, Any] | None:
    raw_value = normalize_text(value)
    evidence_text = normalize_text(evidence) or raw_value
    if not raw_value:
        return None
    slot = normalize_text(field_task.get("slot"))
    topic = extract_distinct_field_topic(field_task.get("field_name"))
    if topic and topic not in evidence_text and not value_occurrence_has_topic(paragraph.get("text"), raw_value, topic):
        return None
    inferred_unit = infer_unit_from_context(paragraph.get("text"), raw_value, evidence_text)
    if field_prefers_ratio_literal(field_task):
        if not is_ratio_literal(raw_value) and inferred_unit not in {"%", "％", "百分点", "‰"}:
            return None
    elif slot != "yoy" and (is_ratio_literal(raw_value) or inferred_unit in {"%", "％", "百分点", "‰"}):
        return None
    if field_prefers_count_unit(field_task):
        parsed = parse_number_unit(raw_value)
        unit = normalize_text(parsed.get("unit")) or normalize_text(inferred_unit)
        if unit not in {"天", "个", "家", "人", "次", "床", "床日", "户", "名", "所"}:
            return None
    if normalize_text(field_task.get("slot")) == "yoy" and not has_yoy_signal(raw_value, evidence_text, paragraph.get("text")):
        return None
    if normalize_text(field_task.get("slot")) == "yoy" and not (
        looks_like_numeric_literal(raw_value)
        or YOY_TEXT_PATTERN.search(raw_value)
    ):
        return None
    if not is_plausible_slot_value(field_task, raw_value):
        return None
    parsed = normalize_cell_value(raw_value)
    return {
        "label": field_task["field_name"],
        "value": parsed["value"] or None,
        "raw": parsed["raw"] or None,
        "number": parsed["number"] or None,
        "unit": parsed["unit"] or None,
        "normalized": parsed["normalized"] or parsed["value"] or None,
        "matched_phrase": evidence_text or raw_value,
        "source": build_field_source(paragraph, evidence_text or raw_value, confidence),
        "match_hint": build_rule_match_hint(field_task, evidence_text or raw_value),
    }


def validate_row_field(
    field_task: dict[str, Any],
    paragraph: dict[str, Any],
    value: Any,
    evidence: Any,
    confidence: Any,
    *,
    strict_field_anchor: bool = True,
) -> dict[str, Any] | None:
    raw_value = normalize_text(value)
    if not raw_value or raw_value in {"略", "未提及", "无", "暂无"}:
        return None

    paragraph_text = normalize_text(paragraph.get("text"))
    evidence_text = normalize_text(evidence) or raw_value
    slot = normalize_text(field_task.get("slot"))
    topic = extract_distinct_field_topic(field_task.get("field_name"))
    if topic and topic not in evidence_text and not value_occurrence_has_topic(paragraph_text, raw_value, topic):
        return None
    inferred_unit = infer_unit_from_context(paragraph_text, raw_value, evidence_text)
    if field_prefers_ratio_literal(field_task):
        if not is_ratio_literal(raw_value) and inferred_unit not in {"%", "％", "百分点", "‰"}:
            return None
    elif slot != "yoy" and (is_ratio_literal(raw_value) or inferred_unit in {"%", "％", "百分点", "‰"}):
        return None
    if field_prefers_count_unit(field_task):
        parsed = parse_number_unit(raw_value)
        unit = normalize_text(parsed.get("unit")) or normalize_text(inferred_unit)
        if unit not in {"天", "个", "家", "人", "次", "床", "床日", "户", "名", "所"}:
            return None
    if not strict_field_anchor and is_non_table_noise_text(paragraph_text):
        return None
    if looks_like_full_row_dump(raw_value, paragraph_text):
        return None

    value_signature = normalize_for_match(raw_value)
    if not value_signature:
        return None

    paragraph_signature = normalize_for_match(paragraph_text)
    evidence_signature = normalize_for_match(evidence_text)
    if value_signature not in paragraph_signature and value_signature not in evidence_signature:
        return None

    if not paragraph_has_field_anchor(field_task, paragraph_text, evidence_text, strict_anchor=strict_field_anchor):
        return None

    slot = field_task.get("slot")
    if slot == "time" and not DATE_PATTERN.search(raw_value):
        return None
    if slot in {"value", "yoy"} and not re.search(r"\d", raw_value):
        return None
    if slot == "yoy" and not has_yoy_signal(raw_value, evidence_text, paragraph_text):
        return None
    if slot == "yoy" and not (
        looks_like_numeric_literal(raw_value)
        or YOY_TEXT_PATTERN.search(raw_value)
    ):
        return None
    if not is_plausible_slot_value(field_task, raw_value):
        return None

    try:
        confidence_value = float(confidence) if confidence not in [None, ""] else 0.86
    except Exception:
        confidence_value = 0.86
    if confidence_value < MIN_ROW_FIELD_CONFIDENCE:
        return None

    parsed = normalize_cell_value(raw_value)
    if field_prefers_metric_literal(field_task) and not parsed.get("unit"):
        inferred_unit = infer_unit_from_context(paragraph_text, raw_value, evidence_text)
        if inferred_unit:
            parsed["value"] = f"{parsed['number'] or raw_value}{inferred_unit}"
            parsed["raw"] = parsed["value"]
            parsed["unit"] = inferred_unit
            parsed["normalized"] = f"{parsed['number'] or raw_value}{inferred_unit}"
    return {
        "label": field_task["field_name"],
        "value": parsed["value"] or None,
        "raw": parsed["raw"] or None,
        "number": parsed["number"] or None,
        "unit": parsed["unit"] or None,
        "normalized": parsed["normalized"] or parsed["value"] or None,
        "matched_phrase": evidence_text or raw_value,
        "source": build_field_source(paragraph, evidence_text, confidence_value),
    }


def extract_first_date(text: Any) -> str:
    match = DATE_PATTERN.search(normalize_text(text))
    return re.sub(r"\s+", "", normalize_text(match.group(1))) if match else ""


def extract_year_from_paragraphs(paragraphs: list[dict[str, Any]]) -> str:
    for paragraph in paragraphs[:12]:
        text = normalize_text(paragraph.get("text"))
        if not text:
            continue
        match = re.search(r"(20\d{2})\s*年", text)
        if match:
            return f"{match.group(1)}年"
        date_text = extract_first_date(text)
        year_match = re.search(r"(20\d{2})", date_text)
        if year_match:
            return f"{year_match.group(1)}年"
    return ""


def extract_nearest_date_before_index(text: Any, end_index: int, fallback: str = "") -> str:
    normalized = normalize_text(text)
    if not normalized:
        return fallback
    nearest = ""
    for match in DATE_PATTERN.finditer(normalized):
        if match.start() > end_index:
            break
        nearest = re.sub(r"\s+", "", normalize_text(match.group(1)))
    return nearest or fallback


def is_non_table_noise_text(text: Any) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return True
    normalized = normalized.strip("* ")
    if NON_TABLE_NOISE_TEXT_PATTERN.fullmatch(normalized):
        return True
    return False


def has_change_value_prefix(prefix: Any) -> bool:
    normalized = normalize_text(prefix).strip("（( ").strip()
    if not normalized:
        return False
    if re.search(r"(?:增加|减少|增长|下降|提高|回落|上升)\s*\d+(?:\.\d+)?[^\d，。,；;（）()]{0,8}(?:和|及)$", normalized):
        return True
    if re.search(
        r"(?:同比|比上年|较上年|比年初|较年初|与上年比较|与上年相比|与上年|上年末|较上年末)(?:[^，。,；;（）()]{0,12})?(?:增加|减少|增长|下降|提高|回落|上升)$",
        normalized,
    ):
        return True
    if normalized.endswith(("增加", "减少", "增长", "下降", "提高", "回落", "上升")) and not normalized.endswith(("增加值", "增长值")):
        return True
    return False


def infer_unit_from_context(paragraph_text: Any, raw_value: Any, evidence: Any = "") -> str:
    text = normalize_text(paragraph_text)
    value = normalize_text(raw_value)
    evidence_text = normalize_text(evidence)
    if not text or not value:
        return ""

    anchors = [anchor for anchor in [evidence_text, value] if anchor]
    checked = set()
    for anchor in anchors:
        start = text.find(anchor)
        while start >= 0:
            if start in checked:
                break
            checked.add(start)
            suffix = text[start + len(anchor): start + len(anchor) + 8]
            parsed = parse_number_unit(f"{value}{suffix}")
            if parsed.get("unit") and parsed.get("unit") != "年":
                return parsed["unit"]
            start = text.find(anchor, start + 1)
    return ""


def value_occurrence_has_topic(paragraph_text: Any, raw_value: Any, topic: str, window: int = 14) -> bool:
    text = normalize_text(paragraph_text)
    value = normalize_text(raw_value)
    topic_text = normalize_text(topic)
    if not text or not value or not topic_text:
        return False
    start = text.find(value)
    while start >= 0:
        left = max(0, start - window)
        right = min(len(text), start + len(value) + window)
        snippet = text[left:right]
        if topic_text in snippet:
            return True
        start = text.find(value, start + 1)
    return False


def split_metric_fragments(text: Any) -> list[str]:
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
            if buffer and re.search(r"\d", piece) and not YOY_TEXT_PATTERN.search(piece) and re.search(r"\d", buffer):
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


def extract_place_names(text: Any) -> list[str]:
    normalized = normalize_text(text)
    if not normalized:
        return []
    names = []
    for match in PLACE_LIST_PATTERN.finditer(normalized):
        raw_places = normalize_text(match.group("places"))
        if not raw_places:
            continue
        candidates = []
        for item in raw_places.split("、"):
            candidate = normalize_text(item)
            candidate = re.sub(r"^(?:截至)?20\d{2}年(?:末|底|初)?", "", candidate).strip()
            if candidate:
                candidates.append(candidate)
        if not candidates:
            continue
        if len(candidates) > len(names):
            names = candidates
    return unique_strings(names)


def has_multiple_category_entities(text: Any) -> bool:
    return len(extract_place_names(text)) > 1


def find_relevant_category(text: Any) -> str:
    normalized = normalize_text(text)
    if not normalized:
        return ""
    leading_match = re.search(
        r"^(?:(?:其中|此外|另外|全年|年末|截至|按[^，,]+分|分[^，,]+看)[：:，,\s]*)?(全国|全市|全省|全县|东部地区|中部地区|西部地区|东北地区|京津冀地区|长江经济带地区|长三角地区|粤港澳大湾区|城镇居民|农村居民|城市|农村|第一产业|第二产业|第三产业|夏粮|早稻|秋粮|稻谷|小麦|玉米|大豆|棉花|油料|糖料|茶叶|猪肉|牛肉|羊肉|禽肉|养殖|捕捞|国有控股企业|股份制企业|外商及港澳台投资企业|私营企业|采矿业|制造业|电力、热力、燃气及水生产和供应业|公共图书馆|群众文化机构|旅行社|星级饭店|A级景区|县以上|县及县以下)",
        normalized,
    )
    if leading_match:
        return normalize_text(leading_match.group(1))
    direct_match = CATEGORY_PATTERN.search(normalized)
    if direct_match:
        return normalize_text(direct_match.group(1))
    compound_match = re.search(
        r"^(?:(?:其中|此外|另外|全年|年末|截至)[：:，,\s]*)?([\u4e00-\u9fa5A-Za-z0-9·（）()、]{2,24}?)(?:销售额|销售总额|总数|数量|产值|收入|利润|费用|增加值|比重|占比)",
        normalized,
    )
    if compound_match:
        return normalize_text(compound_match.group(1))
    place_names = extract_place_names(normalized)
    if len(place_names) == 1:
        return place_names[0]
    return ""


def clean_indicator_text(text: Any, category: str = "") -> str:
    candidate = normalize_text(text)
    if not candidate:
        return ""
    if category and candidate.startswith(category):
        candidate = candidate[len(category):].strip()
    candidate = candidate.replace("（占", "占").replace("(占", "占")
    candidate = re.sub(r"^[①②③④⑤⑥⑦⑧⑨⑩]\s*", "", candidate)
    candidate = re.sub(r"^(?:20\d{2}\s*年(?:末|底|全年)?|截至20\d{2}\s*年(?:底|末)?|20\d{2}\s*年)", "", candidate).strip()
    candidate = re.sub(r"^(其中|全年|全年来看|年末|截至|截至目前|初步核算|分季度看|分区域看|在规模以上工业中|全国共有|全市共有|全省共有|全国平均每万人|全国人均|共|有|达到|实现|为|的|占)", "", candidate)
    candidate = re.sub(r"(比上年.*|同比.*|较上年.*|较年初.*|比年初.*)$", "", candidate).strip()
    candidate = re.sub(r"由20\d{2}\s*年.*增加到20\d{2}\s*年$", "", candidate).strip()
    candidate = re.sub(r"(?:初步核算)?(?:达到|达|为|下降至|下降到|降至|提高到|提高至|提高了|增至|增加到|增加至|减少至|减至|升至|上升至|降为|约为|约达)$", "", candidate).strip()
    candidate = re.sub(r"\d+(?:\.\d+)?\s*(?:万亿元|万亿|亿元|万元|亿人次|万人次|亿次|万次|亿人|万人|万张|张|万家|家|万个|个|万户|户|万名|名|万份|份|万项|项|万张|张|万册次|册次|万平方米|平方米|%|％|个百分点|‰|亿|万)?(?=占$)", "", candidate).strip()
    candidate = candidate.strip("，,。；;:：、 ")
    candidate = re.sub(r"(共有|拥有|接待|实现|完成|达到)$", "", candidate).strip()
    candidate = re.sub(r"(约|左右)$", "", candidate).strip()
    candidate = re.sub(r"\s+", "", candidate)
    if len(candidate) <= 1:
        return ""
    return candidate


def infer_indicator_for_match(fragment: str, match: re.Match[str], category: str = "", prefer_ratio: bool = False) -> str:
    prefix = normalize_text(fragment[:match.start()])
    prefix = re.sub(r".*[，,:：]", "", prefix)
    candidate = clean_indicator_text(prefix, category=category)
    if candidate:
        if prefer_ratio and candidate.endswith("占"):
            return f"{candidate}比"
        return candidate
    window_prefix = normalize_text(fragment[max(0, match.start() - 24):match.start()])
    candidate = clean_indicator_text(window_prefix, category=category)
    if prefer_ratio and candidate.endswith("占"):
        return f"{candidate}比"
    return candidate


def find_yoy_after_index(fragment: str, start_index: int) -> str:
    suffix = normalize_text(fragment[start_index:])
    patterns = [
        r"(?:同比|比上年|较上年)(?:增长|下降|提高|减少|增产|减产)?\s*(-?\d+(?:\.\d+)?)\s*(%|％|个百分点|‰)?",
        r"(?:增长|下降|提高|减少|增产|减产)\s*(-?\d+(?:\.\d+)?)\s*(%|％|百分点|个百分点|‰)?",
        r"(?:同比|比上年|较上年)(?:增加|减少|增长|下降|提高|回落|上升)\s*(-?\d+(?:\.\d+)?)\s*(万个|个|万家|家|万人|人|万户|户|万张|张|万台|台|亿元|万元|亿人次|万人次|人次|次)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, suffix)
        if not match:
            continue
        value = normalize_text(match.group(1))
        unit = normalize_text(match.group(2))
        if not unit:
            unit = infer_unit_from_context(suffix, value, match.group(0))
        if not unit:
            unit = "%"
        return f"{value}{unit}" if unit else value
    return ""


def assign_slot_field_value(
    fields: dict[str, Any],
    field_tasks_by_slot: dict[str, list[dict[str, Any]]],
    slot: str,
    paragraph: dict[str, Any],
    value: Any,
    evidence: Any,
    confidence: float = NON_TABLE_RULE_CONFIDENCE,
) -> None:
    raw_value = normalize_text(value)
    if not raw_value:
        return
    slot_fields = field_tasks_by_slot.get(slot, [])
    require_disambiguation = len(slot_fields) > 1
    paragraph_text = normalize_text(paragraph.get("text"))
    evidence_text = normalize_text(evidence) or raw_value
    for field_task in slot_fields:
        if require_disambiguation:
            anchor_score = get_field_anchor_score(field_task, paragraph_text, evidence_text)
            if anchor_score < RELAXED_TEXT_ANCHOR_SCORE:
                continue
        cell = build_rule_field_cell(field_task, paragraph, raw_value, evidence, confidence=confidence)
        if cell is not None:
            fields[field_task["name"]] = cell


def build_non_table_candidate_from_fragment(
    fragment: str,
    paragraph: dict[str, Any],
    field_tasks_by_slot: dict[str, list[dict[str, Any]]],
    fallback_time: str,
    inherited_category: str = "",
) -> list[dict[str, Any]]:
    normalized = normalize_text(fragment)
    if not normalized or normalized.startswith("发布时间") or DATE_PATTERN.fullmatch(normalized) or is_non_table_noise_text(normalized):
        return []

    row_time = extract_first_date(normalized) or fallback_time
    category = find_relevant_category(normalized) or inherited_category
    candidates = []
    primary_field_keys = {
        field_task["name"]
        for slot in {"indicator", "value", "yoy"}
        for field_task in field_tasks_by_slot.get(slot, [])
    }

    for match in NUMBER_PATTERN.finditer(normalized):
        value = normalize_text(match.group(1))
        unit = normalize_text(match.group(2))
        local_prefix = normalize_text(normalized[max(0, match.start() - 16):match.start()])
        if not value:
            continue
        if re.match(r"\s*[~～\-—至到/]", normalized[match.end():match.end() + 2]):
            continue
        if re.search(r"[~～\-—至到/]\s*$", normalized[max(0, match.start() - 2):match.start()]):
            continue
        if YEAR_ONLY_PATTERN.fullmatch(value) and unit in {"", "年"}:
            continue
        match_time = extract_nearest_date_before_index(normalized, match.start(), row_time)
        if match_time and re.search(rf"{re.escape(value)}\s*(?:年|月|日)", normalized[max(0, match.start() - 2):match.end() + 2]):
            continue
        if match.start() > 0 and normalized[max(0, match.start() - 4):match.start()].endswith("第"):
            continue
        if has_change_value_prefix(local_prefix):
            continue

        metric_text = normalize_text(normalized[max(0, match.start() - 10):min(len(normalized), match.end() + 18)])
        is_ratio = unit in {"%", "％", "个百分点", "‰"}
        if is_ratio and YOY_TEXT_PATTERN.search(local_prefix):
            continue

        indicator = infer_indicator_for_match(normalized, match, category=category, prefer_ratio=is_ratio)
        if not indicator:
            continue

        yoy = find_yoy_after_index(normalized, match.end())
        value_text = value
        unit_text = unit
        if is_ratio and YOY_TEXT_PATTERN.search(metric_text):
            yoy = f"{value}{unit or '%'}"
            value_text = ""
            unit_text = ""

        fields: dict[str, Any] = {}
        assign_slot_field_value(fields, field_tasks_by_slot, "category", paragraph, category, category or normalized)
        for field_task in field_tasks_by_slot.get("indicator", []):
            indicator_value = indicator
            indicator_evidence = indicator
            if field_prefers_metric_literal(field_task) and value_text:
                indicator_value = f"{value_text}{unit_text}" if unit_text else value_text
                indicator_evidence = metric_text or indicator_value
            cell = build_rule_field_cell(
                field_task,
                paragraph,
                indicator_value,
                indicator_evidence,
                confidence=NON_TABLE_RULE_CONFIDENCE,
            )
            if cell is not None:
                fields[field_task["name"]] = cell
        assign_slot_field_value(
            fields,
            field_tasks_by_slot,
            "value",
            paragraph,
            value_text,
            f"{indicator}{value_text}{unit_text}" if indicator and value_text else (metric_text or f"{value_text}{unit_text}"),
        )
        assign_slot_field_value(fields, field_tasks_by_slot, "unit", paragraph, unit_text, f"{value}{unit}" if unit else unit_text)
        assign_slot_field_value(fields, field_tasks_by_slot, "time", paragraph, match_time, match_time)
        assign_slot_field_value(fields, field_tasks_by_slot, "yoy", paragraph, yoy, yoy or metric_text)
        if not fields or (primary_field_keys and not any(field_key in fields for field_key in primary_field_keys)):
            continue

        candidates.append(
            {
                "candidate_id": make_row_id(),
                "paragraph_id": paragraph.get("paragraph_id"),
                "paragraph_text": paragraph.get("text"),
                "source_kind": paragraph.get("source_kind"),
                "source_table_id": paragraph.get("source_table_id"),
                "source_row": paragraph.get("source_row"),
                "source_col": paragraph.get("source_col"),
                "source_header": paragraph.get("source_header"),
                "source_locator": paragraph.get("source_locator"),
                "fields": fields,
            }
        )
    return candidates


def choose_parallel_field_task(
    label: str,
    sample_value: str,
    field_tasks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    label_text = normalize_text(label)
    if not label_text:
        return None

    best_field = None
    best_score = 0.0
    for field_task in field_tasks:
        slot = normalize_text(field_task.get("slot"))
        if slot in {"category", "time", "unit", "source_paragraph"}:
            continue
        score = max(
            score_field_term_similarity(field_task, label_text),
            get_field_anchor_score(field_task, label_text, label_text),
        )
        if is_ratio_literal(sample_value):
            score += 0.45 if field_prefers_ratio_literal(field_task) else -0.9
        elif field_prefers_ratio_literal(field_task):
            score -= 0.75
        if score > best_score:
            best_score = score
            best_field = field_task
    if best_score < 0.72:
        return None
    return best_field


def split_parallel_values(raw_text: str) -> list[str]:
    normalized = normalize_text(raw_text).strip("，,。；; ")
    if not normalized:
        return []
    parts = [
        normalize_text(item).strip("，,。；; ")
        for item in re.split(r"[、]", normalized)
        if normalize_text(item).strip("，,。；; ")
    ]
    return parts


def build_parallel_list_candidates(
    paragraph: dict[str, Any],
    field_tasks: list[dict[str, Any]],
    field_tasks_by_slot: dict[str, list[dict[str, Any]]],
    fallback_time: str,
) -> list[dict[str, Any]]:
    paragraph_text = normalize_text(paragraph.get("text"))
    if not paragraph_text or "分别为" not in paragraph_text:
        return []

    category_names = extract_place_names(paragraph_text)
    if len(category_names) <= 1:
        return []

    category_fields = field_tasks_by_slot.get("category", [])
    if not category_fields:
        return []

    assignments = []
    category_seed = "、".join(category_names)
    for match in PARALLEL_LABEL_VALUES_PATTERN.finditer(paragraph_text):
        raw_label = normalize_text(match.group("label"))
        values = split_parallel_values(match.group("values"))
        if not raw_label or len(values) != len(category_names):
            continue
        clean_label = clean_indicator_text(raw_label, category=category_seed) or raw_label
        field_task = choose_parallel_field_task(clean_label, values[0], field_tasks)
        if field_task is None:
            continue
        assignments.append((field_task, clean_label, values))

    if not assignments:
        return []

    candidates = []
    time_fields = field_tasks_by_slot.get("time", [])
    primary_field_keys = {
        field_task["name"]
        for slot in {"indicator", "value", "yoy"}
        for field_task in field_tasks_by_slot.get(slot, [])
    }
    for index, category_name in enumerate(category_names):
        fields: dict[str, Any] = {}
        for category_field in category_fields:
            category_cell = build_rule_field_cell(
                category_field,
                paragraph,
                category_name,
                category_name,
                confidence=NON_TABLE_RULE_CONFIDENCE + 0.02,
            )
            if category_cell is not None:
                fields[category_field["name"]] = category_cell
        for time_field in time_fields:
            if not fallback_time:
                continue
            time_cell = build_rule_field_cell(
                time_field,
                paragraph,
                fallback_time,
                fallback_time,
                confidence=NON_TABLE_RULE_CONFIDENCE,
            )
            if time_cell is not None:
                fields[time_field["name"]] = time_cell
        for field_task, label, values in assignments:
            value = values[index]
            cell = build_rule_field_cell(
                field_task,
                paragraph,
                value,
                f"{label}:{value}",
                confidence=NON_TABLE_RULE_CONFIDENCE + 0.03,
            )
            if cell is not None:
                fields[field_task["name"]] = cell
        if primary_field_keys and not any(field_key in fields for field_key in primary_field_keys):
            continue
        candidates.append(
            {
                "candidate_id": make_row_id(),
                "paragraph_id": paragraph.get("paragraph_id"),
                "paragraph_text": paragraph.get("text"),
                "source_kind": paragraph.get("source_kind"),
                "source_table_id": paragraph.get("source_table_id"),
                "source_row": paragraph.get("source_row"),
                "source_col": paragraph.get("source_col"),
                "source_header": paragraph.get("source_header"),
                "source_locator": paragraph.get("source_locator"),
                "fields": fields,
            }
        )
    return candidates


def build_non_table_fragment_candidates(
    field_tasks: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not field_tasks or not paragraphs:
        return []

    field_tasks_by_slot: dict[str, list[dict[str, Any]]] = {}
    for field_task in field_tasks:
        slot = normalize_text(field_task.get("slot"))
        if slot:
            field_tasks_by_slot.setdefault(slot, []).append(field_task)

    if not any(slot in field_tasks_by_slot for slot in {"indicator", "value", "yoy"}):
        return []

    fallback_time = extract_year_from_paragraphs(paragraphs)
    candidates = []
    for paragraph in paragraphs:
        paragraph_text = normalize_text(paragraph.get("text"))
        if not paragraph_text or is_non_table_noise_text(paragraph_text) or not re.search(r"\d", paragraph_text):
            continue
        paragraph_time = extract_first_date(paragraph_text) or fallback_time
        inherited_category = find_relevant_category(paragraph_text)
        candidates.extend(
            build_parallel_list_candidates(
                paragraph,
                field_tasks,
                field_tasks_by_slot,
                paragraph_time,
            )
        )
        for fragment in split_metric_fragments(paragraph_text):
            candidates.extend(
                build_non_table_candidate_from_fragment(
                    fragment,
                    paragraph,
                    field_tasks_by_slot,
                    paragraph_time,
                    inherited_category=inherited_category,
                )
            )
    return candidates


def build_extract_rows_prompt(
    field_tasks: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    *,
    strict_field_anchor: bool = True,
) -> str:
    field_requirement = (
        "只有当字段名或别名与段落内容语义高度一致、证据明确时，才返回该字段。"
        if strict_field_anchor
        else "字段名与原文不要求字面完全一致；只要是同义、近义、缩略表达且证据明确，就可以命中该字段。"
    )
    payload = {
        "task": "按段落逐条识别当前表头下的字段值，先做行内结构化，不做跨段拼接。",
        "requirements": [
            "每个 paragraph_id 必须独立判断，不能借用其他段落的信息。",
            "fields 里只能使用提供的 key，不能新增字段，不能改 key。",
            field_requirement,
            "如果某字段描述或列名明显表示金额、数量、规模等可量化结果，value 优先返回具体数值，不要返回指标名称。",
            "value 必须是最小可用值，不能返回整段、整行说明或多个字段拼接结果。",
            "evidence 必须来自同一段落，并且能够直接支持 value。",
            "如果一个段落里包含多个独立记录，可以拆成多个 records；否则最多返回一个 record。",
            "拿不准就不要返回该字段，不要补全，不要猜测，不要输出默认值。",
            "只输出 JSON。",
        ],
        "fields": [
            {
                "field_name": field["field_name"],
                "key": field["name"],
                "slot": field.get("slot"),
                "aliases": field.get("aliases", []),
                "description": field.get("description", ""),
                "prefers_metric_literal": field_prefers_metric_literal(field),
            }
            for field in field_tasks
        ],
        "paragraphs": [
            {
                "paragraph_id": paragraph["paragraph_id"],
                "text": paragraph["text"],
                "origin": paragraph.get("origin"),
                "source_kind": paragraph.get("source_kind"),
                "source_table_id": paragraph.get("source_table_id"),
                "source_row": paragraph.get("source_row"),
            }
            for paragraph in paragraphs
        ],
        "output_schema": {
            "items": [
                {
                    "paragraph_id": 0,
                    "records": [
                        {
                            "fields": {
                                "field_key": {
                                    "value": "字段值",
                                    "evidence": "原文证据",
                                    "confidence": 0.0,
                                }
                            }
                        }
                    ],
                }
            ]
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def extract_candidate_value_from_text(
    field_task: dict[str, Any],
    text: str,
    terms: list[str],
    *,
    strict_field_anchor: bool = True,
) -> str | None:
    normalized_text = normalize_text(text)
    normalized_signature = normalize_for_match(normalized_text)
    if terms:
        normalized_terms = [normalize_for_match(term) for term in terms if normalize_for_match(term)]
        if strict_field_anchor and normalized_terms and not any(term in normalized_signature for term in normalized_terms):
            return None
        if not strict_field_anchor and get_field_anchor_score(field_task, normalized_text) < RELAXED_TEXT_ANCHOR_SCORE:
            return None

    for term in terms:
        if not term:
            continue
        for pattern in [
            rf"{re.escape(term)}\s*[:：]\s*([^|；。。，,\n]+)",
            rf"{re.escape(term)}\s*(?:为|是|有|达|共|约|超)\s*([^|；。。，,\n]+)",
        ]:
            match = re.search(pattern, normalized_text, re.I)
            if match:
                candidate = normalize_text(match.group(1))
                if candidate:
                    return candidate

    if not strict_field_anchor:
        for pattern in [
            r"(.{1,24}?)\s*[:：]\s*([^|；。。，,\n]+)",
            r"(.{1,24}?)\s*(?:为|是|有|达|共|约|超)\s*([^|；。。，,\n]+)",
        ]:
            for match in re.finditer(pattern, normalized_text, re.I):
                label = normalize_text(match.group(1))
                candidate = normalize_text(match.group(2))
                if candidate and score_field_term_similarity(field_task, label) >= RELAXED_TEXT_ANCHOR_SCORE:
                    return candidate

    slot = field_task.get("slot")
    if slot == "time":
        match = DATE_PATTERN.search(normalized_text)
        if match:
            return normalize_text(match.group(1))

    if slot in {"value", "yoy"}:
        if not strict_field_anchor:
            return None
        match = NUMBER_PATTERN.search(normalized_text)
        if match:
            number = normalize_text(match.group(1))
            unit = normalize_text(match.group(2))
            return f"{number}{unit}" if unit else number
    return None


def fallback_extract_row_candidates(
    field_tasks: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    *,
    strict_field_anchor: bool = True,
) -> list[dict[str, Any]]:
    candidates = []
    for paragraph in paragraphs:
        fields = {}
        for field_task in field_tasks:
            terms = get_field_terms(field_task)
            value = extract_candidate_value_from_text(
                field_task,
                paragraph["text"],
                terms,
                strict_field_anchor=strict_field_anchor,
            )
            if not value:
                continue
            validated = validate_row_field(
                field_task,
                paragraph,
                value,
                value,
                0.78,
                strict_field_anchor=strict_field_anchor,
            )
            if validated is not None:
                fields[field_task["name"]] = validated
        if fields:
            candidates.append(
                {
                    "candidate_id": make_row_id(),
                    "paragraph_id": paragraph["paragraph_id"],
                    "paragraph_text": paragraph["text"],
                    "source_kind": paragraph.get("source_kind"),
                    "source_table_id": paragraph.get("source_table_id"),
                    "source_row": paragraph.get("source_row"),
                    "source_col": paragraph.get("source_col"),
                    "source_header": paragraph.get("source_header"),
                    "source_locator": paragraph.get("source_locator"),
                    "fields": fields,
                }
            )
    return candidates


def normalize_records_from_item(item: dict[str, Any]) -> list[dict[str, Any]]:
    records = item.get("records")
    if isinstance(records, list):
        return [record for record in records if isinstance(record, dict)]
    if isinstance(item.get("fields"), dict):
        return [{"fields": item.get("fields")}]
    return []


def parse_row_candidates_from_response(
    parsed: Any,
    paragraph_map: dict[int, dict[str, Any]],
    field_tasks: list[dict[str, Any]],
    *,
    strict_field_anchor: bool = True,
) -> list[dict[str, Any]]:
    parsed = normalize_llm_row_payload(parsed)
    field_map = {field["name"]: field for field in field_tasks}
    candidates = []
    for item in parsed.get("items", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            paragraph_id = int(item.get("paragraph_id"))
        except Exception:
            continue
        paragraph = paragraph_map.get(paragraph_id)
        if paragraph is None:
            continue

        for record in normalize_records_from_item(item):
            raw_fields = record.get("fields")
            if not isinstance(raw_fields, dict):
                continue
            fields = {}
            for field_key, raw_payload in raw_fields.items():
                field_task = field_map.get(field_key)
                if field_task is None:
                    continue
                if isinstance(raw_payload, dict):
                    value = raw_payload.get("value")
                    evidence = raw_payload.get("evidence")
                    confidence = raw_payload.get("confidence")
                else:
                    value = raw_payload
                    evidence = raw_payload
                    confidence = 0.86
                validated = validate_row_field(
                    field_task,
                    paragraph,
                    value,
                    evidence,
                    confidence,
                    strict_field_anchor=strict_field_anchor,
                )
                if validated is not None:
                    fields[field_key] = validated
            if fields:
                candidates.append(
                    {
                        "candidate_id": make_row_id(),
                        "paragraph_id": paragraph_id,
                        "paragraph_text": paragraph["text"],
                        "source_kind": paragraph.get("source_kind"),
                        "source_table_id": paragraph.get("source_table_id"),
                        "source_row": paragraph.get("source_row"),
                        "source_col": paragraph.get("source_col"),
                        "source_header": paragraph.get("source_header"),
                        "source_locator": paragraph.get("source_locator"),
                        "fields": fields,
                    }
                )
    return candidates


def candidate_signature(candidate: dict[str, Any]) -> tuple[Any, ...]:
    field_signature = tuple(
        sorted(
            (
                field_key,
                normalize_for_match(cell.get("value")),
            )
            for field_key, cell in (candidate.get("fields") or {}).items()
            if normalize_text(cell.get("value"))
        )
    )
    return (
        candidate.get("source_table_id"),
        candidate.get("source_row"),
        candidate.get("paragraph_id"),
        field_signature,
    )


def candidate_quality_score(candidate: dict[str, Any]) -> float:
    score = 1.2 if candidate.get("source_kind") == "table_row" else 0.0
    for cell in (candidate.get("fields") or {}).values():
        source = cell.get("source") or {}
        score += 1.0 + float(source.get("confidence") or 0.0)
    return score


def merge_unique_row_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {}
    for candidate in candidates:
        if not isinstance(candidate, dict) or not candidate.get("fields"):
            continue
        signature = candidate_signature(candidate)
        existing = merged.get(signature)
        if existing is None or candidate_quality_score(candidate) > candidate_quality_score(existing):
            merged[signature] = candidate
    return sorted(
        merged.values(),
        key=lambda item: (
            item.get("source_table_id") or "",
            item.get("source_row") if item.get("source_row") is not None else 10**9,
            item.get("paragraph_id") if item.get("paragraph_id") is not None else 10**9,
        ),
    )


def process_row_candidate_batch(
    batch_index: int,
    batch: list[dict[str, Any]],
    paragraph_map: dict[int, dict[str, Any]],
    field_tasks: list[dict[str, Any]],
    *,
    client=None,
    model: str = DEFAULT_MODEL,
    strict_field_anchor: bool = True,
) -> tuple[int, list[dict[str, Any]]]:
    if strict_field_anchor and batch and all(paragraph.get("source_kind") == "table_row" for paragraph in batch):
        batch_candidates = fallback_extract_row_candidates(
            field_tasks,
            batch,
            strict_field_anchor=strict_field_anchor,
        )
        if batch_candidates:
            return batch_index, batch_candidates

    llm_client = client
    if client is not None:
        llm_client = getattr(THREAD_LOCAL_STATE, "llm_client", None)
        if llm_client is None:
            llm_client = build_client() or client
            THREAD_LOCAL_STATE.llm_client = llm_client
        parsed = call_llm_json(
            client=llm_client,
            user_content=build_extract_rows_prompt(
                field_tasks,
                batch,
                strict_field_anchor=strict_field_anchor,
            ),
            system_content="你是高精度字段识别助手。先做行内结构化，再交给后续主键归并。弱相关字段一律不要返回，只输出 JSON。",
            model=model,
            temperature=0.0,
        )
        batch_candidates = parse_row_candidates_from_response(
            parsed,
            paragraph_map,
            field_tasks,
            strict_field_anchor=strict_field_anchor,
        )
    else:
        batch_candidates = []

    if not batch_candidates:
        batch_candidates = fallback_extract_row_candidates(
            field_tasks,
            batch,
            strict_field_anchor=strict_field_anchor,
        )
    return batch_index, batch_candidates


def extract_row_candidates(
    field_tasks: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    client=None,
    model: str = DEFAULT_MODEL,
    progress_callback=None,
    strict_field_anchor: bool = True,
) -> list[dict[str, Any]]:
    paragraph_map = {paragraph["paragraph_id"]: paragraph for paragraph in paragraphs}
    batches = iter_paragraph_batches(paragraphs)
    total_batches = len(batches)
    candidates = build_non_table_fragment_candidates(field_tasks, paragraphs) if not strict_field_anchor else []

    if total_batches <= 1 or ROW_PARSE_WORKERS <= 1:
        for batch_index, batch in enumerate(batches, start=1):
            if progress_callback is not None:
                progress_callback(batch_index - 1, total_batches, f"正在识别第 {batch_index}/{total_batches} 批段落")
            _, batch_candidates = process_row_candidate_batch(
                batch_index,
                batch,
                paragraph_map,
                field_tasks,
                client=client,
                model=model,
                strict_field_anchor=strict_field_anchor,
            )
            candidates.extend(batch_candidates)
            if progress_callback is not None:
                progress_callback(batch_index, total_batches, f"已完成第 {batch_index}/{total_batches} 批段落识别")
        return merge_unique_row_candidates(candidates)

    if progress_callback is not None:
        progress_callback(0, total_batches, f"正在并发识别 {total_batches} 批段落")

    batch_results: dict[int, list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=min(ROW_PARSE_WORKERS, total_batches)) as executor:
        future_map = {
            executor.submit(
                process_row_candidate_batch,
                batch_index,
                batch,
                paragraph_map,
                field_tasks,
                client=client,
                model=model,
                strict_field_anchor=strict_field_anchor,
            ): batch_index
            for batch_index, batch in enumerate(batches, start=1)
        }
        completed = 0
        for future in as_completed(future_map):
            batch_index, batch_candidates = future.result()
            batch_results[batch_index] = batch_candidates
            completed += 1
            if progress_callback is not None:
                progress_callback(completed, total_batches, f"已完成第 {completed}/{total_batches} 批段落识别")

    for batch_index in range(1, total_batches + 1):
        candidates.extend(batch_results.get(batch_index, []))

    return merge_unique_row_candidates(candidates)


def build_empty_row(field_tasks: list[dict[str, Any]]) -> dict[str, Any]:
    row = {
        "__row_id__": make_row_id(),
        "__key_fields__": [],
        "__candidate_ids__": [],
        "__field_options__": {},
        "__decision_trace__": {},
    }
    for field in field_tasks:
        row[field["name"]] = {
            "label": field["field_name"],
            "value": None,
            "raw": None,
            "number": None,
            "unit": None,
            "normalized": None,
            "source": None,
            "matched_phrase": None,
            "match_hint": None,
            "decision": None,
        }
    return row


def set_cell_from_candidate(row: dict[str, Any], field_task: dict[str, Any], candidate: dict[str, Any]) -> None:
    cell = candidate.get("fields", {}).get(field_task["name"]) or {}
    row[field_task["name"]] = {
        "label": field_task["field_name"],
        "value": normalize_text(cell.get("value")) or None,
        "raw": normalize_text(cell.get("raw")) or None,
        "number": normalize_text(cell.get("number")) or None,
        "unit": normalize_text(cell.get("unit")) or None,
        "normalized": normalize_text(cell.get("normalized")) or normalize_text(cell.get("value")) or None,
        "source": copy.deepcopy(cell.get("source")),
        "matched_phrase": normalize_text(cell.get("matched_phrase") or ((cell.get("source") or {}).get("evidence"))) or None,
        "match_hint": copy.deepcopy(cell.get("match_hint")) if isinstance(cell.get("match_hint"), dict) else None,
        "decision": copy.deepcopy(cell.get("decision")) if isinstance(cell.get("decision"), dict) else None,
    }
    candidate_ids = row.setdefault("__candidate_ids__", [])
    candidate_id = candidate.get("candidate_id")
    if candidate_id and candidate_id not in candidate_ids:
        candidate_ids.append(candidate_id)


def backfill_unit_fields_from_value(row: dict[str, Any], field_tasks: list[dict[str, Any]]) -> None:
    unit_fields = [field for field in field_tasks if field.get("slot") == "unit"]
    if not unit_fields:
        return
    if any(normalize_text((row.get(field["name"]) or {}).get("value")) for field in unit_fields):
        return

    source_cell = None
    for slot in ("value", "yoy"):
        for field in field_tasks:
            if field.get("slot") != slot:
                continue
            cell = row.get(field["name"]) or {}
            if normalize_text(cell.get("unit")):
                source_cell = cell
                break
        if source_cell is not None:
            break
    if source_cell is None:
        return

    unit_value = normalize_text(source_cell.get("unit"))
    if not unit_value:
        return
    for unit_field in unit_fields:
        row[unit_field["name"]] = {
            "label": unit_field["field_name"],
            "value": unit_value,
            "raw": unit_value,
            "number": None,
            "unit": unit_value,
            "normalized": unit_value,
            "source": copy.deepcopy(source_cell.get("source")),
            "matched_phrase": unit_value,
            "match_hint": copy.deepcopy(source_cell.get("match_hint")) if isinstance(source_cell.get("match_hint"), dict) else None,
            "decision": copy.deepcopy(source_cell.get("decision")) if isinstance(source_cell.get("decision"), dict) else None,
        }


def backfill_context_fields_from_source(row: dict[str, Any], field_tasks: list[dict[str, Any]]) -> None:
    source_cell = None
    for field in field_tasks:
        cell = row.get(field["name"]) or {}
        source = cell.get("source")
        if isinstance(source, dict) and normalize_text(source.get("paragraph_text")):
            source_cell = cell
            break
    if source_cell is None:
        return

    source = copy.deepcopy(source_cell.get("source")) or {}
    paragraph_text = normalize_text(source.get("paragraph_text"))
    if not paragraph_text:
        return

    category_value = find_relevant_category(paragraph_text)
    time_value = extract_first_date(paragraph_text)

    for field in field_tasks:
        slot = normalize_text(field.get("slot"))
        cell = row.get(field["name"]) or {}
        if normalize_text(cell.get("value")):
            continue
        if slot == "category" and category_value:
            row[field["name"]] = {
                "label": field["field_name"],
                "value": category_value,
                "raw": category_value,
                "number": None,
                "unit": None,
                "normalized": category_value,
                "source": source,
                "matched_phrase": category_value,
                "match_hint": copy.deepcopy(source_cell.get("match_hint")) if isinstance(source_cell.get("match_hint"), dict) else None,
                "decision": copy.deepcopy(source_cell.get("decision")) if isinstance(source_cell.get("decision"), dict) else None,
            }
        if slot == "time" and time_value:
            row[field["name"]] = {
                "label": field["field_name"],
                "value": time_value,
                "raw": time_value,
                "number": None,
                "unit": None,
                "normalized": time_value,
                "source": source,
                "matched_phrase": time_value,
                "match_hint": copy.deepcopy(source_cell.get("match_hint")) if isinstance(source_cell.get("match_hint"), dict) else None,
                "decision": copy.deepcopy(source_cell.get("decision")) if isinstance(source_cell.get("decision"), dict) else None,
            }


def get_row_key_snapshot(row: dict[str, Any], key_fields: list[str]) -> dict[str, str]:
    snapshot = {}
    for key in key_fields:
        cell = row.get(key, {})
        value = normalize_text(cell.get("value"))
        if value:
            snapshot[key] = value
    return snapshot


def get_candidate_key_snapshot(candidate: dict[str, Any], key_fields: list[str]) -> dict[str, str] | None:
    snapshot = {}
    for key in key_fields:
        cell = candidate.get("fields", {}).get(key) or {}
        value = normalize_text(cell.get("value"))
        if not value:
            return None
        snapshot[key] = value
    return snapshot


def get_row_key_sources(row: dict[str, Any], key_fields: list[str]) -> list[dict[str, Any]]:
    sources = []
    for key in key_fields:
        cell = row.get(key, {})
        source = cell.get("source")
        if isinstance(source, dict):
            sources.append(source)
    return sources


def build_row_source_indexes(rows: list[dict[str, Any]], key_fields: list[str]) -> dict[str, dict[Any, list[dict[str, Any]]]]:
    by_table_row = {}
    by_paragraph = {}
    for row in rows:
        for source in get_row_key_sources(row, key_fields):
            table_key = (source.get("source_table_id"), source.get("source_row"))
            if table_key[0] and table_key[1] is not None:
                by_table_row.setdefault(table_key, []).append(row)
            paragraph_id = source.get("paragraph_id")
            if paragraph_id is not None:
                by_paragraph.setdefault(paragraph_id, []).append(row)
    return {"by_table_row": by_table_row, "by_paragraph": by_paragraph}


def build_first_column_rows(
    field_task: dict[str, Any],
    row_candidates: list[dict[str, Any]],
    field_tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seeded: dict[str, dict[str, Any]] = {}
    ordered_signatures: list[str] = []
    fallback_rows = []
    require_companion_value = normalize_text(field_task.get("slot")) == "category" and len(field_tasks) > 1
    for candidate in row_candidates:
        candidate_fields = candidate.get("fields") or {}
        cell = (candidate.get("fields") or {}).get(field_task["name"]) or {}
        value = normalize_text(cell.get("value"))
        if not value:
            continue
        if require_companion_value:
            companion_count = sum(
                1
                for field in field_tasks
                if field["name"] != field_task["name"]
                and normalize_text((candidate_fields.get(field["name"]) or {}).get("value"))
            )
            if companion_count <= 0:
                continue
        if not is_preferred_seed_value(field_task, value):
            row = build_empty_row(field_tasks)
            set_cell_from_candidate(row, field_task, candidate)
            row["__key_fields__"] = [field_task["name"]]
            fallback_rows.append(row)
            continue

        if field_prefers_metric_literal(field_task):
            parsed = parse_number_unit(value)
            signature = parsed.get("number") or parsed.get("normalized") or value
        else:
            signature = value
        signature = normalize_for_match(signature or value)
        if not signature:
            continue

        current_score = score_seed_cell(field_task, cell)
        existing = seeded.get(signature)
        if existing is None:
            row = build_empty_row(field_tasks)
            set_cell_from_candidate(row, field_task, candidate)
            row["__key_fields__"] = [field_task["name"]]
            row["__seed_score__"] = current_score
            seeded[signature] = row
            ordered_signatures.append(signature)
            continue

        if current_score > float(existing.get("__seed_score__") or 0.0):
            seed_ids = list(existing.get("__candidate_ids__", []))
            row = build_empty_row(field_tasks)
            set_cell_from_candidate(row, field_task, candidate)
            row["__candidate_ids__"] = unique_strings([*seed_ids, *row.get("__candidate_ids__", [])])
            row["__key_fields__"] = [field_task["name"]]
            row["__seed_score__"] = current_score
            seeded[signature] = row
            continue

        candidate_id = candidate.get("candidate_id")
        if candidate_id and candidate_id not in existing.get("__candidate_ids__", []):
            existing.setdefault("__candidate_ids__", []).append(candidate_id)
    rows = [seeded[signature] for signature in ordered_signatures]
    if rows:
        for row in rows:
            row.pop("__seed_score__", None)
        return rows
    for row in fallback_rows:
        row.pop("__seed_score__", None)
    return fallback_rows


def choose_seed_field(
    field_tasks: list[dict[str, Any]],
    row_candidates: list[dict[str, Any]],
    *,
    strict_seed_field: bool = True,
) -> dict[str, Any] | None:
    if not field_tasks:
        return None
    if strict_seed_field:
        return field_tasks[0]

    candidate_counts = {field["name"]: 0 for field in field_tasks}
    for candidate in row_candidates:
        candidate_fields = candidate.get("fields") or {}
        for field in field_tasks:
            if field["name"] in candidate_fields:
                candidate_counts[field["name"]] += 1

    ranked = sorted(
        field_tasks,
        key=lambda field: (
            0 if candidate_counts.get(field["name"], 0) > 0 else 1,
            -candidate_counts.get(field["name"], 0),
            SEED_FIELD_SLOT_PRIORITY.get(field.get("slot"), 99),
            field.get("column_index", 10**9),
        ),
    )
    return ranked[0] if ranked else field_tasks[0]


def compare_key_values(left: str, right: str) -> float:
    left_text = normalize_text(left)
    right_text = normalize_text(right)
    if not left_text or not right_text:
        return 0.0

    left_norm = normalize_for_match(left_text)
    right_norm = normalize_for_match(right_text)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 3.5

    left_number = parse_number_unit(left_text).get("normalized")
    right_number = parse_number_unit(right_text).get("normalized")
    if left_number and right_number and normalize_for_match(left_number) == normalize_for_match(right_number):
        return 3.0

    if len(left_norm) >= 2 and len(right_norm) >= 2 and (left_norm in right_norm or right_norm in left_norm):
        return 2.0

    if SequenceMatcher(a=left_norm, b=right_norm).ratio() >= 0.94:
        return 1.2

    return 0.0


def get_known_row_key_fields(
    row: dict[str, Any],
    field_tasks: list[dict[str, Any]],
    exclude_field_key: str | None = None,
) -> list[str]:
    keys = []
    for field in field_tasks:
        field_key = field["name"]
        if exclude_field_key and field_key == exclude_field_key:
            continue
        cell = row.get(field_key) or {}
        if normalize_text(cell.get("value")):
            keys.append(field_key)
    return keys


def refresh_row_key_fields(row: dict[str, Any], field_tasks: list[dict[str, Any]]) -> None:
    row["__key_fields__"] = get_known_row_key_fields(row, field_tasks)


def build_labeled_key_snapshot(
    values: dict[str, str],
    field_map: dict[str, dict[str, Any]],
) -> dict[str, str]:
    snapshot = {}
    for field_key, value in values.items():
        field = field_map.get(field_key)
        if field is None or not normalize_text(value):
            continue
        snapshot[field["field_name"]] = normalize_text(value)
    return snapshot


def get_partial_candidate_key_snapshot(candidate: dict[str, Any], key_fields: list[str]) -> dict[str, str]:
    snapshot = {}
    for key in key_fields:
        cell = candidate.get("fields", {}).get(key) or {}
        value = normalize_text(cell.get("value"))
        if value:
            snapshot[key] = value
    return snapshot


def build_option_id(candidate: dict[str, Any], field_key: str) -> str:
    return f"{candidate.get('candidate_id') or make_row_id()}:{field_key}"


def get_option_value_signature(option: dict[str, Any]) -> str:
    normalized_value = normalize_text(option.get("normalized"))
    if normalized_value:
        parsed = parse_number_unit(normalized_value)
        if normalize_text(parsed.get("normalized")):
            return normalize_for_match(parsed.get("normalized"))
        return normalize_for_match(normalized_value)

    raw_value = normalize_text(option.get("value"))
    if not raw_value:
        return ""
    parsed = parse_number_unit(raw_value)
    if normalize_text(parsed.get("normalized")):
        return normalize_for_match(parsed.get("normalized"))
    return normalize_for_match(raw_value)


def build_option_signature(option: dict[str, Any]) -> tuple[Any, ...]:
    source = option.get("source") or {}
    return (
        get_option_value_signature(option),
        source.get("source_table_id"),
        source.get("source_row"),
        source.get("paragraph_id"),
    )


def build_field_option_from_candidate(
    row: dict[str, Any],
    field_task: dict[str, Any],
    candidate: dict[str, Any],
    field_tasks: list[dict[str, Any]],
    field_map: dict[str, dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    field_matcher: FieldAssistMatcher | None,
    heuristic_score: float,
) -> dict[str, Any] | None:
    cell = candidate.get("fields", {}).get(field_task["name"]) or {}
    value = normalize_text(cell.get("value"))
    if not value:
        return None

    source = build_source_payload(cell.get("source"))
    matched_phrase = normalize_text(cell.get("matched_phrase") or ((source or {}).get("evidence")) or value)
    match_hint = copy.deepcopy(cell.get("match_hint")) if isinstance(cell.get("match_hint"), dict) else None
    if match_hint is None and field_matcher is not None:
        match_hint = field_matcher.summarize(matched_phrase or value, field_task["name"])

    key_fields = get_known_row_key_fields(row, field_tasks, exclude_field_key=field_task["name"])
    key_snapshot = build_labeled_key_snapshot(get_row_key_snapshot(row, key_fields), field_map)
    candidate_key_snapshot = build_labeled_key_snapshot(get_partial_candidate_key_snapshot(candidate, key_fields), field_map)
    option = {
        "option_id": build_option_id(candidate, field_task["name"]),
        "candidate_id": candidate.get("candidate_id"),
        "field_key": field_task["name"],
        "field_name": field_task["field_name"],
        "value": value,
        "raw": normalize_text(cell.get("raw")) or value,
        "number": normalize_text(cell.get("number")) or None,
        "unit": normalize_text(cell.get("unit")) or None,
        "normalized": normalize_text(cell.get("normalized")) or value,
        "matched_phrase": matched_phrase or None,
        "paragraph_id": candidate.get("paragraph_id"),
        "paragraph_text": normalize_text(candidate.get("paragraph_text")),
        "context_window": collect_context_window(paragraphs, candidate.get("paragraph_id"), window=ROW_MATCH_CONTEXT_WINDOW),
        "source": source,
        "source_kind": candidate.get("source_kind"),
        "source_table_id": candidate.get("source_table_id"),
        "source_row": candidate.get("source_row"),
        "source_col": candidate.get("source_col"),
        "source_header": candidate.get("source_header"),
        "source_locator": candidate.get("source_locator"),
        "heuristic_score": round(float(heuristic_score), 4),
        "match_hint": match_hint,
        "row_key_snapshot": key_snapshot,
        "candidate_key_snapshot": candidate_key_snapshot,
    }
    return option


def score_field_candidate_for_row(
    row: dict[str, Any],
    field_task: dict[str, Any],
    candidate: dict[str, Any],
    field_tasks: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    field_matcher: FieldAssistMatcher | None,
) -> float:
    cell = candidate.get("fields", {}).get(field_task["name"]) or {}
    value = normalize_text(cell.get("value"))
    if not value:
        return -1.0

    key_fields = get_known_row_key_fields(row, field_tasks, exclude_field_key=field_task["name"])
    row_snapshot = get_row_key_snapshot(row, key_fields)
    candidate_snapshot = get_partial_candidate_key_snapshot(candidate, key_fields)
    source = cell.get("source") or {}
    paragraph_id = candidate.get("paragraph_id")
    context_window = collect_context_window(paragraphs, paragraph_id, window=ROW_MATCH_CONTEXT_WINDOW)
    normalized_context = normalize_for_match(f"{context_window} {candidate.get('paragraph_text') or ''}")
    ambiguous_context = has_multiple_category_entities(candidate.get("paragraph_text"))

    score = 0.4 + float(source.get("confidence") or 0.0)
    direct_key_matches = 0
    context_key_matches = 0
    same_table_row = False

    if candidate.get("source_kind") == "table_row":
        score += 1.4

    for key, row_value in row_snapshot.items():
        if key in candidate_snapshot:
            relation = compare_key_values(row_value, candidate_snapshot[key])
            if relation <= 0:
                return -1.0
            score += relation + 0.6
            direct_key_matches += 1
            continue

        normalized_row_value = normalize_for_match(row_value)
        if not ambiguous_context and normalized_row_value and normalized_row_value in normalized_context:
            score += 2.1
            context_key_matches += 1
            continue
        if not is_generic_row_key_value(row_value):
            return -1.0

    for key_source in get_row_key_sources(row, key_fields):
        if not isinstance(key_source, dict):
            continue
        if candidate.get("source_table_id") and key_source.get("source_table_id") == candidate.get("source_table_id"):
            score += 1.0
            if candidate.get("source_row") is not None and key_source.get("source_row") == candidate.get("source_row"):
                score += 4.6
                same_table_row = True
        if paragraph_id is not None and key_source.get("paragraph_id") == paragraph_id:
            score += 2.8

    matched_phrase = normalize_text(cell.get("matched_phrase") or source.get("evidence") or value)
    match_hint = copy.deepcopy(cell.get("match_hint")) if isinstance(cell.get("match_hint"), dict) else None
    if match_hint is None and field_matcher is not None:
        match_hint = field_matcher.summarize(matched_phrase or value, field_task["name"])
    if match_hint is not None:
        cell["match_hint"] = match_hint
        score += float(match_hint.get("target_score") or 0.0) * 1.5
        if match_hint.get("is_target_top"):
            score += 0.5
        elif float(match_hint.get("target_score") or 0.0) < 0.45:
            score -= 0.7

    if key_fields and direct_key_matches <= 0 and context_key_matches <= 0 and not same_table_row:
        score -= 1.6

    if candidate.get("source_kind") == "table_row" and key_fields and not same_table_row and direct_key_matches <= 0:
        score -= 1.2

    return round(score, 4)


def collect_row_field_options(
    row: dict[str, Any],
    field_task: dict[str, Any],
    field_candidates: list[dict[str, Any]],
    field_tasks: list[dict[str, Any]],
    field_map: dict[str, dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    field_matcher: FieldAssistMatcher | None,
    used_candidate_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    used_candidate_ids = used_candidate_ids or set()
    options_by_signature: dict[tuple[Any, ...], dict[str, Any]] = {}

    for candidate in field_candidates:
        candidate_id = candidate.get("candidate_id")
        if candidate_id and candidate_id in used_candidate_ids:
            continue
        heuristic_score = score_field_candidate_for_row(
            row,
            field_task,
            candidate,
            field_tasks,
            paragraphs,
            field_matcher,
        )
        if heuristic_score < MIN_FIELD_OPTION_SCORE:
            continue
        option = build_field_option_from_candidate(
            row,
            field_task,
            candidate,
            field_tasks,
            field_map,
            paragraphs,
            field_matcher,
            heuristic_score,
        )
        if option is None:
            continue
        signature = build_option_signature(option)
        existing = options_by_signature.get(signature)
        if existing is None or float(option["heuristic_score"]) > float(existing["heuristic_score"]):
            options_by_signature[signature] = option

    options = sorted(
        options_by_signature.values(),
        key=lambda item: (
            0 if item.get("source_kind") == "table_row" else 1,
            -float(item.get("heuristic_score") or 0.0),
            item.get("paragraph_id") if item.get("paragraph_id") is not None else 10**9,
        ),
    )
    return options[:MAX_FIELD_OPTIONS]


def build_row_field_decision_prompt(
    row: dict[str, Any],
    field_task: dict[str, Any],
    options: list[dict[str, Any]],
    field_tasks: list[dict[str, Any]],
    field_map: dict[str, dict[str, Any]],
) -> str:
    key_fields = get_known_row_key_fields(row, field_tasks, exclude_field_key=field_task["name"])
    key_snapshot = build_labeled_key_snapshot(get_row_key_snapshot(row, key_fields), field_map)
    key_sources = [
        build_source_payload(source)
        for source in get_row_key_sources(row, key_fields)
        if isinstance(source, dict)
    ]
    payload = {
        "task": "判断当前列应该从哪些候选值中选择一个填入当前行。",
        "requirements": [
            "字段语义判断以 field 为准，match_hint 只作辅助参考，不能替代最终判断。",
            "必须同时判断：候选值是否真的是当前字段；候选值是否属于当前行已知主键描述的对象/地区/时间/类别。",
            "如果候选项来自表格，且与当前行来源同 table_id/source_row，应优先考虑。",
            "如果只能弱匹配，可以选择一个低置信度候选；完全不能确认时返回 NONE。",
            "alternatives 最多返回 3 个 option_id，按可信度从高到低。",
            "只输出 JSON。",
        ],
        "field": {
            "field_name": field_task["field_name"],
            "key": field_task["name"],
            "slot": field_task.get("slot"),
            "aliases": field_task.get("aliases", []),
            "description": field_task.get("description", ""),
        },
        "current_row": {
            "row_id": row.get("__row_id__"),
            "known_keys": key_snapshot,
            "key_sources": key_sources,
        },
        "candidate_options": [
            {
                "option_id": option["option_id"],
                "value": option.get("value"),
                "matched_phrase": option.get("matched_phrase"),
                "heuristic_score": option.get("heuristic_score"),
                "match_hint": option.get("match_hint"),
                "candidate_key_snapshot": option.get("candidate_key_snapshot"),
                "paragraph_id": option.get("paragraph_id"),
                "paragraph_text": option.get("paragraph_text"),
                "context_window": option.get("context_window"),
                "source": option.get("source"),
            }
            for option in options
        ],
        "output_schema": {
            "selected_option_id": "option_id or NONE",
            "alternatives": ["option_id"],
            "confidence": 0.0,
            "reason": "简短原因",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def decide_field_option_for_row(
    row: dict[str, Any],
    field_task: dict[str, Any],
    options: list[dict[str, Any]],
    field_tasks: list[dict[str, Any]],
    field_map: dict[str, dict[str, Any]],
    client=None,
    model: str = DEFAULT_MODEL,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not options:
        return None, {
            "mode": "empty",
            "selected_option_id": None,
            "alternatives": [],
            "confidence": 0.0,
            "reason": "当前列没有可用候选",
        }

    best = options[0]
    alternatives = [item["option_id"] for item in options[1:4]]
    if len(options) == 1 and float(best.get("heuristic_score") or 0.0) >= AUTO_SELECT_ROW_FIELD_SCORE:
        return best, {
            "mode": "auto_exact",
            "selected_option_id": best["option_id"],
            "alternatives": alternatives,
            "confidence": round(min(0.99, 0.7 + float(best.get("heuristic_score")) / 12.0), 4),
            "reason": "单个候选且与主键/来源强一致，直接采用",
        }
    if option_is_strong_exact_match(row, field_task, best, field_tasks):
        return best, {
            "mode": "auto_strong",
            "selected_option_id": best["option_id"],
            "alternatives": alternatives,
            "confidence": round(min(0.99, 0.74 + float(best.get("heuristic_score")) / 15.0), 4),
            "reason": "候选与当前行动态主键完全一致，且字段语义命中强，直接采用",
        }

    best_score = float(best.get("heuristic_score") or 0.0)
    second_score = float(options[1].get("heuristic_score") or 0.0) if len(options) > 1 else 0.0
    score_gap = best_score - second_score if len(options) > 1 else best_score
    best_target_score = float((best.get("match_hint") or {}).get("target_score") or 0.0)
    safe_fallback = option_has_safe_fallback(row, best)

    if best_score < 2.4 and score_gap < 0.8:
        return None, {
            "mode": "auto_weak_none",
            "selected_option_id": None,
            "alternatives": [item["option_id"] for item in options[:3]],
            "confidence": 0.0,
            "reason": "候选仅弱相关且缺少稳定主键信号，直接跳过",
        }

    if not safe_fallback and float(best.get("heuristic_score") or 0.0) < AUTO_SELECT_ROW_FIELD_SCORE:
        return None, {
            "mode": "auto_key_guard_none",
            "selected_option_id": None,
            "alternatives": [item["option_id"] for item in options[:3]],
            "confidence": 0.0,
            "reason": "候选未能同时命中当前行的稳定主键，避免误填",
        }

    if safe_fallback and (best_score >= AUTO_SELECT_ROW_FIELD_SCORE or score_gap >= 1.4 or (best_score >= 5.0 and best_target_score >= 0.92)):
        return best, {
            "mode": "fallback",
            "selected_option_id": best["option_id"],
            "alternatives": alternatives,
            "confidence": round(min(0.94, 0.64 + max(score_gap, 0.0) / 6.0), 4),
            "reason": "启发式最佳候选明显领先，无需额外 LLM 决策",
        }

    if client is not None:
        parsed = call_llm_json(
            client=client,
            user_content=build_row_field_decision_prompt(row, field_task, options, field_tasks, field_map),
            system_content="你是字段填充决策助手。match 只作辅助，最终必须依据字段语义和当前行主键判断是否可填，只输出 JSON。",
            model=model,
            temperature=0.0,
        )
        selected_option_id = normalize_text(parsed.get("selected_option_id"))
        returned_alternatives = [
            normalize_text(item)
            for item in (parsed.get("alternatives") or [])
            if normalize_text(item)
        ][:3]
        try:
            confidence = float(parsed.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        reason = normalize_text(parsed.get("reason")) or "LLM 未给出明确理由"
        if selected_option_id and selected_option_id.upper() != "NONE" and confidence >= MIN_ROW_FIELD_DECISION_CONFIDENCE:
            for option in options:
                if option["option_id"] == selected_option_id:
                    return option, {
                        "mode": "llm",
                        "selected_option_id": selected_option_id,
                        "alternatives": returned_alternatives,
                "confidence": round(confidence, 4),
                "reason": reason,
            }
        if selected_option_id.upper() == "NONE":
            return None, {
                "mode": "llm_none",
                "selected_option_id": None,
                "alternatives": returned_alternatives,
                "confidence": round(confidence, 4),
                "reason": reason,
            }

    if safe_fallback and (float(best.get("heuristic_score") or 0.0) >= AUTO_SELECT_ROW_FIELD_SCORE or score_gap >= 1.4):
        return best, {
            "mode": "fallback",
            "selected_option_id": best["option_id"],
            "alternatives": alternatives,
            "confidence": round(min(0.92, 0.62 + max(score_gap, 0.0) / 6.0), 4),
            "reason": "启发式最佳候选明显领先，采用回退决策",
        }

    return None, {
        "mode": "fallback_none",
        "selected_option_id": None,
        "alternatives": [item["option_id"] for item in options[:3]],
        "confidence": 0.0,
        "reason": "候选存在但主键联系不足，保留候选不自动填充",
    }


def option_matches_row_snapshot(option: dict[str, Any], row_snapshot: dict[str, str]) -> bool:
    if not row_snapshot:
        return False

    candidate_snapshot = option.get("candidate_key_snapshot") or {}
    direct_matches = 0
    for label, row_value in row_snapshot.items():
        candidate_value = candidate_snapshot.get(label)
        if not candidate_value:
            continue
        if compare_key_values(row_value, candidate_value) <= 0:
            return False
        direct_matches += 1
    if direct_matches > 0:
        unmatched_specific_values = [
            row_value
            for label, row_value in row_snapshot.items()
            if label not in candidate_snapshot and not is_generic_row_key_value(row_value)
        ]
        if not unmatched_specific_values:
            return True
        if has_multiple_category_entities(option.get("paragraph_text")):
            return False
        context_text = normalize_for_match(f"{option.get('context_window') or ''} {option.get('paragraph_text') or ''}")
        return all(normalize_for_match(value) in context_text for value in unmatched_specific_values if normalize_for_match(value))

    if has_multiple_category_entities(option.get("paragraph_text")):
        return False

    context_text = normalize_for_match(f"{option.get('context_window') or ''} {option.get('paragraph_text') or ''}")
    if not context_text:
        return False
    return all(normalize_for_match(value) in context_text for value in row_snapshot.values() if normalize_for_match(value))


def is_generic_row_key_value(value: Any) -> bool:
    normalized = normalize_text(value)
    if not normalized:
        return True
    return normalized in {"全国", "全市", "全省", "全县", "全区", "全行业", "全系统", "整体", "平均"}


def summarize_option_row_support(option: dict[str, Any]) -> dict[str, int]:
    row_snapshot = option.get("row_key_snapshot") or {}
    candidate_snapshot = option.get("candidate_key_snapshot") or {}
    context_text = normalize_for_match(f"{option.get('context_window') or ''} {option.get('paragraph_text') or ''}")
    allow_context_fallback = not has_multiple_category_entities(option.get("paragraph_text"))
    matched = 0
    specific_matched = 0

    for label, row_value in row_snapshot.items():
        matched_flag = False
        candidate_value = candidate_snapshot.get(label)
        if candidate_value and compare_key_values(row_value, candidate_value) > 0:
            matched_flag = True
        elif allow_context_fallback:
            normalized_row_value = normalize_for_match(row_value)
            if normalized_row_value and normalized_row_value in context_text:
                matched_flag = True
        if not matched_flag:
            continue
        matched += 1
        if not is_generic_row_key_value(row_value):
            specific_matched += 1

    return {
        "known": len(row_snapshot),
        "matched": matched,
        "specific_matched": specific_matched,
    }


def option_has_safe_fallback(row: dict[str, Any], option: dict[str, Any]) -> bool:
    if option.get("candidate_id") in row.get("__candidate_ids__", []):
        return True

    support = summarize_option_row_support(option)
    if support["known"] <= 0:
        return False
    if support["known"] == 1:
        return support["matched"] >= 1
    return support["matched"] >= 2 and support["specific_matched"] >= 1


def collect_additional_field_options(
    row: dict[str, Any],
    field_task: dict[str, Any],
    selected_option: dict[str, Any] | None,
    options: list[dict[str, Any]],
    field_tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if selected_option is None or not options or not field_allows_multi_value_clone(field_task):
        return []

    key_fields = get_known_row_key_fields(row, field_tasks, exclude_field_key=field_task["name"])
    field_map = {field["name"]: field for field in field_tasks}
    row_snapshot = build_labeled_key_snapshot(get_row_key_snapshot(row, key_fields), field_map)
    if not row_snapshot:
        return []

    selected_value = get_option_value_signature(selected_option)
    selected_score = float(selected_option.get("heuristic_score") or 0.0)
    extras = []
    seen_values = {selected_value}
    for option in options:
        if option.get("option_id") == selected_option.get("option_id"):
            continue
        option_value = get_option_value_signature(option)
        if not option_value or option_value in seen_values:
            continue
        option_score = float(option.get("heuristic_score") or 0.0)
        option_target_score = float((option.get("match_hint") or {}).get("target_score") or 0.0)
        if option_score < MULTI_VALUE_MIN_SCORE and option_target_score < 0.92:
            continue
        if option_target_score < 0.95 and selected_score - option_score > MULTI_VALUE_OPTION_SCORE_GAP:
            continue
        if not option_matches_row_snapshot(option, row_snapshot):
            continue
        seen_values.add(option_value)
        extras.append(option)
    return extras


def option_is_strong_exact_match(
    row: dict[str, Any],
    field_task: dict[str, Any],
    option: dict[str, Any],
    field_tasks: list[dict[str, Any]],
) -> bool:
    if float(option.get("heuristic_score") or 0.0) < AUTO_SELECT_ROW_FIELD_SCORE:
        return False
    match_hint = option.get("match_hint") or {}
    if float(match_hint.get("target_score") or 0.0) < 0.95:
        return False
    key_fields = get_known_row_key_fields(row, field_tasks, exclude_field_key=field_task["name"])
    field_map = {field["name"]: field for field in field_tasks}
    row_snapshot = build_labeled_key_snapshot(get_row_key_snapshot(row, key_fields), field_map)
    if not row_snapshot:
        return True
    return option_matches_row_snapshot(option, row_snapshot)


def clone_row_with_selected_option(
    row: dict[str, Any],
    field_task: dict[str, Any],
    option: dict[str, Any],
    decision: dict[str, Any],
    field_tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    cloned = copy.deepcopy(row)
    cloned["__row_id__"] = make_row_id()
    apply_selected_option_to_row(cloned, field_task, option, decision, field_tasks)
    return cloned


def store_row_field_decision(
    row: dict[str, Any],
    field_task: dict[str, Any],
    options: list[dict[str, Any]],
    decision: dict[str, Any],
) -> None:
    field_key = field_task["name"]
    selected_option_id = decision.get("selected_option_id")
    public_options = []
    for option in options:
        public_options.append(
            {
                "option_id": option.get("option_id"),
                "candidate_id": option.get("candidate_id"),
                "value": option.get("value"),
                "raw": option.get("raw"),
                "matched_phrase": option.get("matched_phrase"),
                "heuristic_score": option.get("heuristic_score"),
                "match_hint": option.get("match_hint"),
                "paragraph_id": option.get("paragraph_id"),
                "paragraph_text": option.get("paragraph_text"),
                "context_window": option.get("context_window"),
                "source": option.get("source"),
                "row_key_snapshot": option.get("row_key_snapshot"),
                "candidate_key_snapshot": option.get("candidate_key_snapshot"),
                "selected": option.get("option_id") == selected_option_id,
            }
        )
    row.setdefault("__field_options__", {})[field_key] = public_options
    row.setdefault("__decision_trace__", {})[field_key] = {
        "mode": decision.get("mode"),
        "selected_option_id": selected_option_id,
        "alternatives": decision.get("alternatives") or [],
        "confidence": float(decision.get("confidence") or 0.0),
        "reason": normalize_text(decision.get("reason")),
    }


def apply_selected_option_to_row(
    row: dict[str, Any],
    field_task: dict[str, Any],
    option: dict[str, Any],
    decision: dict[str, Any],
    field_tasks: list[dict[str, Any]],
) -> None:
    field_key = field_task["name"]
    row[field_key] = {
        "label": field_task["field_name"],
        "value": normalize_text(option.get("value")) or None,
        "raw": normalize_text(option.get("raw")) or None,
        "number": normalize_text(option.get("number")) or None,
        "unit": normalize_text(option.get("unit")) or None,
        "normalized": normalize_text(option.get("normalized")) or normalize_text(option.get("value")) or None,
        "source": copy.deepcopy(option.get("source")),
        "matched_phrase": normalize_text(option.get("matched_phrase")) or None,
        "match_hint": copy.deepcopy(option.get("match_hint")) if isinstance(option.get("match_hint"), dict) else None,
        "decision": {
            "mode": decision.get("mode"),
            "confidence": float(decision.get("confidence") or 0.0),
            "reason": normalize_text(decision.get("reason")),
            "selected_option_id": option.get("option_id"),
        },
    }
    candidate_ids = row.setdefault("__candidate_ids__", [])
    candidate_id = option.get("candidate_id")
    if candidate_id and candidate_id not in candidate_ids:
        candidate_ids.append(candidate_id)
    refresh_row_key_fields(row, field_tasks)


def build_row_iteration_order(
    rows: list[dict[str, Any]],
    field_tasks: list[dict[str, Any]],
    current_field_key: str,
) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -len(get_known_row_key_fields(row, field_tasks, exclude_field_key=current_field_key)),
            row.get("__row_id__") or "",
        ),
    )


def build_match_prompt(
    field_task: dict[str, Any],
    candidate: dict[str, Any],
    candidate_rows: list[dict[str, Any]],
    field_map: dict[str, dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    key_fields: list[str],
) -> str:
    key_snapshot = get_candidate_key_snapshot(candidate, key_fields) or {}
    current_cell = candidate.get("fields", {}).get(field_task["name"]) or {}
    current_source = current_cell.get("source") or {}

    payload = {
        "task": "判断当前字段值应该并入哪一行已有记录。",
        "requirements": [
            "只能在 candidate_rows 里选择一个 row_id，或者返回 NONE。",
            "必须优先依据已确认主键字段是否一致来判断。",
            "如果主键字段不完整、存在冲突、或者无法稳定定位，就返回 NONE。",
            "不要新建记录，不要补字段，不要输出默认值。",
            "只输出 JSON。",
        ],
        "field": {
            "field_name": field_task["field_name"],
            "key": field_task["name"],
            "slot": field_task.get("slot"),
            "aliases": field_task.get("aliases", []),
            "description": field_task.get("description", ""),
        },
        "candidate": {
            "key_fields": {
                field_map[key]["field_name"]: value
                for key, value in key_snapshot.items()
                if key in field_map
            },
            "field_value": current_cell.get("value"),
            "paragraph_id": candidate.get("paragraph_id"),
            "context_window": collect_context_window(paragraphs, candidate.get("paragraph_id"), window=ROW_MATCH_CONTEXT_WINDOW),
            "source_table_id": current_source.get("source_table_id"),
            "source_row": current_source.get("source_row"),
        },
        "candidate_rows": [
            {
                "row_id": row["__row_id__"],
                "key_fields": {
                    field_map[key]["field_name"]: value
                    for key, value in get_row_key_snapshot(row, key_fields).items()
                    if key in field_map
                },
                "key_sources": get_row_key_sources(row, key_fields),
            }
            for row in candidate_rows
        ],
        "output_schema": {"row_id": "候选 row_id 或 NONE", "reason": "简短原因", "confidence": 0.0},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def score_row_for_candidate(
    row: dict[str, Any],
    candidate: dict[str, Any],
    key_fields: list[str],
    context_window: str,
) -> float:
    candidate_snapshot = get_candidate_key_snapshot(candidate, key_fields)
    row_snapshot = get_row_key_snapshot(row, key_fields)
    if not row_snapshot:
        return -1.0

    normalized_window = normalize_for_match(context_window)
    score = 0.0
    if candidate.get("candidate_id") in row.get("__candidate_ids__", []):
        score += 6.0

    for source in get_row_key_sources(row, key_fields):
        if candidate.get("source_table_id") and source.get("source_table_id") == candidate.get("source_table_id"):
            score += 1.2
            if candidate.get("source_row") is not None and source.get("source_row") == candidate.get("source_row"):
                score += 4.0
        if candidate.get("paragraph_id") is not None and source.get("paragraph_id") == candidate.get("paragraph_id"):
            score += 3.2

    if candidate_snapshot:
        for key in key_fields:
            relation = compare_key_values(row_snapshot.get(key), candidate_snapshot.get(key))
            if relation <= 0:
                return -1.0
            score += relation
        return score

    matched_in_context = 0
    for value in row_snapshot.values():
        normalized_value = normalize_for_match(value)
        if normalized_value and normalized_value in normalized_window:
            matched_in_context += 1
            score += 1.8

    if matched_in_context <= 0:
        return -1.0

    return score


def match_candidate_to_row(
    field_task: dict[str, Any],
    candidate: dict[str, Any],
    rows: list[dict[str, Any]],
    field_tasks: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    key_fields: list[str],
    row_indexes: dict[str, dict[Any, list[dict[str, Any]]]] | None = None,
    client=None,
    model: str = DEFAULT_MODEL,
):
    context_window = collect_context_window(paragraphs, candidate.get("paragraph_id"), window=ROW_MATCH_CONTEXT_WINDOW)

    scoped_rows = rows
    if row_indexes:
        table_key = (candidate.get("source_table_id"), candidate.get("source_row"))
        if table_key[0] and table_key[1] is not None:
            indexed_rows = row_indexes.get("by_table_row", {}).get(table_key, [])
            if len(indexed_rows) == 1:
                return indexed_rows[0]
            if indexed_rows:
                scoped_rows = indexed_rows

        paragraph_id = candidate.get("paragraph_id")
        if paragraph_id is not None:
            indexed_rows = row_indexes.get("by_paragraph", {}).get(paragraph_id, [])
            if len(indexed_rows) == 1 and score_row_for_candidate(indexed_rows[0], candidate, key_fields, context_window) > 0:
                return indexed_rows[0]
            if indexed_rows:
                scoped_rows = indexed_rows

    scored_rows = []
    for row in scoped_rows:
        score = score_row_for_candidate(row, candidate, key_fields, context_window)
        if score > 0:
            scored_rows.append((score, row))
    if not scored_rows:
        return None

    scored_rows.sort(key=lambda item: (-item[0], item[1]["__row_id__"]))
    if len(scored_rows) == 1:
        return scored_rows[0][1]
    if scored_rows[0][0] - scored_rows[1][0] >= 1.0:
        return scored_rows[0][1]

    candidate_rows = [row for _, row in scored_rows[:MAX_ROW_CANDIDATES_FOR_MATCH]]
    field_map = {field["name"]: field for field in field_tasks}
    if client is not None:
        parsed = call_llm_json(
            client=client,
            user_content=build_match_prompt(field_task, candidate, candidate_rows, field_map, paragraphs, key_fields),
            system_content="你是主键归并助手。只有当主键与上下文足够稳定时才允许归并，只输出 JSON。",
            model=model,
            temperature=0.0,
        )
        row_id = normalize_text(parsed.get("row_id"))
        try:
            confidence = float(parsed.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        if row_id and row_id.upper() != "NONE" and confidence >= MIN_ROW_MATCH_CONFIDENCE:
            for row in candidate_rows:
                if row["__row_id__"] == row_id:
                    return row
    return scored_rows[0][1]


def attach_candidate_field(
    rows: list[dict[str, Any]],
    row: dict[str, Any],
    field_task: dict[str, Any],
    candidate: dict[str, Any],
    key_fields_after_fill: list[str],
) -> None:
    field_key = field_task["name"]
    new_cell = candidate.get("fields", {}).get(field_key) or {}
    new_value = normalize_text(new_cell.get("value"))
    if not new_value:
        return

    existing = row.get(field_key, {})
    existing_value = normalize_text(existing.get("value"))

    if not existing_value:
        set_cell_from_candidate(row, field_task, candidate)
        row["__key_fields__"] = key_fields_after_fill
        return

    if normalize_for_match(existing_value) == normalize_for_match(new_value):
        candidate_id = candidate.get("candidate_id")
        if candidate_id and candidate_id not in row.get("__candidate_ids__", []):
            row.setdefault("__candidate_ids__", []).append(candidate_id)
        return

    cloned = copy.deepcopy(row)
    cloned["__row_id__"] = make_row_id()
    set_cell_from_candidate(cloned, field_task, candidate)
    cloned["__key_fields__"] = key_fields_after_fill
    rows.append(cloned)


def build_rows_from_candidates(
    field_tasks: list[dict[str, Any]],
    row_candidates: list[dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    field_matcher: FieldAssistMatcher | None = None,
    client=None,
    model: str = DEFAULT_MODEL,
    progress_callback=None,
    strict_seed_field: bool = True,
) -> list[dict[str, Any]]:
    if not field_tasks or not row_candidates:
        return []

    seed_field = choose_seed_field(field_tasks, row_candidates, strict_seed_field=strict_seed_field)
    if seed_field is None:
        return []
    field_order = [seed_field, *[field for field in field_tasks if field["name"] != seed_field["name"]]]
    rows = build_first_column_rows(seed_field, row_candidates, field_tasks)
    if not rows:
        return []

    field_map = {field["name"]: field for field in field_tasks}
    field_candidates_map = {
        field["name"]: [
            candidate for candidate in row_candidates if field["name"] in (candidate.get("fields") or {})
        ]
        for field in field_tasks
    }

    for row in rows:
        refresh_row_key_fields(row, field_tasks)
        seed_options = collect_row_field_options(
            row,
            seed_field,
            [candidate for candidate in field_candidates_map.get(seed_field["name"], []) if candidate.get("candidate_id") in row.get("__candidate_ids__", [])] or field_candidates_map.get(seed_field["name"], []),
            field_tasks,
            field_map,
            paragraphs,
            field_matcher,
        )
        selected_value = normalize_text((row.get(seed_field["name"]) or {}).get("value"))
        selected_option = next((option for option in seed_options if normalize_text(option.get("value")) == selected_value), None)
        seed_decision = {
            "mode": "seed",
            "selected_option_id": selected_option.get("option_id") if selected_option else None,
            "alternatives": [],
            "confidence": 1.0 if selected_option else 0.0,
            "reason": "首列直接命中并初始化行主键" if seed_field["name"] == field_tasks[0]["name"] else f"以“{seed_field['field_name']}”作为首个可用种子列初始化记录",
        }
        store_row_field_decision(row, seed_field, seed_options[:1] if selected_option is None else [selected_option], seed_decision)

    total_columns = max(1, len(field_order) - 1)
    for column_index, field_task in enumerate(field_order[1:], start=1):
        if progress_callback is not None:
            progress_callback(
                column_index - 1,
                total_columns,
                f"正在按主键归并第 {column_index + 1}/{len(field_order)} 列",
            )

        ordered_rows = build_row_iteration_order(rows, field_tasks, current_field_key=field_task["name"])
        for row in ordered_rows:
            existing_value = normalize_text((row.get(field_task["name"]) or {}).get("value"))
            if existing_value:
                continue
            options = collect_row_field_options(
                row,
                field_task,
                field_candidates_map.get(field_task["name"], []),
                field_tasks,
                field_map,
                paragraphs,
                field_matcher,
            )
            selected_option, decision = decide_field_option_for_row(
                row,
                field_task,
                options,
                field_tasks,
                field_map,
                client=client,
                model=model,
            )
            extra_options = collect_additional_field_options(
                row,
                field_task,
                selected_option,
                options,
                field_tasks,
            )
            store_row_field_decision(row, field_task, options, decision)
            if selected_option is None:
                continue
            row_before_apply = copy.deepcopy(row)
            apply_selected_option_to_row(
                row,
                field_task,
                selected_option,
                decision,
                field_tasks,
            )

            for extra_option in extra_options:
                extra_decision = {
                    "mode": "multi_value_clone",
                    "selected_option_id": extra_option.get("option_id"),
                    "alternatives": [item.get("option_id") for item in options if item.get("option_id") != extra_option.get("option_id")][:3],
                    "confidence": min(0.99, float(extra_option.get("heuristic_score") or 0.0) / 10.0),
                    "reason": "当前列存在多个与动态主键一致的值，复制整行保留多值结果",
                }
                cloned_row = copy.deepcopy(row_before_apply)
                store_row_field_decision(cloned_row, field_task, options, extra_decision)
                cloned_row = clone_row_with_selected_option(
                    cloned_row,
                    field_task,
                    extra_option,
                    extra_decision,
                    field_tasks,
                )
                rows.append(cloned_row)

        if progress_callback is not None:
            progress_callback(
                column_index,
                total_columns,
                f"已完成第 {column_index + 1}/{len(field_order)} 列归并",
            )
    return rows


def build_rows_direct_from_candidates(
    field_tasks: list[dict[str, Any]],
    row_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not field_tasks or not row_candidates:
        return []

    indicator_keys = {field["name"] for field in field_tasks if field.get("slot") == "indicator"}
    primary_value_keys = {field["name"] for field in field_tasks if field.get("slot") in {"value", "yoy"}}
    seen_signatures: set[tuple[Any, ...]] = set()
    rows = []

    ordered_candidates = sorted(
        row_candidates,
        key=lambda candidate: (
            candidate.get("paragraph_id") if candidate.get("paragraph_id") is not None else 10**9,
            -candidate_quality_score(candidate),
            candidate.get("candidate_id") or "",
        ),
    )
    for candidate in ordered_candidates:
        candidate_fields = candidate.get("fields") or {}
        if not candidate_fields:
            continue
        if indicator_keys and not any(field_key in candidate_fields for field_key in indicator_keys):
            continue
        if primary_value_keys and not any(field_key in candidate_fields for field_key in primary_value_keys):
            continue

        row = build_empty_row(field_tasks)
        for field in field_tasks:
            if field["name"] in candidate_fields:
                set_cell_from_candidate(row, field, candidate)
        backfill_unit_fields_from_value(row, field_tasks)
        backfill_context_fields_from_source(row, field_tasks)
        refresh_row_key_fields(row, field_tasks)

        filled_count = sum(
            1
            for field in field_tasks
            if normalize_text((row.get(field["name"]) or {}).get("value"))
        )
        if filled_count <= 0:
            continue
        if not row_has_meaningful_diversity(row, field_tasks):
            continue

        signature = (
            candidate.get("paragraph_id"),
            tuple(
                (
                    field["name"],
                    normalize_for_match((row.get(field["name"]) or {}).get("value")),
                )
                for field in field_tasks
                if normalize_text((row.get(field["name"]) or {}).get("value"))
            ),
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        rows.append(row)
    return rows


def build_source_payload(source: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(source, dict):
        return None
    paragraph_id = source.get("paragraph_id")
    try:
        paragraph_id = int(paragraph_id) if paragraph_id is not None else None
    except Exception:
        paragraph_id = None
    return {
        "paragraph_id": paragraph_id,
        "paragraph_text": normalize_text(source.get("paragraph_text")),
        "evidence": normalize_text(source.get("evidence")),
        "confidence": float(source.get("confidence") or 0.0),
        "source_kind": source.get("source_kind"),
        "source_table_id": source.get("source_table_id"),
        "source_row": source.get("source_row"),
        "source_col": source.get("source_col"),
        "source_header": source.get("source_header"),
        "source_locator": source.get("source_locator"),
    }


def row_has_meaningful_diversity(row: dict[str, Any], field_tasks: list[dict[str, Any]]) -> bool:
    filled_values = []
    slot_values: dict[str, str] = {}
    for field in field_tasks:
        cell = row.get(field["name"]) or {}
        value = normalize_text(cell.get("value"))
        if not value:
            continue
        signature = normalize_for_match(value)
        if not signature:
            continue
        filled_values.append(signature)
        slot = normalize_text(field.get("slot"))
        if slot and slot not in slot_values:
            slot_values[slot] = signature

    if len(filled_values) >= 2 and len(set(filled_values)) == 1:
        return False
    if slot_values.get("category") and slot_values.get("indicator") and slot_values["category"] == slot_values["indicator"]:
        return False
    return True


def finalize_rows(rows: list[dict[str, Any]], field_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    finalized = []
    for row in rows:
        result_row = {
            "record_id": row.get("__row_id__"),
            "__key_fields__": [],
            "__field_keys__": {},
            "__sources__": {},
            "__field_options__": {},
            "__decision_trace__": {},
        }
        has_any_value = False

        for field in field_tasks:
            field_key = field["name"]
            label = field["field_name"]
            cell = row.get(field_key) or {}
            value = normalize_text(cell.get("value"))
            result_row[label] = value or None
            result_row["__field_keys__"][label] = field_key
            source = build_source_payload(cell.get("source"))
            if source:
                result_row["__sources__"][label] = source
            options = (row.get("__field_options__") or {}).get(field_key)
            if isinstance(options, list) and options:
                result_row["__field_options__"][label] = copy.deepcopy(options)
            decision = (row.get("__decision_trace__") or {}).get(field_key)
            if isinstance(decision, dict) and decision:
                result_row["__decision_trace__"][label] = copy.deepcopy(decision)
            if field_key in row.get("__key_fields__", []):
                result_row["__key_fields__"].append(label)
            if value:
                has_any_value = True

        if has_any_value:
            finalized.append(result_row)
    return finalized


def extract(
    data: dict,
    frontend_form: dict | None = None,
    word_config: dict | None = None,
    client=None,
    model: str = DEFAULT_MODEL,
    progress_callback=None,
) -> dict:
    frontend_form = frontend_form if frontend_form is not None else word_config
    config = normalize_extract_config(frontend_form)
    client = build_client() if client is None else client
    doc_type = normalize_text(data.get("doc_type", "")).lower()
    strict_field_anchor = is_strict_table_doc(doc_type)
    paragraphs = ensure_paragraphs(data)
    if not strict_field_anchor:
        paragraphs = merge_non_table_continuation_paragraphs(paragraphs)
    raw_text = "\n".join(paragraph["text"] for paragraph in paragraphs)

    if progress_callback is not None:
        progress_callback(percent=30, message="正在初始化字段语义")
    field_tasks = build_field_tasks_from_frontend(
        frontend_form,
        raw_text,
        client=client,
        model=model,
        relaxed_semantic_match=not strict_field_anchor,
    )
    field_matcher = FieldAssistMatcher(field_tasks)

    if strict_field_anchor:
        rows = extract_from_xlsx_by_header_mapping(
            data,
            field_tasks,
            client=client,
            model=model,
            progress_callback=progress_callback,
        )
        return {
            "doc_id": normalize_text(data.get("doc_id", "")),
            "doc_type": normalize_text(data.get("doc_type", "")),
            "table_id": normalize_config_text(config.get("table_id")),
            "fields": [
                {
                    "field_name": field["field_name"],
                    "key": field["name"],
                    "slot": field.get("slot"),
                    "aliases": field.get("aliases", []),
                    "description": field.get("description", ""),
                    "visible": field.get("visible", True),
                }
                for field in field_tasks
            ],
            "results": rows,
        }

    def report_row_progress(current, total, message):
        if progress_callback is None:
            return
        if total in [None, 0]:
            progress_callback(percent=40, message=message)
            return
        ratio = float(current) / float(total)
        progress_callback(percent=38 + (ratio * 38), current=current, total=total, message=message)

    row_candidates = extract_row_candidates(
        field_tasks,
        paragraphs,
        client=client,
        model=model,
        progress_callback=report_row_progress,
        strict_field_anchor=strict_field_anchor,
    )

    def report_merge_progress(current, total, message):
        if progress_callback is None:
            return
        if total in [None, 0]:
            progress_callback(percent=82, message=message)
            return
        ratio = float(current) / float(total)
        progress_callback(percent=78 + (ratio * 14), current=current, total=total, message=message)

    if strict_field_anchor:
        rows = build_rows_from_candidates(
            field_tasks,
            row_candidates,
            paragraphs,
            field_matcher=field_matcher,
            client=client,
            model=model,
            progress_callback=report_merge_progress,
            strict_seed_field=True,
        )
    else:
        if progress_callback is not None:
            progress_callback(percent=82, message="非表格文档按第一列建行，并按动态主键逐列填充")
        rows = build_rows_from_candidates(
            field_tasks,
            row_candidates,
            paragraphs,
            field_matcher=field_matcher,
            client=client,
            model=model,
            progress_callback=report_merge_progress,
            strict_seed_field=True,
        )
        if not rows:
            if progress_callback is not None:
                progress_callback(percent=86, message="首列建行未产出稳定结果，回退为段落候选直接成行")
            rows = build_rows_direct_from_candidates(field_tasks, row_candidates)

    if progress_callback is not None:
        progress_callback(percent=94, message="正在整理结果")
    rows = finalize_rows(rows, field_tasks)

    return {
        "doc_id": normalize_text(data.get("doc_id", "")),
        "doc_type": normalize_text(data.get("doc_type", "")),
        "table_id": normalize_config_text(config.get("table_id")),
        "fields": [
            {
                "field_name": field["field_name"],
                "key": field["name"],
                "slot": field.get("slot"),
                "aliases": field.get("aliases", []),
                "description": field.get("description", ""),
                "visible": field.get("visible", True),
            }
            for field in field_tasks
        ],
        "results": rows,
    }
