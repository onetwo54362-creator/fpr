"""Facebook response parsing utilities."""

from __future__ import annotations
import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs

from .constants import FB_RESPONSE_PREFIX, TargetType

log = logging.getLogger(__name__)


# =============================================================================
# GraphQL Response Parsing
# =============================================================================

def parse_fb_response(text: str) -> list[dict]:
    """Parse Facebook GraphQL response into data blocks."""
    blocks = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith(FB_RESPONSE_PREFIX):
            line = line[len(FB_RESPONSE_PREFIX):]
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and "data" in obj:
                data = obj["data"]
                if isinstance(data, dict):
                    data.pop("errors", None)
                    data.pop("extensions", None)
                    blocks.append(data)
        except json.JSONDecodeError:
            continue
    if not blocks:
        raw = text.replace(FB_RESPONSE_PREFIX, "").strip()
        blocks = _extract_data_blocks_fallback(raw)
    return blocks


def _extract_data_blocks_fallback(raw_text: str) -> list[dict]:
    blocks = []
    i = 0
    n = len(raw_text)
    while True:
        idx = raw_text.find('"data"', i)
        if idx == -1:
            break
        brace_start = raw_text.find('{', idx)
        if brace_start == -1:
            break
        depth = 0
        for j in range(brace_start, n):
            if raw_text[j] == '{':
                depth += 1
            elif raw_text[j] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        blocks.append(json.loads(raw_text[brace_start:j + 1]))
                    except json.JSONDecodeError:
                        pass
                    i = j + 1
                    break
        else:
            break
    return blocks


def parse_fb_json_first(response_text: str) -> dict:
    text = response_text.strip()
    if text.startswith(FB_RESPONSE_PREFIX):
        text = text[len(FB_RESPONSE_PREFIX):]
    try:
        return json.loads(text.split("\n")[0].strip())
    except json.JSONDecodeError:
        return {}


# =============================================================================
# Cookie Parsing (4 formats)
# =============================================================================

def parse_cookies(cookie_input: str) -> dict:
    cookie_input = cookie_input.strip()
    # JSON
    if cookie_input.startswith("[") or cookie_input.startswith("{"):
        try:
            parsed = json.loads(cookie_input)
            if isinstance(parsed, list):
                cookies = {}
                for item in parsed:
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        cookies[item["name"]] = item["value"]
                if cookies:
                    return cookies
            elif isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    # Netscape
    if "\t" in cookie_input:
        cookies = {}
        for line in cookie_input.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
        if cookies:
            return cookies
    # Header string
    cookies = {}
    for pair in cookie_input.replace(";", "\n").split("\n"):
        pair = pair.strip()
        if "=" in pair:
            key, _, value = pair.partition("=")
            key, value = key.strip(), value.strip()
            if key and value:
                cookies[key] = value
    return cookies


# =============================================================================
# URL Type Detection
# =============================================================================

def parse_target_url(url: str) -> tuple[str, str, str]:
    """Returns (target_type, entity_id_or_username, clean_url)."""
    url = url.strip()
    if not url.startswith("http"):
        url = f"https://www.facebook.com/{url}"
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    parts = path.split("/")

    if "groups" in parts:
        idx = parts.index("groups")
        if idx + 1 < len(parts):
            gid = parts[idx + 1]
            return TargetType.GROUP, gid, f"https://www.facebook.com/groups/{gid}"

    if "profile.php" in path:
        params = parse_qs(parsed.query)
        uid = params.get("id", [""])[0]
        return TargetType.PROFILE, uid, f"https://www.facebook.com/profile.php?id={uid}"

    if "pages" in parts:
        idx = parts.index("pages")
        remaining = parts[idx + 1:]
        for part in reversed(remaining):
            if part.isdigit():
                return TargetType.PAGE, part, f"https://www.facebook.com/{part}"
        if remaining:
            return TargetType.PAGE, remaining[-1], f"https://www.facebook.com/{remaining[-1]}"

    if "people" in parts:
        idx = parts.index("people")
        for part in reversed(parts[idx + 1:]):
            if part.isdigit():
                return TargetType.PROFILE, part, f"https://www.facebook.com/profile.php?id={part}"

    if len(parts) >= 1 and parts[0]:
        username = parts[0]
        return TargetType.UNKNOWN, username, f"https://www.facebook.com/{username}"

    return TargetType.UNKNOWN, "", url


# =============================================================================
# Embedded JSON Extraction
# =============================================================================

def extract_json_from_html(html: str, search_keys: list[str]) -> list[dict]:
    results = []
    for key in search_keys:
        pattern = f'"{key}"'
        for m in re.finditer(re.escape(pattern), html):
            pos = m.start()
            obj_start = _find_object_start(html, pos)
            if obj_start < 0:
                continue
            try:
                obj, _ = _parse_json_at(html, obj_start)
                if obj and isinstance(obj, dict):
                    results.append(obj)
                    if len(results) >= 30:
                        return results
            except Exception:
                continue
    return results


def _parse_json_at(text: str, start: int) -> tuple[Optional[dict], int]:
    if start >= len(text) or text[start] != '{':
        return None, start
    depth = 0
    in_str = False
    esc = False
    for i in range(start, min(start + 500000, len(text))):
        c = text[i]
        if esc:
            esc = False
            continue
        if c == '\\' and in_str:
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1]), i + 1
                except json.JSONDecodeError:
                    return None, i + 1
    return None, start


def _find_object_start(text: str, pos: int, max_back: int = 50000) -> int:
    depth = 0
    for i in range(pos, max(pos - max_back, -1), -1):
        if text[i] == '}':
            depth += 1
        elif text[i] == '{':
            if depth == 0:
                return i
            depth -= 1
    return -1


# =============================================================================
# Deep Nested Value Search
# =============================================================================

def find_nested_value(obj: Any, key: str, max_depth: int = 10, _depth: int = 0) -> Any:
    if _depth > max_depth:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for val in obj.values():
            r = find_nested_value(val, key, max_depth, _depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = find_nested_value(item, key, max_depth, _depth + 1)
            if r is not None:
                return r
    return None


def find_all_nested_values(obj: Any, key: str, max_depth: int = 10, _depth: int = 0) -> list:
    results = []
    if _depth > max_depth:
        return results
    if isinstance(obj, dict):
        if key in obj:
            results.append(obj[key])
        for val in obj.values():
            results.extend(find_all_nested_values(val, key, max_depth, _depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(find_all_nested_values(item, key, max_depth, _depth + 1))
    return results


def find_typed_objects(obj: Any, typename: str, max_depth: int = 15, _depth: int = 0) -> list[dict]:
    results = []
    if _depth > max_depth:
        return results
    if isinstance(obj, dict):
        if obj.get("__typename") == typename:
            results.append(obj)
        for val in obj.values():
            results.extend(find_typed_objects(val, typename, max_depth, _depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(find_typed_objects(item, typename, max_depth, _depth + 1))
    return results


# =============================================================================
# Entity Type Detection from HTML
# =============================================================================

def detect_entity_type_from_html(html: str) -> str:
    snippet = html[:100000]
    page_score = sum(1 for s in ['"__typename":"Page"', '"pageID"', '"page_id"', 'fb://page/'] if s in snippet)
    profile_score = sum(1 for s in ['"__typename":"User"', '"userID"', '"profile_id"', 'fb://profile/'] if s in snippet)
    group_score = sum(1 for s in ['"__typename":"Group"', '"groupID"', '"group_id"', 'fb://group/'] if s in snippet)
    if group_score > page_score and group_score > profile_score:
        return TargetType.GROUP
    if page_score > profile_score:
        return TargetType.PAGE
    if profile_score > 0:
        return TargetType.PROFILE
    return TargetType.UNKNOWN
