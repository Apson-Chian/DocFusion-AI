import json
import math
import os
import re
from difflib import SequenceMatcher
from pathlib import Path

try:
    import numpy as np
except ImportError:
    np = None

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

try:
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    cosine_similarity = None

from ..core.paths import SERVICE_ASSETS_DIR


DEFAULT_DICT_PATH = SERVICE_ASSETS_DIR / "field_mapping.json"
DEFAULT_MATCHER_MODEL = os.getenv("MATCHER_MODEL", "shibing624/text2vec-base-chinese")
ENABLE_MATCHER_EMBEDDING = os.getenv("MATCHER_ENABLE_EMBEDDING", "0") == "1"


def normalize_key(text: str) -> str:
    text = str(text or "").strip().lower()
    return re.sub(r"[\s:：_\-（）()\[\]【】,，.。/\\]+", "", text)


class FieldSemanticMatcher:
    def __init__(
        self,
        dict_path: str | Path | None = None,
        model_name: str = DEFAULT_MATCHER_MODEL,
        threshold: float = 0.8,
    ):
        dict_path = Path(dict_path or DEFAULT_DICT_PATH)
        mapping_dict = json.loads(dict_path.read_text(encoding="utf-8"))

        self.model_name = model_name
        self.threshold = threshold
        self.standard_fields: list[str] = []
        self.reverse_dict: dict[str, str] = {}

        for std_key, synonyms in mapping_dict.items():
            self.standard_fields.append(std_key)
            self.reverse_dict[normalize_key(std_key)] = std_key
            for synonym in synonyms:
                self.reverse_dict[normalize_key(synonym)] = std_key

        self.model = None
        self.standard_embeddings = None
        self.vector_enabled = False

        should_try_vector = ENABLE_MATCHER_EMBEDDING or os.path.isdir(str(model_name))
        if should_try_vector and SentenceTransformer is not None and np is not None and cosine_similarity is not None:
            try:
                self.model = SentenceTransformer(model_name)
                self.standard_embeddings = self.model.encode(self.standard_fields)
                self.vector_enabled = True
            except Exception:
                self.model = None
                self.standard_embeddings = None
                self.vector_enabled = False

    def encode_field(self, extracted_key: str):
        if not self.vector_enabled or self.model is None:
            return self._fallback_vector(extracted_key)
        return self.model.encode([extracted_key])[0]

    def _fallback_vector(self, text: str) -> list[float]:
        normalized = normalize_key(text)
        counts = {}
        for char in normalized:
            counts[char] = counts.get(char, 0) + 1
        return [float(sum((ord(char) + count) for char, count in counts.items())) or 0.0]

    def _rule_check(self, standard_key: str, entity_value):
        value_str = str(entity_value)
        if standard_key in ["amount", "quota"] and not re.search(r"\d", value_str):
            return False
        return True

    def _fallback_similarity(self, left: str, right: str) -> float:
        left_norm = normalize_key(left)
        right_norm = normalize_key(right)
        if not left_norm or not right_norm:
            return 0.0
        exact_bonus = 1.0 if left_norm == right_norm else 0.0
        overlap = len(set(left_norm) & set(right_norm)) / max(len(set(left_norm) | set(right_norm)), 1)
        sequence = SequenceMatcher(a=left_norm, b=right_norm).ratio()
        return min(1.0, max(exact_bonus, 0.55 * sequence + 0.45 * overlap))

    def match_field(self, extracted_key: str, entity_value=""):
        return self.match_field_with_embedding(extracted_key, entity_value)

    def match_field_with_embedding(self, extracted_key: str, entity_value="", query_embedding=None):
        normalized_key = normalize_key(extracted_key)
        if normalized_key in self.reverse_dict:
            std_key = self.reverse_dict[normalized_key]
            if self._rule_check(std_key, entity_value):
                return std_key, 1.0

        best_match = None
        best_score = -math.inf

        if self.vector_enabled and self.standard_embeddings is not None and np is not None and cosine_similarity is not None:
            if query_embedding is None:
                query_embedding = self.encode_field(extracted_key)
            similarities = cosine_similarity(np.asarray(query_embedding).reshape(1, -1), self.standard_embeddings)[0]
            best_match_idx = int(np.argmax(similarities))
            best_match = self.standard_fields[best_match_idx]
            best_score = float(similarities[best_match_idx])
        else:
            for field in self.standard_fields:
                score = self._fallback_similarity(extracted_key, field)
                if score > best_score:
                    best_match = field
                    best_score = score

        if best_match and best_score >= self.threshold and self._rule_check(best_match, entity_value):
            return best_match, float(best_score)

        return None, float(best_score if best_score != -math.inf else 0.0)

    def process_data(self, json_data):
        result = {}
        for chinese_key, value in json_data.items():
            matched_key, _ = self.match_field(chinese_key, value)
            result[matched_key or f"未匹配_{chinese_key}"] = value
        return result
