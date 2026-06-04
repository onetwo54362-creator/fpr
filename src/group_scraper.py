"""Group scraper — details only, no member scraping.

Extracts group metadata from the main page and /about page.
Uses profile_fields JSON when available, plus regex fallbacks.
"""

from __future__ import annotations

import gc
import json
import logging
import re

from .graphql_engine import GraphQLEngine
from .models import GroupData, GroupMember
from .rate_limiter import RateLimiter

log = logging.getLogger(__name__)

INVALID_NAMES = {
    'WAWebOpusRecorderWorkerBundle', 'WebWizRecorderWorkerBundle',
    'WAWebWorkerBundle', 'CometMediaViewerPhoto', 'CometFeed',
    'RelayModern', 'MAWMainV4WebWorkerBundle', 'BlobStorageWorkerBundle',
    'ZenonSignalingSharedWorkerV2Bundle', 'Facebook', '', 'undefined', 'null',
    'About', 'Intro', 'Mentions',
}

# Privacy boilerplate — never store as group description
PRIVACY_BOILERPLATE = [
    "anyone can see who's in the group",
    "only members can see who",
    "anyone can find this group",
    "only members can see posts",
    "only current and former members",
    "visible - anyone can find this group",
    "members and visitors can see",
]


class GroupScraper:
    """Scrapes Facebook group details. Does NOT scrape member lists."""

    def __init__(self, engine: GraphQLEngine, rate_limiter: RateLimiter,
                 scrape_about: bool = True, **kwargs):
        self.engine = engine
        self.rate_limiter = rate_limiter
        self.scrape_about = scrape_about

    async def scrape(self, url: str, group_id: str = '',
                     initial_html: str = '') -> GroupData:
        group = GroupData(group_id=group_id, group_url=url)

        # Step 1: Main page
        html = initial_html or await self.engine.fetch_page_html(url)
        if html:
            self._extract_basic(html, group)
            self._classify_group(html, group)
            await self.rate_limiter.on_request_complete()
        del html
        del initial_html
        gc.collect()

        # Step 2: About page
        if self.scrape_about and group.group_type != 'Unavailable Group':
            await self._scrape_about(group)

        return group

    # =========================================================================
    # Basic extraction
    # =========================================================================
    def _extract_basic(self, html: str, group: GroupData):
        # --- Name ---
        m = re.search(r'<title[^>]*>([^<]+)</title>', html)
        if m:
            name = m.group(1).strip()
            for sfx in (' | Facebook', ' - Facebook', ' \u2014 Facebook', ' \u00b7 Facebook',
                         ' | Group', ' group'):
                name = name.replace(sfx, '')
            if name and name not in INVALID_NAMES and not name.startswith('WAWeb'):
                group.name = name

        if not group.name:
            m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
            if not m:
                m = re.search(r'content="([^"]+)"\s+property="og:title"', html)
            if m:
                name = m.group(1).replace(' | Facebook', '').replace(' - Facebook', '').strip()
                if name and name not in INVALID_NAMES:
                    group.name = name

        # --- Group ID ---
        if not group.group_id:
            for pat in [r'"groupID"\s*:\s*"(\d+)"', r'"group_id"\s*:\s*"(\d+)"',
                        r'"entity_id"\s*:\s*"(\d+)"', r'/groups/(\d+)']:
                m = re.search(pat, html)
                if m:
                    group.group_id = m.group(1)
                    break

        # --- Privacy ---
        m = re.search(r'"privacy"\s*:\s*"(PUBLIC|CLOSED|SECRET|PRIVATE)"', html, re.IGNORECASE)
        if m:
            group.privacy = m.group(1).capitalize()

        # --- Visibility ---
        m = re.search(r'"visibility"\s*:\s*"(VISIBLE|HIDDEN)"', html, re.IGNORECASE)
        if m:
            group.visibility = m.group(1).capitalize()

        # --- Join mode ---
        if '"join_action_type":"APPLY"' in html or '"approval_required":true' in html:
            group.join_mode = 'Approval Required'
        elif '"join_action_type":"JOIN"' in html:
            group.join_mode = 'Open'

        # --- Member count ---
        m = re.search(r'"member_count"\s*:\s*(\d+)', html)
        if m:
            group.member_count = int(m.group(1))
        if not group.member_count:
            m = re.search(r'"text"\s*:\s*"([\d,.KMB]+)\s+(?:total\s+)?members?"', html, re.IGNORECASE)
            if m:
                group.member_count = _parse_count(m.group(1))

        # --- Description ---
        m = re.search(r'"group_description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"', html)
        if not m:
            m = re.search(r'"description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"', html)
        if m:
            desc = _unescape(m.group(1))
            if not _is_privacy_boilerplate(desc):
                group.description = desc

        # --- Cover photo ---
        m = re.search(r'"coverPhoto"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if not m:
            m = re.search(r'"cover_photo"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if m:
            group.cover_photo_url = m.group(1).replace('\\/', '/')

        # --- Posts per day/month ---
        m = re.search(r'"new_post_count"\s*:\s*\{\s*"count"\s*:\s*(\d+)', html)
        if m:
            group.posts_per_day = float(m.group(1))
        m = re.search(r'"text"\s*:\s*"([\d,.]+)\s+new posts? (?:a|per) month"', html, re.IGNORECASE)
        if m:
            group.posts_per_month = float(m.group(1).replace(',', ''))

        # --- Created at ---
        m = re.search(r'"creation_time"\s*:\s*(\d+)', html)
        if m:
            import datetime
            try:
                ts = int(m.group(1))
                group.created_at = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()
            except (ValueError, OverflowError):
                pass

        # --- Rules ---
        for m in re.finditer(r'"rule_title_text"\s*:\s*"([^"]+)"', html):
            rule = _unescape(m.group(1))
            if rule and rule not in group.rules:
                group.rules.append(rule)

        # --- Topics ---
        for m in re.finditer(r'"group_topic"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html):
            topic = _unescape(m.group(1))
            if topic and topic not in group.topics:
                group.topics.append(topic)

        # --- Location ---
        m = re.search(r'"location"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
        if m and not group.location:
            group.location = _unescape(m.group(1))

        # --- History ---
        m = re.search(r'"group_history"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"', html)
        if m and not group.history:
            group.history = _unescape(m.group(1))

        # --- Admins from JSON ---
        for m in re.finditer(r'"role"\s*:\s*"(ADMIN|MODERATOR)"[^}]*"name"\s*:\s*"([^"]+)"', html):
            role = m.group(1).capitalize()
            name = _unescape(m.group(2))
            if name in INVALID_NAMES:
                continue
            member = GroupMember(name=name, role=role)
            target = group.admins if role == 'Admin' else group.moderators
            if not any(a.name == name for a in target):
                target.append(member)

        # Also try the reverse pattern
        for m in re.finditer(r'"name"\s*:\s*"([^"]+)"[^}]*"role"\s*:\s*"(ADMIN|MODERATOR)"', html):
            name = _unescape(m.group(1))
            role = m.group(2).capitalize()
            if name in INVALID_NAMES:
                continue
            member = GroupMember(name=name, role=role)
            target = group.admins if role == 'Admin' else group.moderators
            if not any(a.name == name for a in target):
                target.append(member)

    # =========================================================================
    # Classification
    # =========================================================================
    def _classify_group(self, html: str, group: GroupData):
        snippet = html[:200000]
        s_lower = snippet.lower()

        if "this group is unavailable" in s_lower or "this content isn't available" in s_lower:
            group.group_type = 'Unavailable Group'
            return

        if '"is_archived":true' in snippet:
            group.group_type = 'Archived Group'
            return

        privacy = group.privacy.upper() if group.privacy else ''
        visibility = group.visibility.upper() if group.visibility else ''

        if visibility == 'HIDDEN' or privacy == 'SECRET':
            group.group_type = 'Hidden Group'
        elif privacy in ('CLOSED', 'PRIVATE'):
            group.group_type = 'Private Group'
        else:
            group.group_type = 'Public Group'

    # =========================================================================
    # About page scraping
    # =========================================================================
    async def _scrape_about(self, group: GroupData):
        gid = group.group_id or ''
        if not gid:
            # Try to extract from URL
            m = re.search(r'/groups/([^/?]+)', group.group_url)
            if m:
                gid = m.group(1)

        if not gid:
            return

        about_url = f'https://www.facebook.com/groups/{gid}/about'
        try:
            html = await self.engine.fetch_page_html(about_url)
            if html:
                # Re-extract with about page data (richer content)
                self._extract_basic(html, group)
                # Also try profile_fields extraction
                self._extract_profile_fields(html, group)
                del html  # Free memory immediately
                gc.collect()
                await self.rate_limiter.on_request_complete()
        except Exception as e:
            log.warning(f'  \u26a0\ufe0f Failed group about: {e}')

    def _extract_profile_fields(self, html: str, group: GroupData):
        """Parse profile_fields if available on the about page."""
        all_fields = _parse_profile_fields_json(html)

        for field in all_fields:
            if not isinstance(field, dict):
                continue

            ft = field.get('field_type', '')
            title_text, content_text = _get_field_texts(field)
            value = _unescape(title_text or content_text)
            if not value:
                continue

            if ft == 'bio' and not group.description:
                if not _is_privacy_boilerplate(value):
                    group.description = value
            elif ft == 'current_city' and not group.location:
                group.location = value


# =============================================================================
# Shared helpers
# =============================================================================

def _parse_profile_fields_json(html: str) -> list[dict]:
    all_fields = []
    for m in re.finditer(r'"profile_fields"\s*:\s*\{\s*"nodes"\s*:\s*\[', html):
        start = m.end() - 1
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


def _get_field_texts(field: dict) -> tuple[str, str]:
    title_text = ''
    content_text = ''
    title = field.get('title')
    if isinstance(title, dict):
        title_text = title.get('text', '')
    elif isinstance(title, str):
        title_text = title
    tc = field.get('text_content')
    if isinstance(tc, dict) and tc:
        content_text = tc.get('text', '')
    return title_text, content_text


def _is_privacy_boilerplate(text: str) -> bool:
    t_lower = text.lower().strip()
    return any(bp in t_lower for bp in PRIVACY_BOILERPLATE)


def _unescape(text: str) -> str:
    if not text:
        return ''
    try:
        return text.encode().decode('unicode_escape', errors='ignore')
    except Exception:
        return text


def _parse_count(text: str) -> int:
    text = text.replace(',', '').strip()
    mult = 1
    if text.upper().endswith('K'):
        mult, text = 1000, text[:-1]
    elif text.upper().endswith('M'):
        mult, text = 1_000_000, text[:-1]
    elif text.upper().endswith('B'):
        mult, text = 1_000_000_000, text[:-1]
    try:
        return int(float(text) * mult)
    except ValueError:
        return 0
