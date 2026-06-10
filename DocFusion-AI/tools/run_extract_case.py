import argparse
import json
import sys
import time
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from backend.app.api.upload import create_upload_task
from backend.app.db.database import SessionLocal
from backend.app.db.models import Task
from backend.app.api._result_utils import safe_json_loads, unify_task_payload


DONE_STATUSES = {"extracted", "matched", "failed"}


def wait_for_task(task_id: int, timeout: float, interval: float) -> Task:
    deadline = time.time() + timeout
    last_task = None
    while time.time() < deadline:
        db = SessionLocal()
        try:
            task = db.query(Task).filter(Task.id == task_id).first()
            if task is None:
                raise RuntimeError(f"task_id={task_id} 不存在")
            last_task = task
            if str(task.status or "").lower() in DONE_STATUSES and str(task.extract_status or "").lower() in {"success", "failed"}:
                db.expunge(task)
                return task
        finally:
            db.close()
        time.sleep(interval)
    if last_task is None:
        raise TimeoutError(f"等待任务 {task_id} 超时")
    return last_task


def load_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


def summarize(task: Task) -> dict:
    task_result, extract_result, match_result = unify_task_payload(task)
    results = extract_result.get("results") or []
    fields = extract_result.get("fields") or []
    rows = [item for item in results if isinstance(item, dict)]
    field_names = [item.get("field_name") for item in fields if isinstance(item, dict) and item.get("field_name")]

    filled_counts = {}
    for field_name in field_names:
        filled_counts[field_name] = sum(1 for row in rows if row.get(field_name) not in [None, ""])

    return {
        "task_id": task.id,
        "status": task.status,
        "parse_status": task.parse_status,
        "extract_status": task.extract_status,
        "match_status": task.match_status,
        "error_message": task.error_message,
        "doc_type": task_result.get("doc_type"),
        "result_count": len(rows),
        "field_names": field_names,
        "filled_counts": filled_counts,
        "results_preview": rows[:5],
        "extract_result": extract_result,
        "match_result": match_result,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="fixture file path")
    parser.add_argument("--config", required=True, help="extract config json path")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--save", help="optional output json path")
    args = parser.parse_args()

    file_path = Path(args.file)
    config = load_config(Path(args.config))

    payload = create_upload_task(file_path.name, file_path.read_bytes(), config)
    task = wait_for_task(int(payload["task_id"]), timeout=args.timeout, interval=args.interval)
    summary = summarize(task)

    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)

    if args.save:
        Path(args.save).write_text(text, encoding="utf-8")

    return 0 if str(summary["extract_status"]).lower() != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
