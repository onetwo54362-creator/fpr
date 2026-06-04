"""Profile scraper — memory-efficient, no full HTML in memory.

Uses engine.fetch_and_extract() which returns only the tiny JSON data
(profile_fields + meta) instead of the full 2-5MB HTML page.
"""

from __future__ import annotations

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

        # Apply metadata
        self._apply_meta(meta, profile)
        # Apply any profile_fields from main page
        if fields:
            self._apply_fields(fields, profile)
        # Classify
        self._classify(meta, profile)
        await self.rate_limiter.on_request_complete()

        # Step 2: Scrape about sections (only compact data returned)
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

    def _classify(self, meta: dict, profile: ProfileData):
        status = meta.get('status', '')
        if status == 'deactivated' or status == 'unavailable':
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
        for section_key in ABOUT_SECTIONS:
            if profile.user_id and profile.user_id.isdigit():
                url = f'https://www.facebook.com/profile.php?id={profile.user_id}&sk={section_key}'
            else:
                url = f'https://www.facebook.com/{profile.username}/{section_key}'
            try:
                # Returns ~5KB dict, NOT 2-5MB HTML
                result = await self.engine.fetch_and_extract(url)
                fields = result.get('profile_fields', [])
                if fields:
                    self._apply_fields(fields, profile)
                await self.rate_limiter.on_request_complete()
                await self.rate_limiter.section_delay()
            except Exception as e:
                log.warning(f'  ⚠️ Failed {section_key}: {e}')

    def _apply_fields(self, fields: list, profile: ProfileData):
        for field in fields:
            if not isinstance(field, dict):
                continue
            ft = field.get('field_type', '')
            title_text, content_text = _get_field_texts(field)
            value = _unescape(title_text or content_text)
            if not value:
                continue

            if ft == 'bio' and not profile.bio:
                profile.bio = value
            elif ft == 'intro' and not profile.intro:
                profile.intro = value
            elif ft == 'current_city' and not profile.current_city:
                profile.current_city = value
            elif ft == 'hometown' and not profile.hometown:
                profile.hometown = value
            elif ft == 'birthday' and not profile.birthday:
                profile.birthday = value
            elif ft == 'relationship_status':
                if not profile.relationship_status:
                    profile.relationship_status = value
                    if content_text and content_text != value:
                        profile.relationship_status += f' — {_unescape(content_text)}'
            elif ft == 'significant_other' and not profile.significant_other:
                profile.significant_other = value
            elif ft == 'family_member':
                relationship = _extract_relationship(field, content_text, value)
                if value and not any(f.name == value for f in profile.family_members):
                    profile.family_members.append(FamilyMember(name=value, relationship=relationship))
            elif ft == 'gender' and not profile.gender:
                profile.gender = value
            elif ft == 'languages':
                if not profile.languages:
                    for lang in re.split(r',\s*|\s+and\s+', value):
                        lang = lang.strip()
                        if lang and lang not in profile.languages:
                            profile.languages.append(lang)
            elif ft == 'favorite_quotes' and not profile.favorite_quotes:
                profile.favorite_quotes = value
            elif ft == 'interested_in' and not profile.interested_in:
                profile.interested_in = value
            elif ft == 'political_views' and not profile.political_views:
                profile.political_views = value
            elif ft == 'religious_views' and not profile.religious_views:
                profile.religious_views = value
            elif ft == 'work':
                company = value
                position = _unescape(content_text) if content_text and content_text != company else ''
                if company and not any(w.company == company for w in profile.work):
                    profile.work.append(WorkExperience(company=company, position=position))
            elif ft == 'education':
                school = value
                concentration = _unescape(content_text) if content_text and content_text != school else ''
                if school and not any(e.school == school for e in profile.education):
                    profile.education.append(Education(school=school, concentration=concentration))
            elif ft == 'website':
                if value not in profile.websites:
                    profile.websites.append(value)
            elif ft == 'screenname':
                if value not in profile.social_links and 'facebook.com' not in value:
                    profile.social_links.append(value)
            elif ft == 'phone':
                if value not in profile.phone_numbers:
                    profile.phone_numbers.append(value)
            elif ft in ('email_address', 'email'):
                if value not in profile.emails and '@' in value:
                    profile.emails.append(value)
            elif ft == 'name_pronunciation':
                extra = f'Pronunciation: {value}'
                profile.intro = f'{profile.intro}\n{extra}' if profile.intro else extra
            elif ft == 'other_names':
                extra = f'Other name: {value}'
                if not profile.intro:
                    profile.intro = extra
                elif 'Other name' not in profile.intro:
                    profile.intro += f'\n{extra}'
                else:
                    profile.intro += f', {value}'
            elif ft == 'category':
                if not profile.bio:
                    profile.bio = value
            elif ft == 'impressum':
                extra = f'Impressum: {value}'
                profile.intro = f'{profile.intro}\n{extra}' if profile.intro else extra
            elif ft in ('travel', 'places_lived'):
                if value not in profile.places_lived:
                    profile.places_lived.append(value)
            elif ft == 'directory_item':
                if value not in profile.life_events:
                    profile.life_events.append(value)


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


def _extract_relationship(field: dict, content_text: str, name: str) -> str:
    relationship = ''
    if isinstance(field.get('list_item_groups'), list):
        for group in field['list_item_groups']:
            if isinstance(group, dict):
                for item in group.get('list_items', []):
                    if isinstance(item, dict):
                        tc = item.get('text_content')
                        if isinstance(tc, dict):
                            relationship = tc.get('text', '')
    if not relationship and content_text and content_text != name:
        relationship = content_text
    return relationship


def _unescape(text: str) -> str:
    if not text:
        return ''
    try:
        return text.encode().decode('unicode_escape', errors='ignore')
    except Exception:
        return text
