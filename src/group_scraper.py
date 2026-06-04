"""Group scraper — details only, no member scraping. Memory-efficient."""

from __future__ import annotations

import datetime
import logging
import re

from .graphql_engine import GraphQLEngine
from .models import GroupData, GroupMember
from .rate_limiter import RateLimiter

log = logging.getLogger(__name__)

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
    def __init__(self, engine: GraphQLEngine, rate_limiter: RateLimiter,
                 scrape_about: bool = True, **kwargs):
        self.engine = engine
        self.rate_limiter = rate_limiter
        self.scrape_about = scrape_about

    async def scrape(self, url: str, group_id: str = '',
                     initial_data: dict = None) -> GroupData:
        group = GroupData(group_id=group_id, group_url=url)

        data = initial_data or await self.engine.fetch_and_extract(url)
        meta = data.get('meta', {})
        fields = data.get('profile_fields', [])

        self._apply_meta(meta, group)
        if fields:
            self._apply_fields(fields, group)
        self._classify(meta, group)
        await self.rate_limiter.on_request_complete()

        if self.scrape_about and group.group_type != 'Unavailable Group':
            await self._scrape_about(group)

        return group

    def _apply_meta(self, meta: dict, group: GroupData):
        if meta.get('name') and not group.name:
            group.name = meta['name']
        if meta.get('id') and not group.group_id:
            group.group_id = meta['id']
        if meta.get('cover_photo_url'):
            group.cover_photo_url = meta['cover_photo_url']
        if meta.get('privacy'):
            group.privacy = meta['privacy']
        if meta.get('visibility'):
            group.visibility = meta['visibility']
        if meta.get('member_count'):
            group.member_count = meta['member_count']
        if meta.get('description'):
            desc = _unescape(meta['description'])
            if not _is_boilerplate(desc):
                group.description = desc
        if meta.get('rules'):
            for r in meta['rules']:
                if r not in group.rules:
                    group.rules.append(r)
        if meta.get('topics'):
            for t in meta['topics']:
                if t not in group.topics:
                    group.topics.append(t)
        if meta.get('creation_time') and not group.created_at:
            try:
                ts = meta['creation_time']
                group.created_at = datetime.datetime.fromtimestamp(
                    ts, tz=datetime.timezone.utc
                ).isoformat()
            except (ValueError, OverflowError):
                pass

    def _classify(self, meta: dict, group: GroupData):
        status = meta.get('status', '')
        if status in ('unavailable',):
            group.group_type = 'Unavailable Group'
            return
        if status == 'archived':
            group.group_type = 'Archived Group'
            return
        privacy = (group.privacy or '').upper()
        visibility = (group.visibility or '').upper()
        if visibility == 'HIDDEN' or privacy == 'SECRET':
            group.group_type = 'Hidden Group'
        elif privacy in ('CLOSED', 'PRIVATE'):
            group.group_type = 'Private Group'
        else:
            group.group_type = 'Public Group'

    async def _scrape_about(self, group: GroupData):
        gid = group.group_id or ''
        if not gid:
            m = re.search(r'/groups/([^/?]+)', group.group_url)
            if m:
                gid = m.group(1)
        if not gid:
            return

        about_url = f'https://www.facebook.com/groups/{gid}/about'
        try:
            result = await self.engine.fetch_and_extract(about_url)
            meta = result.get('meta', {})
            fields = result.get('profile_fields', [])
            self._apply_meta(meta, group)
            if fields:
                self._apply_fields(fields, group)
            await self.rate_limiter.on_request_complete()
        except Exception as e:
            log.warning(f'  ⚠️ Failed group about: {e}')

    def _apply_fields(self, fields: list, group: GroupData):
        for field in fields:
            if not isinstance(field, dict):
                continue
            ft = field.get('field_type', '')
            title_text, content_text = _get_field_texts(field)
            value = _unescape(title_text or content_text)
            if not value:
                continue

            if ft == 'bio' and not group.description:
                if not _is_boilerplate(value):
                    group.description = value
            elif ft == 'current_city' and not group.location:
                group.location = value


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


def _is_boilerplate(text: str) -> bool:
    t_lower = text.lower().strip()
    return any(bp in t_lower for bp in PRIVACY_BOILERPLATE)


def _unescape(text: str) -> str:
    if not text:
        return ''
    try:
        return text.encode().decode('unicode_escape', errors='ignore')
    except Exception:
        return text
