"""Safe JSON extraction helpers for LLM responses.

Shared by the research planner (Step 2) and the query generator (Step 3).
Never uses eval(); tolerates code fences and prose around the JSON object.
"""

import json
import re

_JSON_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class SafeJSONError(Exception):
    """Raised when text does not contain a usable JSON object."""


def extract_json_object(raw: str) -> dict:
    """Extract the first JSON object from LLM output."""
    text = raw.strip()
    if not text:
        raise SafeJSONError("empty response")

    text = _JSON_FENCE_RE.sub("", text).strip()

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise SafeJSONError("no JSON object found")

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise SafeJSONError(f"invalid JSON: {exc.msg}") from exc

    if not isinstance(data, dict):
        raise SafeJSONError("JSON is not an object")
    return data


def string_list(payload: dict, key: str) -> list[str]:
    """Return payload[key] as a list of non-empty stripped strings."""
    value = payload.get(key)
    if not isinstance(value, list):
        raise SafeJSONError(f"missing '{key}' list")

    items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if not items:
        raise SafeJSONError(f"'{key}' contains no usable strings")
    return items
