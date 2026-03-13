import json
from semantic_matcher import FieldSemanticMatcher

def run_test():
    # 初始化你的匹配器
    matcher = FieldSemanticMatcher()

    # 模拟负责人2(信息抽取)传给你的散乱数据
    test_data = {
        "招考单位": "国家税务总局蒙阴县税务局",  # 测试字典匹配 -> department
        "拟招收人数": 3,                      # 测试语义向量匹配 -> quota
        "咨询电话1": "0531-88758810",         # 测试字典匹配 -> phone
        "项目预算金额": "伍拾万元整",           # 测试规则校验拦截 (无阿拉伯数字) -> 应该匹配失败
        "就读专业": "计算机类"                 # 测试字典匹配 -> major
    }

    print("\n--- 开始处理测试数据 ---")
    result = matcher.process_data(test_data)
    
    print("\n--- 负责人3模块输出结果 ---")
    print(json.dumps(result, indent=4, ensure_ascii=False))

if __name__ == "__main__":
    run_test()