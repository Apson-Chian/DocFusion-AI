import os
from pathlib import Path


_ENV_LOADED = False


def _strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    result: list[str] = []

    for index, char in enumerate(value):
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            if index == 0 or value[index - 1].isspace():
                break
        result.append(char)

    return "".join(result).strip()


def _parse_value(raw_value: str) -> str:
    value = _strip_inline_comment(raw_value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _should_replace_env(existing: str | None) -> bool:
    return existing is None or existing == ""


def _should_force_override_env(key: str) -> bool:
    return key.startswith("EXTRACT_") or key in {"OPENAI_API_KEY", "DASHSCOPE_API_KEY"}


def load_project_env() -> Path | None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return None

    repo_dir = Path(__file__).resolve().parents[3]
    candidates = []
    cwd_env = Path.cwd() / ".env"
    repo_env = repo_dir / ".env"

    candidates.append(cwd_env)
    if repo_env != cwd_env:
        candidates.append(repo_env)

    for env_path in candidates:
        if not env_path.exists():
            continue

        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue

            key, raw_value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue

            parsed_value = _parse_value(raw_value)
            if _should_force_override_env(key) or _should_replace_env(os.environ.get(key)):
                os.environ[key] = parsed_value

        _ENV_LOADED = True
        return env_path

    _ENV_LOADED = True
    return None
