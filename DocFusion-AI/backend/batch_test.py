import os
import time
import json
import csv
from pathlib import Path
from typing import Any, Dict, List

import requests


# =========================
# 配置区
# =========================
REPO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TEST_DIR = REPO_DIR / "test_data" / "fixtures"
REPORT_DIR = REPO_DIR / "backend" / "storage" / "reports"
DEBUG_DIR = REPORT_DIR / "debug_tasks"

BASE_URL = os.getenv("DOCFUSION_BASE_URL", "http://127.0.0.1:8000")
TEST_DIR = Path(os.getenv("DOCFUSION_TEST_DIR", str(DEFAULT_TEST_DIR))).expanduser()

OUTPUT_CSV = REPORT_DIR / "batch_test_results_general.csv"
OUTPUT_JSON = REPORT_DIR / "batch_test_results_general.json"

ALLOWED_EXTS = {".txt", ".docx", ".xlsx", ".xls", ".pdf"}

UPLOAD_TIMEOUT = 120
GET_TIMEOUT = 30

# 轮询参数
MAX_RETRY_TEXT = 40      # txt/docx/pdf：继续等抽取结果
MAX_RETRY_SHEET = 20     # xlsx/xls：只要求解析结果
SLEEP_SECONDS = 1
# =========================


def upload_file(file_path: str) -> Dict[str, Any]:
    url = f"{BASE_URL}/upload"
    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f)}
        response = requests.post(url, files=files, timeout=UPLOAD_TIMEOUT)

    response.raise_for_status()
    data = response.json()

    if "task_id" not in data:
        raise ValueError(f"上传成功但响应中没有 task_id：{data}")

    return data


def get_task(task_id: int) -> Dict[str, Any]:
    url = f"{BASE_URL}/tasks/{task_id}"
    response = requests.get(url, timeout=GET_TIMEOUT)
    response.raise_for_status()
    return response.json()


def get_fields(task_id: int) -> Dict[str, Any]:
    url = f"{BASE_URL}/fields/{task_id}"
    try:
        response = requests.get(url, timeout=GET_TIMEOUT)
        if response.status_code != 200:
            return {
                "ok": False,
                "status_code": response.status_code,
                "error": f"/fields/{task_id} 请求失败",
                "response_text": response.text,
            }
        return {
            "ok": True,
            "data": response.json()
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e)
        }


def collect_test_files(test_dir: str | Path) -> List[Path]:
    root = Path(test_dir)
    if not root.exists():
        raise FileNotFoundError(f"测试样例目录不存在：{test_dir}")

    file_list: List[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in ALLOWED_EXTS:
            file_list.append(path)

    file_list.sort()
    return file_list


def safe_get(data: Any, key: str, default=None):
    if isinstance(data, dict):
        return data.get(key, default)
    return default


def is_non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() != "null"
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def normalize_task_payload(task_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    兼容两种结构：
    1) 顶层直接有 parse_result_summary / extract_result
    2) 顶层只有 result，且 result 是 JSON 字符串/字典
    """
    if not isinstance(task_data, dict):
        return {}

    payload: Dict[str, Any] = {}

    # 新结构：顶层直接有
    if "parse_result_summary" in task_data:
        payload["parse_result_summary"] = task_data.get("parse_result_summary", {})
    if "extract_result" in task_data:
        payload["extract_result"] = task_data.get("extract_result", {})

    if payload:
        return payload

    # 旧结构：result 中存 JSON 字符串或 dict
    result_obj = task_data.get("result")

    if isinstance(result_obj, dict):
        return {"result_parsed": result_obj}

    if isinstance(result_obj, str):
        text = result_obj.strip()
        if text:
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return {"result_parsed": parsed}
            except Exception:
                return {}

    return {}


def has_meaningful_payload(task_data: Dict[str, Any], require_extract: bool = False) -> bool:
    """
    require_extract=False:
        解析结果或抽取结果任一出现即可
    require_extract=True:
        必须等到 extract_result.results 非空
    """
    task_payload = normalize_task_payload(task_data)

    # 解析结果是否到位
    parse_ready = False

    parse_summary = task_payload.get("parse_result_summary")
    if isinstance(parse_summary, dict):
        if safe_get(parse_summary, "paragraph_count", 0):
            parse_ready = True
        if safe_get(parse_summary, "table_count", 0):
            parse_ready = True
        if str(safe_get(parse_summary, "raw_text_preview", "")).strip():
            parse_ready = True

    result_parsed = task_payload.get("result_parsed")
    if isinstance(result_parsed, dict):
        paragraphs = result_parsed.get("paragraphs", [])
        tables = result_parsed.get("tables", [])
        raw_text = result_parsed.get("raw_text", "") or ""

        if isinstance(paragraphs, list) and len(paragraphs) > 0:
            parse_ready = True
        if isinstance(tables, list) and len(tables) > 0:
            parse_ready = True
        if str(raw_text).strip():
            parse_ready = True

    # 抽取结果是否到位
    extract_ready = False
    extract_result = task_payload.get("extract_result")
    if isinstance(extract_result, dict):
        results = extract_result.get("results", [])
        if isinstance(results, list) and len(results) > 0:
            extract_ready = True

    if require_extract:
        return extract_ready

    return parse_ready or extract_ready


def wait_for_full_task_data(task_id: int, file_ext: str) -> Dict[str, Any]:
    """
    - txt/docx/pdf：优先等待 extract_result.results
    - xlsx/xls：只要求解析结果
    """
    need_extract = file_ext in {".txt", ".docx", ".pdf"}
    max_retry = MAX_RETRY_TEXT if need_extract else MAX_RETRY_SHEET

    last_data: Dict[str, Any] = {}

    for _ in range(max_retry):
        data = get_task(task_id)
        last_data = data

        status = str(data.get("status", "")).strip().lower()
        if status in {"failed", "error"}:
            return data

        if status in {"extracted", "parsed", "completed", "success", "done"}:
            if has_meaningful_payload(data, require_extract=need_extract):
                return data

        time.sleep(SLEEP_SECONDS)

    return last_data


def parse_project_fields(fields_data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(fields_data, dict):
        return {
            "project_name": None,
            "project_leader": None,
            "organization_name": None,
            "phone": None,
            "has_project_fields": False,
        }

    project_name = fields_data.get("project_name")
    project_leader = fields_data.get("project_leader")
    organization_name = fields_data.get("organization_name")
    phone = fields_data.get("phone")

    has_project_fields = any([
        is_non_empty(project_name),
        is_non_empty(project_leader),
        is_non_empty(organization_name),
        is_non_empty(phone),
    ])

    return {
        "project_name": project_name,
        "project_leader": project_leader,
        "organization_name": organization_name,
        "phone": phone,
        "has_project_fields": has_project_fields,
    }


def parse_parse_summary(task_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    同时兼容：
    - 新结构：parse_result_summary
    - 旧结构：result_parsed 里的 paragraphs/tables/raw_text
    """
    parse_summary = task_payload.get("parse_result_summary")
    if isinstance(parse_summary, dict):
        paragraph_count = safe_get(parse_summary, "paragraph_count", 0) or 0
        table_count = safe_get(parse_summary, "table_count", 0) or 0
        raw_text_preview = safe_get(parse_summary, "raw_text_preview", "") or ""

        parse_ok = paragraph_count > 0 or table_count > 0 or bool(str(raw_text_preview).strip())
        return {
            "paragraph_count": paragraph_count,
            "table_count": table_count,
            "raw_text_preview_length": len(str(raw_text_preview).strip()),
            "parse_ok": parse_ok,
        }

    result_parsed = task_payload.get("result_parsed", {})
    if isinstance(result_parsed, dict):
        paragraphs = result_parsed.get("paragraphs", [])
        tables = result_parsed.get("tables", [])
        raw_text = result_parsed.get("raw_text", "") or ""

        if not isinstance(paragraphs, list):
            paragraphs = []
        if not isinstance(tables, list):
            tables = []

        paragraph_count = len(paragraphs)
        table_count = len(tables)
        raw_text_preview_length = len(str(raw_text).strip())

        parse_ok = paragraph_count > 0 or table_count > 0 or raw_text_preview_length > 0
        return {
            "paragraph_count": paragraph_count,
            "table_count": table_count,
            "raw_text_preview_length": raw_text_preview_length,
            "parse_ok": parse_ok,
        }

    return {
        "paragraph_count": 0,
        "table_count": 0,
        "raw_text_preview_length": 0,
        "parse_ok": False,
    }


def parse_indicator_results(task_payload: Dict[str, Any]) -> Dict[str, Any]:
    extract_result = task_payload.get("extract_result", {})
    results = safe_get(extract_result, "results", [])

    if not isinstance(results, list):
        results = []

    indicator_count = len(results)

    indicator_examples = []
    for item in results[:3]:
        if isinstance(item, dict):
            indicator_examples.append({
                "分类": item.get("分类"),
                "指标": item.get("指标"),
                "数值": item.get("数值"),
                "单位": item.get("单位"),
                "时间": item.get("时间"),
                "同比": item.get("同比"),
                "来源段落": item.get("来源段落"),
            })

    return {
        "indicator_count": indicator_count,
        "has_indicator_results": indicator_count > 0,
        "indicator_examples": json.dumps(indicator_examples, ensure_ascii=False),
    }


def guess_doc_category(
    file_ext: str,
    has_project_fields: bool,
    has_indicator_results: bool,
    paragraph_count: int,
    table_count: int,
) -> str:
    if has_project_fields:
        return "project_doc"
    if has_indicator_results:
        return "stat_report_or_indicator_doc"
    if file_ext in {".xlsx", ".xls"}:
        return "spreadsheet_doc"
    if table_count > 0 and paragraph_count == 0:
        return "table_like_doc"
    if paragraph_count > 0:
        return "text_doc"
    return "unknown"


def judge_result(row: Dict[str, Any]) -> str:
    if is_non_empty(row.get("error")):
        return "请求报错"

    if not row.get("upload_ok"):
        return "上传失败"

    status = str(row.get("task_status", "")).lower()
    if status in {"failed", "error"}:
        return "任务执行失败"

    if row.get("has_project_fields"):
        return "项目字段抽取成功"

    if row.get("has_indicator_results"):
        return "统计指标抽取成功"

    if row.get("parse_ok"):
        return "文档解析成功"

    return "可能未解析出有效内容"


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    try:
        all_files = collect_test_files(TEST_DIR)
    except Exception as e:
        print(f"读取测试样例失败：{e}")
        return

    if not all_files:
        print("没有找到可测试文件。")
        return

    print("=" * 90)
    print(f"测试目录：{TEST_DIR}")
    print(f"共发现 {len(all_files)} 个测试文件")
    print("=" * 90)

    results: List[Dict[str, Any]] = []

    for index, file_path in enumerate(all_files, start=1):
        relative_path = str(file_path.relative_to(TEST_DIR))

        row: Dict[str, Any] = {
            "index": index,
            "file_name": file_path.name,
            "relative_path": relative_path,
            "full_path": str(file_path),
            "file_ext": file_path.suffix.lower(),

            "upload_ok": False,
            "task_id": None,
            "task_status": None,

            "parse_ok": False,
            "paragraph_count": 0,
            "table_count": 0,
            "raw_text_preview_length": 0,

            "project_name": None,
            "project_leader": None,
            "organization_name": None,
            "phone": None,
            "has_project_fields": False,

            "indicator_count": 0,
            "has_indicator_results": False,
            "indicator_examples": "",

            "doc_category": "unknown",
            "result_label": None,
            "error": None,
        }

        print(f"[{index}/{len(all_files)}] 正在测试：{relative_path}")

        try:
            upload_data = upload_file(str(file_path))
            task_id = upload_data.get("task_id")
            row["upload_ok"] = True
            row["task_id"] = task_id
            print(f"    上传成功，task_id = {task_id}")

            task_data = wait_for_full_task_data(
                task_id=task_id,
                file_ext=row["file_ext"]
            )
            row["task_status"] = safe_get(task_data, "status")
            print(f"    当前状态 = {row['task_status']}")

            if index <= 3:
                debug_filename = DEBUG_DIR / f"debug_task_{task_id}.json"
                with open(debug_filename, "w", encoding="utf-8") as f:
                    json.dump(task_data, f, ensure_ascii=False, indent=2)

            task_payload = normalize_task_payload(task_data)

            parse_info = parse_parse_summary(task_payload)
            row.update(parse_info)

            indicator_info = parse_indicator_results(task_payload)
            row.update(indicator_info)

            fields_resp = get_fields(task_id)
            if fields_resp.get("ok"):
                fields_data = fields_resp.get("data", {})
                project_info = parse_project_fields(fields_data)
                row.update(project_info)

            row["doc_category"] = guess_doc_category(
                file_ext=row["file_ext"],
                has_project_fields=row["has_project_fields"],
                has_indicator_results=row["has_indicator_results"],
                paragraph_count=row["paragraph_count"],
                table_count=row["table_count"],
            )

            row["result_label"] = judge_result(row)

            print(f"    parse_ok              = {row['parse_ok']}")
            print(f"    paragraph_count       = {row['paragraph_count']}")
            print(f"    table_count           = {row['table_count']}")
            print(f"    raw_text_preview_len  = {row['raw_text_preview_length']}")
            print(f"    has_project_fields    = {row['has_project_fields']}")
            print(f"    indicator_count       = {row['indicator_count']}")
            print(f"    doc_category          = {row['doc_category']}")
            print(f"    结果判定              = {row['result_label']}")

        except Exception as e:
            row["error"] = str(e)
            row["result_label"] = judge_result(row)
            print(f"    出错：{e}")

        results.append(row)
        print("-" * 90)

    csv_headers = [
        "index",
        "file_name",
        "relative_path",
        "full_path",
        "file_ext",
        "upload_ok",
        "task_id",
        "task_status",
        "parse_ok",
        "paragraph_count",
        "table_count",
        "raw_text_preview_length",
        "project_name",
        "project_leader",
        "organization_name",
        "phone",
        "has_project_fields",
        "indicator_count",
        "has_indicator_results",
        "indicator_examples",
        "doc_category",
        "result_label",
        "error",
    ]

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=csv_headers)
        writer.writeheader()
        writer.writerows(results)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    total_count = len(results)
    upload_success_count = sum(1 for r in results if r["upload_ok"])
    parse_success_count = sum(1 for r in results if r["parse_ok"])
    project_success_count = sum(1 for r in results if r["result_label"] == "项目字段抽取成功")
    indicator_success_count = sum(1 for r in results if r["result_label"] == "统计指标抽取成功")
    parsed_only_count = sum(1 for r in results if r["result_label"] == "文档解析成功")
    error_count = sum(1 for r in results if is_non_empty(r["error"]))

    print("=" * 90)
    print("批量测试完成")
    print(f"总文件数                 ：{total_count}")
    print(f"上传成功数               ：{upload_success_count}")
    print(f"解析成功数               ：{parse_success_count}")
    print(f"项目字段抽取成功数       ：{project_success_count}")
    print(f"统计指标抽取成功数       ：{indicator_success_count}")
    print(f"仅解析成功数             ：{parsed_only_count}")
    print(f"报错数量                 ：{error_count}")
    print(f"CSV 文件                 ：{OUTPUT_CSV}")
    print(f"JSON 文件                ：{OUTPUT_JSON}")
    print("=" * 90)


if __name__ == "__main__":
    main()
