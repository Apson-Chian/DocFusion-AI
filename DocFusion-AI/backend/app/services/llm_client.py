import json
import os
import re
from typing import Optional

from ..core.logger import logger

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None


DEFAULT_BASE_URL = os.getenv("EXTRACT_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
DEFAULT_MODEL = os.getenv("EXTRACT_MODEL", "qwen-plus")
_LOGGED_LLM_ERRORS: set[str] = set()


def get_default_base_url() -> str:
    return os.getenv("EXTRACT_BASE_URL", DEFAULT_BASE_URL)


def get_default_model() -> str:
    return os.getenv("EXTRACT_MODEL", DEFAULT_MODEL)


def extract_json_text(raw_text: str) -> str:
    if not raw_text:
        return ""
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_text.strip(), re.S)
    return fenced.group(1).strip() if fenced else raw_text.strip()


def safe_load_json(raw_text: str) -> Optional[dict]:
    try:
        return json.loads(extract_json_text(raw_text))
    except Exception:
        return None


def build_client() -> Optional["OpenAI"]:
    api_key = (
        os.getenv("EXTRACT_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("OPENAI_API_KEY")
    )
    if not api_key or OpenAI is None:
        return None
    return OpenAI(api_key=api_key, base_url=get_default_base_url())


def call_llm_json(
    client: Optional["OpenAI"],
    user_content: str,
    system_content: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.1,
) -> dict:
    if client is None:
        return {}
    model_name = model or get_default_model()
    try:
        completion = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            temperature=temperature,
        )
        content = completion.choices[0].message.content if completion.choices else ""
        return safe_load_json(content) or {}
    except Exception as exc:
        error_message = ""
        try:
            error_message = str(exc)
        except Exception:
            error_message = "unknown"
        signature = f"{type(exc).__name__}:{error_message}"
        if signature not in _LOGGED_LLM_ERRORS:
            _LOGGED_LLM_ERRORS.add(signature)
            logger.warning("LLM 调用失败，已回退到规则抽取: %s", signature)
        return {}
