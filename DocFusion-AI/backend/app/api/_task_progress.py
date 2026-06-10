TERMINAL_TASK_STATUSES = {
    "matched",
    "extracted",
    "failed",
    "parse_failed",
    "extract_failed",
}

FAILURE_TASK_STATUSES = {
    "failed",
    "parse_failed",
    "extract_failed",
}


def clamp_progress_percent(value):
    try:
        percent = float(value)
    except Exception:
        return None
    return max(0.0, min(100.0, percent))


def set_task_progress(task, *, stage=None, current=None, total=None, percent=None, message=None):
    if stage is not None:
        task.progress_stage = str(stage)
    if current is not None:
        try:
            task.progress_current = int(current)
        except Exception:
            task.progress_current = None
    if total is not None:
        try:
            task.progress_total = int(total)
        except Exception:
            task.progress_total = None

    derived_percent = percent
    if derived_percent is None and current is not None and total not in [None, 0]:
        try:
            derived_percent = (float(current) / float(total)) * 100.0
        except Exception:
            derived_percent = None
    if derived_percent is not None:
        task.progress_percent = clamp_progress_percent(derived_percent)

    if message is not None:
        task.progress_message = str(message)


def serialize_task_progress(task):
    return {
        "stage": task.progress_stage,
        "current": task.progress_current,
        "total": task.progress_total,
        "percent": task.progress_percent,
        "message": task.progress_message,
        "terminal": task.status in TERMINAL_TASK_STATUSES,
        "failed": task.status in FAILURE_TASK_STATUSES,
    }
