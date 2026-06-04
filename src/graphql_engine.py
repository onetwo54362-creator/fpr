"""GraphQL HTTP engine for Facebook API."""

from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import Optional

import httpx

from .constants import (
    GRAPHQL_URL, DEFAULT_REQUEST_TIMEOUT, DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_BASE_DELAY, get_base_headers, get_document_headers, get_random_user_agent, TargetType,
)
from .proxy_manager import ProxyManager
from .response_parser import parse_fb_response, parse_fb_json_first, detect_entity_type_from_html

log = logging.getLogger(__name__)


class GraphQLEngine:
    def __init__(self, cookies: dict, fb_dtsg: str, proxy_manager: Optional[ProxyManager] = None,
                 timeout: int = DEFAULT_REQUEST_TIMEOUT, max_retries: int = DEFAULT_MAX_RETRIES):
        self.cookies = cookies
        self.fb_dtsg = fb_dtsg
        self.proxy_manager = proxy_manager or ProxyManager()
        self.timeout = timeout
        self.max_retries = max_retries
        self._user_agent = get_random_user_agent()
        self._total_requests = 0
        self._failed_requests = 0
        proxy_url = self.proxy_manager.get_proxy_dict()
        self._client = httpx.AsyncClient(
            proxy=proxy_url, timeout=httpx.Timeout(timeout),
            follow_redirects=True, cookies=cookies,
        )
        log.info(f"🔗 GraphQL engine initialized (user: {cookies.get('c_user', '?')})")

    async def close(self):
        await self._client.aclose()

    def _build_payload(self, doc_id: str, variables: dict, friendly_name: str = "") -> dict:
        uid = self.cookies.get("c_user", "0")
        return {
            "av": uid, "__user": uid, "__a": "1",
            "fb_dtsg": self.fb_dtsg, "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": friendly_name, "server_timestamps": "true",
            "doc_id": doc_id, "variables": json.dumps(variables),
        }

    async def request(self, doc_id: str, variables: dict, friendly_name: str = "") -> list[dict]:
        headers = get_base_headers(user_agent=self._user_agent, friendly_name=friendly_name)
        payload = self._build_payload(doc_id, variables, friendly_name)
        for attempt in range(1, self.max_retries + 1):
            try:
                self._total_requests += 1
                resp = await self._client.post(GRAPHQL_URL, data=payload, headers=headers)
                if self.proxy_manager.is_blocked(resp.status_code, resp.text[:500] if resp.status_code != 200 else ""):
                    log.warning(f"  🚫 Attempt {attempt}/{self.max_retries}: Blocked (HTTP {resp.status_code})")
                    if attempt < self.max_retries:
                        await asyncio.sleep(attempt * DEFAULT_RETRY_BASE_DELAY)
                    continue
                if resp.status_code != 200:
                    log.warning(f"  ⚠️ Attempt {attempt}/{self.max_retries}: HTTP {resp.status_code}")
                    if attempt < self.max_retries:
                        await asyncio.sleep(attempt * DEFAULT_RETRY_BASE_DELAY)
                    continue
                self.proxy_manager.on_success()
                return parse_fb_response(resp.text)
            except (httpx.ProxyError, httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout) as e:
                self._failed_requests += 1
                log.warning(f"  ⚠️ Attempt {attempt}/{self.max_retries}: {e}")
                if attempt < self.max_retries:
                    await asyncio.sleep(attempt * DEFAULT_RETRY_BASE_DELAY)
            except Exception as e:
                self._failed_requests += 1
                log.error(f"  ❌ Attempt {attempt}/{self.max_retries}: {e}")
                if attempt < self.max_retries:
                    await asyncio.sleep(attempt * DEFAULT_RETRY_BASE_DELAY)
        log.error(f"❌ All {self.max_retries} attempts failed for {friendly_name or doc_id}")
        return []

    async def request_json(self, doc_id: str, variables: dict, friendly_name: str = "") -> dict:
        headers = get_base_headers(user_agent=self._user_agent, friendly_name=friendly_name)
        payload = self._build_payload(doc_id, variables)
        for attempt in range(1, self.max_retries + 1):
            try:
                self._total_requests += 1
                resp = await self._client.post(GRAPHQL_URL, data=payload, headers=headers)
                if self.proxy_manager.is_blocked(resp.status_code, resp.text[:500] if resp.status_code != 200 else ""):
                    if attempt < self.max_retries:
                        await asyncio.sleep(attempt * DEFAULT_RETRY_BASE_DELAY)
                    continue
                if resp.status_code == 200:
                    self.proxy_manager.on_success()
                    return parse_fb_json_first(resp.text)
                if attempt < self.max_retries:
                    await asyncio.sleep(attempt * DEFAULT_RETRY_BASE_DELAY)
            except Exception as e:
                self._failed_requests += 1
                log.warning(f"  ⚠️ Attempt {attempt}: {e}")
                if attempt < self.max_retries:
                    await asyncio.sleep(attempt * DEFAULT_RETRY_BASE_DELAY)
        return {}

    async def fetch_page_html(self, url: str) -> Optional[str]:
        """Fetch a page and return full HTML. Use sparingly — prefer fetch_and_extract()."""
        try:
            headers = get_document_headers(user_agent=self._user_agent)
            self._total_requests += 1
            resp = await self._client.get(url, headers=headers)
            if resp.status_code != 200:
                log.warning(f"Failed to fetch {url}: HTTP {resp.status_code}")
                return None
            self.proxy_manager.on_success()
            return resp.text
        except Exception as e:
            self._failed_requests += 1
            log.error(f"Error fetching {url}: {e}")
            return None

    async def fetch_and_extract(self, url: str) -> dict:
        """Fetch a page, extract only profile_fields + basic metadata, discard HTML.

        Returns a compact dict (~1-10KB) instead of full HTML (2-5MB).
        Keys: profile_fields (list), meta (dict with name, user_id, gender, etc.)
        """
        try:
            headers = get_document_headers(user_agent=self._user_agent)
            self._total_requests += 1
            resp = await self._client.get(url, headers=headers)
            if resp.status_code != 200:
                return {"profile_fields": [], "meta": {}}
            self.proxy_manager.on_success()
            text = resp.text
            # Extract profile_fields nodes — the actual data we need
            fields = _extract_profile_fields_from_html(text)
            # Extract basic metadata with lightweight regex
            meta = _extract_meta_from_html(text, self.cookies.get("c_user", ""))
            # HTML goes out of scope here — freed immediately
            del text
            return {"profile_fields": fields, "meta": meta}
        except Exception as e:
            self._failed_requests += 1
            log.error(f"Error in fetch_and_extract {url}: {e}")
            return {"profile_fields": [], "meta": {}}

    async def resolve_entity(self, url: str) -> dict:
        """Resolve entity type/id from a URL. Returns compact dict, not full HTML.

        Returns: {type, id, meta} where meta has name, gender, verified, etc.
        """
        try:
            headers = get_document_headers(user_agent=self._user_agent)
            self._total_requests += 1
            resp = await self._client.get(url, headers=headers)
            if resp.status_code != 200:
                return {"type": TargetType.UNKNOWN, "id": None, "meta": {}}
            self.proxy_manager.on_success()
            text = resp.text
            entity_type = detect_entity_type_from_html(text)
            entity_id = None
            c_user = self.cookies.get("c_user", "")
            patterns_map = {
                TargetType.PAGE: [r'"pageID"\s*:\s*"(\d+)"', r'"page_id"\s*:\s*"(\d+)"'],
                TargetType.PROFILE: [r'"userID"\s*:\s*"(\d+)"', r'"profile_id"\s*:\s*"(\d+)"'],
                TargetType.GROUP: [r'"groupID"\s*:\s*"(\d+)"', r'"group_id"\s*:\s*"(\d+)"'],
            }
            patterns = patterns_map.get(entity_type, [])
            patterns.extend([r'"entity_id"\s*:\s*"(\d+)"', r'"ownerID"\s*:\s*"(\d+)"', r'"actorID"\s*:\s*"(\d+)"'])
            for p in patterns:
                m = re.search(p, text)
                if m and m.group(1) != c_user:
                    entity_id = m.group(1)
                    break
            # Extract compact metadata + profile_fields (if any on this page)
            meta = _extract_meta_from_html(text, c_user)
            fields = _extract_profile_fields_from_html(text)
            log.info(f"🔍 Resolved: type={entity_type}, id={entity_id}")
            del text  # Free the big HTML string
            return {"type": entity_type, "id": entity_id, "meta": meta, "profile_fields": fields}
        except Exception as e:
            log.error(f"Error resolving entity: {e}")
            return {"type": TargetType.UNKNOWN, "id": None, "meta": {}}

    async def resolve_entity_type_and_id(self, url: str) -> tuple[str, Optional[str], Optional[str]]:
        """Legacy method — now returns None for HTML to save memory."""
        result = await self.resolve_entity(url)
        return result["type"], result.get("id"), None

    async def auto_fetch_fb_dtsg(self) -> Optional[str]:
        log.info("🔑 Auto-fetching fb_dtsg token...")
        dtsg_patterns = [
            r'\["DTSGInitData",\[\],\{"token":"([^"]+)"',
            r'\["DTSGInitialData",\[\],\{"token":"([^"]+)"',
            r'"DTSGInitData",\[\],\{"token":"([^"]+)"',
            r'"DTSGInitialData",\[\],\{"token":"([^"]+)"',
            r'"dtsg":\{"token":"([^"]+)"',
            r'"dtsg_ag":\{"token":"([^"]+)"',
            r'"fb_dtsg":"([^"]+)"',
            r'name="fb_dtsg"\s+value="([^"]+)"',
            r'name="fb_dtsg"[^>]*value="([^"]+)"',
            r'"fb_dtsg"\s*,\s*"([^"]+)"',
            r'"DTSG"[^}]*"token":"([^"]+)"',
            r'dtsg.*?"token":"([^"]+)"',
        ]
        for url in ["https://www.facebook.com/", "https://m.facebook.com/",
                     "https://www.facebook.com/settings", "https://www.facebook.com/profile.php"]:
            try:
                headers = get_document_headers(user_agent=self._user_agent)
                self._total_requests += 1
                resp = await self._client.get(url, headers=headers)
                if resp.status_code != 200:
                    continue
                text = resp.text
                for pattern in dtsg_patterns:
                    for token in re.findall(pattern, text):
                        if len(token) > 10 and not token.isdigit() and not token.startswith("http") and "DOCTYPE" not in token:
                            log.info(f"✅ Got fb_dtsg ({len(token)} chars) from {url}")
                            self.fb_dtsg = token
                            return token
            except Exception:
                continue
        log.warning("⚠️ Could not find fb_dtsg — provide it manually")
        return None

    def get_stats(self) -> dict:
        return {
            "total_requests": self._total_requests,
            "failed_requests": self._failed_requests,
            "success_rate": f"{(self._total_requests - self._failed_requests) / max(1, self._total_requests) * 100:.1f}%",
        }


# =============================================================================
# Inline extractors — run inside engine, HTML never leaves scope
# =============================================================================

# Facebook internal module names to ignore
_INVALID_NAMES = {
    'WAWebOpusRecorderWorkerBundle', 'WebWizRecorderWorkerBundle',
    'WAWebWorkerBundle', 'CometMediaViewerPhoto', 'CometFeed',
    'RelayModern', 'MAWMainV4WebWorkerBundle', 'BlobStorageWorkerBundle',
    'ZenonSignalingSharedWorkerV2Bundle', 'Facebook', '', 'undefined', 'null',
    'About', 'Intro', 'Mentions',
}


def _extract_profile_fields_from_html(html: str) -> list[dict]:
    """Extract all profile_fields nodes from embedded <script> JSON.

    Returns a small list of dicts (~1-5KB) instead of holding the full 2-5MB HTML.
    Uses two strategies: structured JSON parse, then regex fallback.
    """
    all_fields = []

    # Strategy 1: Find "profile_fields":{"nodes":[...]} and parse the array
    for m in re.finditer(r'"profile_fields"\s*:\s*\{\s*"nodes"\s*:\s*\[', html):
        start = m.end() - 1  # position of '['
        depth = 0
        i = start
        limit = min(len(html), start + 80000)
        while i < limit:
            if html[i] == '[':
                depth += 1
            elif html[i] == ']':
                depth -= 1
                if depth == 0:
                    try:
                        nodes = json.loads(html[start:i + 1])
                        all_fields.extend(nodes)
                    except json.JSONDecodeError:
                        pass
                    break
            i += 1

    # Strategy 2: Regex fallback for field_type + title pairs
    if not all_fields:
        for m in re.finditer(r'"field_type"\s*:\s*"([^"]+)"', html):
            field_type = m.group(1)
            ctx_start = max(0, m.start() - 500)
            ctx_end = min(len(html), m.end() + 2000)
            context = html[ctx_start:ctx_end]

            title_m = re.search(r'"title"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"', context)
            text_m = re.search(r'"text_content"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"', context)

            title_text = title_m.group(1) if title_m else ''
            content_text = text_m.group(1) if text_m else ''

            if title_text or content_text:
                all_fields.append({
                    'field_type': field_type,
                    'title': {'text': title_text},
                    'text_content': {'text': content_text} if content_text else None,
                })

    return all_fields


def _extract_meta_from_html(html: str, c_user: str = '') -> dict:
    """Extract compact metadata from HTML — name, id, gender, counts, status flags.

    Returns a small dict (~500 bytes) instead of the full 2-5MB HTML.
    """
    meta = {}

    # Name from <title>
    m = re.search(r'<title[^>]*>([^<]+)</title>', html)
    if m:
        name = m.group(1).strip()
        for sfx in (' | Facebook', ' - Facebook', ' \u2014 Facebook', ' \u00b7 Facebook',
                     ' | Group', ' group'):
            name = name.replace(sfx, '')
        if name and name not in _INVALID_NAMES and not name.startswith('WAWeb'):
            meta['name'] = name

    # og:title fallback
    if 'name' not in meta:
        m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
        if not m:
            m = re.search(r'content="([^"]+)"\s+property="og:title"', html)
        if m:
            name = m.group(1).replace(' | Facebook', '').replace(' - Facebook', '').strip()
            if name and name not in _INVALID_NAMES:
                meta['name'] = name

    # User/Page/Group ID
    for pat in [r'"userID"\s*:\s*"(\d+)"', r'"pageID"\s*:\s*"(\d+)"',
                r'"entity_id"\s*:\s*"(\d+)"', r'"groupID"\s*:\s*"(\d+)"',
                r'"ownerID"\s*:\s*"(\d+)"']:
        m = re.search(pat, html)
        if m and m.group(1) != c_user:
            meta['id'] = m.group(1)
            break

    # Profile picture
    m = re.search(r'"profile_picture_for_sticky_bar"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
    if m:
        meta['profile_picture_url'] = m.group(1).replace('\\/', '/')
    else:
        m = re.search(r'property="og:image"\s+content="([^"]+)"', html)
        if m:
            meta['profile_picture_url'] = m.group(1)

    # Cover photo
    m = re.search(r'"coverPhoto"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
    if not m:
        m = re.search(r'"cover_photo"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
    if m:
        meta['cover_photo_url'] = m.group(1).replace('\\/', '/')

    # Gender
    m = re.search(r'"gender"\s*:\s*"(MALE|FEMALE|CUSTOM)"', html, re.IGNORECASE)
    if m:
        meta['gender'] = m.group(1).capitalize()

    # Verified
    if '"is_verified":true' in html or '"isVerified":true' in html:
        meta['verified'] = True

    # Counts
    m = re.search(r'"friend_count"\s*:\s*(\d+)', html)
    if m: meta['friend_count'] = int(m.group(1))
    m = re.search(r'"follower_count"\s*:\s*(\d+)', html)
    if m: meta['follower_count'] = int(m.group(1))
    m = re.search(r'"member_count"\s*:\s*(\d+)', html)
    if m: meta['member_count'] = int(m.group(1))
    m = re.search(r'"page_likers"\s*:\s*\{\s*"count"\s*:\s*(\d+)', html)
    if m: meta['likes_count'] = int(m.group(1))
    m = re.search(r'"overall_star_rating"\s*:\s*([\d.]+)', html)
    if m: meta['rating'] = float(m.group(1))
    m = re.search(r'"rating_count"\s*:\s*(\d+)', html)
    if m: meta['review_count'] = int(m.group(1))

    # Privacy (groups)
    m = re.search(r'"privacy"\s*:\s*"(PUBLIC|CLOSED|SECRET|PRIVATE)"', html, re.IGNORECASE)
    if m: meta['privacy'] = m.group(1).capitalize()
    m = re.search(r'"visibility"\s*:\s*"(VISIBLE|HIDDEN)"', html, re.IGNORECASE)
    if m: meta['visibility'] = m.group(1).capitalize()

    # Description
    m = re.search(r'"group_description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"', html)
    if not m:
        m = re.search(r'"description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"', html)
    if m: meta['description'] = m.group(1)

    # Category
    m = re.search(r'"category_name"\s*:\s*"([^"]+)"', html)
    if m: meta['category'] = m.group(1)

    # Username
    m = re.search(r'"vanity"\s*:\s*"([^"]+)"', html)
    if not m: m = re.search(r'"username"\s*:\s*"([^"]+)"', html)
    if m and m.group(1): meta['username'] = m.group(1)

    # Status flags (for classification)
    snippet = html[:200000]
    s_lower = snippet.lower()
    if "this content isn't available" in s_lower or "this page isn't available" in s_lower:
        meta['status'] = 'unavailable'
    elif "this account has been deactivated" in s_lower or '"is_deactivated":true' in snippet:
        meta['status'] = 'deactivated'
    elif any(s in snippet for s in ['"is_profile_locked":true', '"profile_locked":true', 'ProfileLockSection']):
        meta['status'] = 'locked'
    elif '"is_private":true' in snippet:
        meta['status'] = 'private'
    elif '"is_published":false' in snippet:
        meta['status'] = 'unpublished'
    elif '"is_archived":true' in snippet:
        meta['status'] = 'archived'

    # Group-specific: rules, topics, admins, creation_time
    rules = []
    for m in re.finditer(r'"rule_title_text"\s*:\s*"([^"]+)"', html):
        r = m.group(1)
        if r not in rules: rules.append(r)
    if rules: meta['rules'] = rules

    topics = []
    for m in re.finditer(r'"group_topic"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html):
        t = m.group(1)
        if t not in topics: topics.append(t)
    if topics: meta['topics'] = topics

    m = re.search(r'"creation_time"\s*:\s*(\d+)', html)
    if m: meta['creation_time'] = int(m.group(1))

    # Lat/Lng
    m = re.search(r'"latitude"\s*:\s*([\d.-]+)', html)
    if m: meta['latitude'] = float(m.group(1))
    m = re.search(r'"longitude"\s*:\s*([\d.-]+)', html)
    if m: meta['longitude'] = float(m.group(1))

    return meta
