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

    async def resolve_entity_type_and_id(self, url: str) -> tuple[str, Optional[str], Optional[str]]:
        try:
            headers = get_document_headers(user_agent=self._user_agent)
            self._total_requests += 1
            resp = await self._client.get(url, headers=headers)
            if resp.status_code != 200:
                return TargetType.UNKNOWN, None, None
            self.proxy_manager.on_success()
            html = resp.text
            entity_type = detect_entity_type_from_html(html)
            entity_id = None
            patterns_map = {
                TargetType.PAGE: [r'"pageID"\s*:\s*"(\d+)"', r'"page_id"\s*:\s*"(\d+)"'],
                TargetType.PROFILE: [r'"userID"\s*:\s*"(\d+)"', r'"profile_id"\s*:\s*"(\d+)"'],
                TargetType.GROUP: [r'"groupID"\s*:\s*"(\d+)"', r'"group_id"\s*:\s*"(\d+)"'],
            }
            patterns = patterns_map.get(entity_type, [])
            patterns.extend([r'"entity_id"\s*:\s*"(\d+)"', r'"ownerID"\s*:\s*"(\d+)"', r'"actorID"\s*:\s*"(\d+)"'])
            for p in patterns:
                m = re.search(p, html)
                if m and m.group(1) != self.cookies.get("c_user", ""):
                    entity_id = m.group(1)
                    break
            log.info(f"🔍 Resolved: type={entity_type}, id={entity_id}")
            return entity_type, entity_id, html
        except Exception as e:
            log.error(f"Error resolving entity: {e}")
            return TargetType.UNKNOWN, None, None

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
