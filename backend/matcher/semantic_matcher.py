import json
import os
import re
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

class FieldSemanticMatcher:
    def __init__(self, dict_path=None, model_name='shibing624/text2vec-base-chinese', threshold=0.80):
        # 1. 加载字典
        if dict_path is None:
            dict_path = os.path.join(os.path.dirname(__file__), 'field_mapping.json')
        
        self.threshold = threshold
        self.standard_fields =[]
        self.reverse_dict = {}
        
        with open(dict_path, 'r', encoding='utf-8') as f:
            mapping_dict = json.load(f)
            for std_key, synonyms in mapping_dict.items():
                self.standard_fields.append(std_key)
                self.reverse_dict[std_key] = std_key
                for syn in synonyms:
                    self.reverse_dict[syn] = std_key

        # 2. 加载大语言模型并预计算标准字段的向量
        print(f"[*] 正在加载向量大模型 {model_name}，请稍候...")
        self.model = SentenceTransformer(model_name)
        self.standard_embeddings = self.model.encode(self.standard_fields)
        print("[*] 模型加载完成，向量特征初始化完毕！")

    def _rule_check(self, standard_key, entity_value):
        """
        PDF原方案要求：结合规则进行校验。例如金额必须包含数字
        """
        value_str = str(entity_value)
        # 如果匹配到的是数量或金额，必须包含数字
        if standard_key in ['amount', 'quota']:
            if not re.search(r'\d', value_str):
                return False
        return True

    def match_field(self, extracted_key, entity_value=""):
        """
        匹配逻辑：字典匹配 -> 向量相似度匹配 -> 规则校验
        """
        # 第一层：字典硬匹配
        if extracted_key in self.reverse_dict:
            std_key = self.reverse_dict[extracted_key]
            if self._rule_check(std_key, entity_value):
                return std_key, 1.0

        # 第二层：语义向量匹配
        query_embedding = self.model.encode([extracted_key])
        similarities = cosine_similarity(query_embedding, self.standard_embeddings)[0]
        
        best_match_idx = np.argmax(similarities)
        best_score = similarities[best_match_idx]
        
        if best_score >= self.threshold:
            std_key = self.standard_fields[best_match_idx]
            # 第三层：规则校验
            if self._rule_check(std_key, entity_value):
                return std_key, float(best_score)
        
        return None, float(best_score)

    def process_data(self, json_data):
        """
        供负责人4(后端)直接调用的入口函数
        """
        result = {}
        for chinese_key, value in json_data.items():
            matched_key, score = self.match_field(chinese_key, value)
            if matched_key:
                result[matched_key] = value
            else:
                result[f"未匹配_{chinese_key}"] = value
        return result