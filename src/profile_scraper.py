"""Profile scraper — memory-efficient, handles infinite URLs at 128MB RAM.

Uses engine.fetch_and_extract() which returns only the tiny JSON data
(profile_fields + meta) instead of the full 2-5MB HTML page.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

from .graphql_engine import GraphQLEngine
from .models import Education, FamilyMember, ProfileData, WorkExperience
from .rate_limiter import RateLimiter

log = logging.getLogger(__name__)

# Essential about sections (each is an HTTP request)
ABOUT_SECTIONS = [
    'directory_personal_details',
    'directory_work',
    'directory_education',
    'directory_contact_info',
    'directory_links',
    'directory_basic_info',
    'directory_names',
    'directory_privacy_and_legal_info',
]


class ProfileScraper:
    def __init__(self, engine: GraphQLEngine, rate_limiter: RateLimiter,
                 scrape_about: bool = True, **kwargs):
        self.engine = engine
        self.rate_limiter = rate_limiter
        self.scrape_about = scrape_about

    async def scrape(self, url: str, username: str = '',
                     user_id: str = '', initial_data: dict = None) -> ProfileData:
        profile = ProfileData(username=username, user_id=user_id, profile_url=url)

        # Step 1: Use initial_data from resolve_entity, or fetch fresh
        data = initial_data or await self.engine.fetch_and_extract(url)
        meta = data.get('meta', {})
        fields = data.get('profile_fields', [])

        self._apply_meta(meta, profile)
        if fields:
            self._apply_fields(fields, profile)
        self._classify(meta, profile)
        await self.rate_limiter.on_request_complete()

        # Step 2: Scrape about sections (only compact data returned per section)
        skip = ('Locked Profile', 'Deactivated Profile', 'Unavailable Profile')
        if self.scrape_about and profile.profile_type not in skip:
            await self._scrape_about_sections(profile)
            self._reclassify(profile)

        return profile

    def _apply_meta(self, meta: dict, profile: ProfileData):
        if meta.get('name') and not profile.name:
            profile.name = meta['name']
        if meta.get('id') and not profile.user_id:
            profile.user_id = meta['id']
        if meta.get('username') and not profile.username:
            profile.username = meta['username']
        if meta.get('profile_picture_url'):
            profile.profile_picture_url = meta['profile_picture_url']
        if meta.get('cover_photo_url'):
            profile.cover_photo_url = meta['cover_photo_url']
        if meta.get('gender') and not profile.gender:
            profile.gender = meta['gender']
        if meta.get('verified'):
            profile.verified = True
        if meta.get('friend_count'):
            profile.friends_count = meta['friend_count']
        if meta.get('follower_count'):
            profile.followers_count = meta['follower_count']
        # Also extract birthday/relationship from meta if present
        if meta.get('birthday') and not profile.birthday:
            profile.birthday = meta['birthday']
        if meta.get('relationship_status') and not profile.relationship_status:
            profile.relationship_status = meta['relationship_status']

    def _classify(self, meta: dict, profile: ProfileData):
        status = meta.get('status', '')
        if status in ('deactivated', 'unavailable'):
            profile.profile_type = 'Deactivated Profile'
        elif status == 'locked':
            profile.profile_type = 'Locked Profile'
        elif status == 'private':
            profile.profile_type = 'Private Profile'
        else:
            profile.profile_type = 'Public Profile'

    def _reclassify(self, profile: ProfileData):
        if profile.profile_type == 'Public Profile':
            has_data = any([
                profile.bio, profile.current_city, profile.hometown,
                len(profile.work) > 0, len(profile.education) > 0,
                profile.birthday, profile.relationship_status,
                len(profile.phone_numbers) > 0, len(profile.emails) > 0,
                len(profile.family_members) > 0, len(profile.websites) > 0,
            ])
            if not has_data:
                profile.profile_type = 'Limited Profile'

    async def _scrape_about_sections(self, profile: ProfileData):
        urls_to_fetch = []
        for section_key in ABOUT_SECTIONS:
            if profile.user_id and profile.user_id.isdigit():
                url = f'https://www.facebook.com/profile.php?id={profile.user_id}&sk={section_key}'
            elif profile.username:
                url = f'https://www.facebook.com/{profile.username}/{section_key}'
            else:
                continue
            urls_to_fetch.append((section_key, url))

        if not urls_to_fetch:
            return

        # Fetch sections concurrently but limit to 3 at a time to prevent OOM
        sem = asyncio.Semaphore(3)

        async def fetch_section(section_key, url):
            async with sem:
                try:
                    result = await self.engine.fetch_and_extract(url)
                    # Random small stagger to avoid completely simultaneous requests
                    await asyncio.sleep(random.uniform(0.2, 0.8))
                    return section_key, result
                except Exception as e:
                    log.warning(f'  ⚠️ Failed {section_key}: {e}')
                    return section_key, None

        import random
        tasks = [asyncio.create_task(fetch_section(k, u)) for k, u in urls_to_fetch]
        results = await asyncio.gather(*tasks)

        for section_key, result in results:
            if not result:
                continue
            fields = result.get('profile_fields', [])
            meta = result.get('meta', {})
            if fields:
                self._apply_fields(fields, profile)
            self._apply_meta(meta, profile)
            
        # Count this whole batch of about sections as 1 request for the rate limiter
        await self.rate_limiter.on_request_complete()

    def _apply_fields(self, fields: list, profile: ProfileData):
        for field in fields:
            if not isinstance(field, dict):
                continue
            ft = field.get('field_type', '')
            title_text, content_text = _get_field_texts(field)
            value = _unescape(title_text or content_text)
            if not value:
                continue

            # ---- Bio / Intro ----
            if ft in ('bio', 'about') and not profile.bio:
                profile.bio = value
            elif ft == 'intro' and not profile.intro:
                profile.intro = value

            # ---- Location ----
            elif ft == 'current_city' and not profile.current_city:
                profile.current_city = value
            elif ft == 'hometown' and not profile.hometown:
                profile.hometown = value
            elif ft in ('travel', 'places_lived'):
                if value not in profile.places_lived:
                    profile.places_lived.append(value)

            # ---- Birthday ----
            elif ft in ('birthday', 'date', 'birth_date', 'date_of_birth') and not profile.birthday:
                profile.birthday = value

            # ---- Relationship ----
            elif ft in ('relationship_status', 'relationship', 'status'):
                if not profile.relationship_status:
                    profile.relationship_status = value
                    if content_text and content_text != value and content_text != title_text:
                        profile.relationship_status += f' — {_unescape(content_text)}'
            elif ft == 'significant_other' and not profile.significant_other:
                profile.significant_other = value

            # ---- Family ----
            elif ft in ('family_member', 'family'):
                _add_family_member(field, value, content_text, profile)

            # ---- Personal info ----
            elif ft == 'gender' and not profile.gender:
                profile.gender = value
            elif ft == 'languages':
                if not profile.languages:
                    for lang in re.split(r',\s*|\s+and\s+', value):
                        lang = lang.strip()
                        if lang and lang not in profile.languages:
                            profile.languages.append(lang)
            elif ft in ('favorite_quotes', 'quotes') and not profile.favorite_quotes:
                profile.favorite_quotes = value
            elif ft == 'interested_in' and not profile.interested_in:
                profile.interested_in = value
            elif ft in ('political_views', 'political') and not profile.political_views:
                profile.political_views = value
            elif ft in ('religious_views', 'religion', 'religious') and not profile.religious_views:
                profile.religious_views = value

            # ---- Work ----
            elif ft == 'work':
                _add_work(value, content_text, profile)

            # ---- Education ----
            elif ft == 'education':
                _add_education(value, content_text, profile)

            # ---- Contact ----
            elif ft == 'website':
                if value not in profile.websites:
                    profile.websites.append(value)
            elif ft in ('screenname', 'social_link'):
                if value not in profile.social_links and 'facebook.com' not in value:
                    profile.social_links.append(value)
            elif ft in ('phone', 'phone_number', 'mobile_phone'):
                if value not in profile.phone_numbers:
                    profile.phone_numbers.append(value)
            elif ft in ('email_address', 'email', 'contact_email'):
                if value not in profile.emails and '@' in value:
                    profile.emails.append(value)

            # ---- Other ----
            elif ft == 'name_pronunciation':
                extra = f'Pronunciation: {value}'
                profile.intro = f'{profile.intro}\n{extra}' if profile.intro else extra
            elif ft in ('other_names', 'nickname', 'maiden_name', 'alternate_name'):
                extra = f'Other name: {value}'
                if not profile.intro:
                    profile.intro = extra
                elif 'Other name' not in profile.intro:
                    profile.intro += f'\n{extra}'
                else:
                    profile.intro += f', {value}'
            elif ft == 'category' and not profile.bio:
                profile.bio = value
            elif ft == 'impressum':
                extra = f'Impressum: {value}'
                profile.intro = f'{profile.intro}\n{extra}' if profile.intro else extra
            elif ft == 'directory_item':
                if value not in profile.life_events:
                    profile.life_events.append(value)


def _add_family_member(field: dict, name: str, content_text: str, profile: ProfileData):
    """Extract family member with relationship from various JSON structures."""
    relationship = ''
    # Try list_item_groups first (grouped family entries)
    if isinstance(field.get('list_item_groups'), list):
        for group in field['list_item_groups']:
            if isinstance(group, dict):
                for item in group.get('list_items', []):
                    if isinstance(item, dict):
                        tc = item.get('text_content')
                        if isinstance(tc, dict):
                            relationship = tc.get('text', '')
    # Try subtitle
    if not relationship:
        subtitle = field.get('subtitle')
        if isinstance(subtitle, dict):
            relationship = subtitle.get('text', '')
    # Try text_content as relationship
    if not relationship and content_text and content_text != name:
        relationship = _unescape(content_text)
    # Add if not duplicate
    if name and not any(f.name == name for f in profile.family_members):
        profile.family_members.append(FamilyMember(name=name, relationship=relationship))


def _add_work(value: str, content_text: str, profile: ProfileData):
    """Parse work entry — handles 'Position at Company' format."""
    company = value
    position = ''
    # Parse 'Manager at Company' or 'Former Manager at Company'
    if ' at ' in value:
        parts = value.split(' at ', 1)
        raw_position = parts[0].strip()
        company = parts[1].strip()
        # Remove 'Former ' prefix from position
        position = re.sub(r'^Former\s+', '', raw_position)
    elif content_text and content_text != value:
        position = _unescape(content_text)
    if company and not any(w.company == company for w in profile.work):
        profile.work.append(WorkExperience(company=company, position=position))


def _add_education(value: str, content_text: str, profile: ProfileData):
    """Parse education — handles 'Studied X at Y', 'Went to Y' formats."""
    school = value
    concentration = ''
    if ' at ' in value:
        parts = value.split(' at ', 1)
        raw = parts[0].strip()
        school = parts[1].strip()
        concentration = re.sub(r'^Studied\s+', '', raw)
    elif value.startswith('Went to '):
        school = value[len('Went to '):].strip()
    elif content_text and content_text != value:
        concentration = _unescape(content_text)
    if school and not any(e.school == school for e in profile.education):
        profile.education.append(Education(school=school, concentration=concentration))


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
    # Also check subtitle as potential text_content
    if not content_text:
        sub = field.get('subtitle')
        if isinstance(sub, dict):
            content_text = sub.get('text', '')
    return title_text, content_text


def _unescape(text: str) -> str:
    if not text:
        return ''
    try:
        return text.encode().decode('unicode_escape', errors='ignore')
    except Exception:
        return text
