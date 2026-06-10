from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = APP_DIR.parent
REPO_DIR = BACKEND_DIR.parent

STORAGE_DIR = BACKEND_DIR / "storage"
UPLOAD_DIR = STORAGE_DIR / "uploads"
LOG_DIR = STORAGE_DIR / "logs"
DATABASE_PATH = STORAGE_DIR / "app.db"

LEGACY_UPLOAD_DIR = BACKEND_DIR / "uploads"
LEGACY_LOG_DIR = BACKEND_DIR / "logs"
LEGACY_DATABASE_PATH = BACKEND_DIR / "app.db"

FRONTEND_DIR = REPO_DIR / "frontend"
LEGACY_FRONTEND_DIR = REPO_DIR / "frontend 4.10" / "project"
if not FRONTEND_DIR.exists() and LEGACY_FRONTEND_DIR.exists():
    FRONTEND_DIR = LEGACY_FRONTEND_DIR

TEST_DATA_DIR = REPO_DIR / "test_data"
SERVICE_ASSETS_DIR = APP_DIR / "services" / "assets"


def _move_directory_contents(source: Path, target: Path) -> None:
    if not source.exists() or source.resolve() == target.resolve():
        return

    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if destination.exists():
            continue
        item.replace(destination)

    try:
        source.rmdir()
    except OSError:
        pass


def ensure_storage_layout() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if LEGACY_DATABASE_PATH.exists() and not DATABASE_PATH.exists():
        LEGACY_DATABASE_PATH.replace(DATABASE_PATH)

    _move_directory_contents(LEGACY_UPLOAD_DIR, UPLOAD_DIR)
    _move_directory_contents(LEGACY_LOG_DIR, LOG_DIR)


def resolve_uploaded_file_path(raw_path: str | Path) -> Path:
    if raw_path is None:
        raise ValueError("文件路径为空")

    raw_text = str(raw_path).strip()
    if not raw_text:
        raise ValueError("文件路径为空")

    normalized = raw_text.replace("\\", "/")
    raw = Path(normalized)

    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)

    parts = [part for part in raw.parts if part not in {"", "."}]
    upload_relative: Path | None = None
    if "uploads" in parts:
        suffix_parts = parts[parts.index("uploads") + 1 :]
        if suffix_parts:
            upload_relative = Path(*suffix_parts)
            candidates.append(UPLOAD_DIR / upload_relative)
            candidates.append(LEGACY_UPLOAD_DIR / upload_relative)

    if parts:
        relative = Path(*parts)
        candidates.append(BACKEND_DIR / relative)
        candidates.append(REPO_DIR / relative)

    if raw.name:
        candidates.append(UPLOAD_DIR / raw.name)
        candidates.append(LEGACY_UPLOAD_DIR / raw.name)

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    for candidate in deduped:
        if candidate.exists():
            return candidate.resolve()

    if upload_relative is not None:
        return (UPLOAD_DIR / upload_relative).resolve()
    if raw.name:
        return (UPLOAD_DIR / raw.name).resolve()
    if deduped:
        return deduped[0].resolve()
    return raw.resolve()


ensure_storage_layout()
