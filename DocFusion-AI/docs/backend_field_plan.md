# 后端字段方案（第一版）

## 目标

根据当前字段字典，先实现第一版 P0 字段，供文档解析、字段抽取、前端展示使用。

## 第一版支持字段

### 文档基础字段
- doc_id：文档ID
- doc_type：文档类型
- raw_text：原始全文
- paragraphs：段落列表
- tables：表格数据

### 业务抽取字段
- project_name：项目名称
- project_leader：项目负责人
- organization_name：单位名称
- phone：联系电话

## 字段说明

| 字段名 | 类型 | 说明 |
|--------|------|------|
| doc_id | string | 文档唯一标识 |
| doc_type | string | 文档类型，如 project / contract / student_score |
| raw_text | string | 文档原始全文 |
| paragraphs | array[string] | 按段落切分后的文本列表 |
| tables | array[object] | 文档中的表格数据 |
| project_name | string | 项目名称 |
| project_leader | string | 项目负责人 |
| organization_name | string | 单位名称 |
| phone | string | 联系电话 |

## 第一版返回示例

```json
{
  "doc_id": "doc_001",
  "doc_type": "project",
  "raw_text": "项目名称：智慧文档融合平台，负责人：张三，联系电话：13800000000",
  "paragraphs": [
    "项目名称：智慧文档融合平台",
    "负责人：张三",
    "联系电话：13800000000"
  ],
  "tables": [],
  "project_name": "智慧文档融合平台",
  "project_leader": "张三",
  "organization_name": "河海大学",
  "phone": "13800000000"
}