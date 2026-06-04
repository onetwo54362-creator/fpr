"""Page scraper — memory-efficient, no full HTML in memory."""

from __future__ import annotations

import logging

from .graphql_engine import GraphQLEngine
from .models import BusinessHours, PageData
from .rate_limiter import RateLimiter

log = logging.getLogger(__name__)

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


class PageScraper:
    def __init__(self, engine: GraphQLEngine, rate_limiter: RateLimiter,
                 scrape_about: bool = True, **kwargs):
        self.engine = engine
        self.rate_limiter = rate_limiter
        self.scrape_about = scrape_about

    async def scrape(self, url: str, page_id: str = '', username: str = '',
                     initial_data: dict = None) -> PageData:
        page = PageData(page_id=page_id, username=username, page_url=url)

        data = initial_data or await self.engine.fetch_and_extract(url)
        meta = data.get('meta', {})
        fields = data.get('profile_fields', [])

        self._apply_meta(meta, page)
        if fields:
            self._apply_fields(fields, page)
        self._classify(meta, page)
        await self.rate_limiter.on_request_complete()

        if self.scrape_about and page.page_type not in ('Unavailable Page', 'Unpublished Page'):
            await self._scrape_about_sections(page)

        return page

    def _apply_meta(self, meta: dict, page: PageData):
        if meta.get('name') and not page.name:
            page.name = meta['name']
        if meta.get('id') and not page.page_id:
            page.page_id = meta['id']
        if meta.get('username') and not page.username:
            page.username = meta['username']
        if meta.get('category') and not page.category:
            page.category = meta['category']
        if meta.get('profile_picture_url'):
            page.profile_picture_url = meta['profile_picture_url']
        if meta.get('cover_photo_url'):
            page.cover_photo_url = meta['cover_photo_url']
        if meta.get('verified'):
            page.verified = True
        if meta.get('likes_count'):
            page.likes_count = meta['likes_count']
        if meta.get('follower_count'):
            page.followers_count = meta['follower_count']
        if meta.get('rating'):
            page.rating = meta['rating']
        if meta.get('review_count'):
            page.review_count = meta['review_count']
        if meta.get('latitude'):
            page.latitude = meta['latitude']
        if meta.get('longitude'):
            page.longitude = meta['longitude']
        if meta.get('description') and not page.description:
            page.description = _unescape(meta['description'])

    def _classify(self, meta: dict, page: PageData):
        status = meta.get('status', '')
        if status == 'unavailable':
            page.page_type = 'Unavailable Page'
            return
        if status == 'unpublished':
            page.page_type = 'Unpublished Page'
            return
        is_verified = page.verified
        is_business = meta.get('category', '') != ''
        if is_verified and is_business:
            page.page_type = 'Verified Business Page'
        elif is_verified:
            page.page_type = 'Verified Page'
        elif is_business:
            page.page_type = 'Business Page'
        else:
            page.page_type = 'Public Page'

    async def _scrape_about_sections(self, page: PageData):
        for section_key in ABOUT_SECTIONS:
            if page.page_id and page.page_id.isdigit():
                url = f'https://www.facebook.com/profile.php?id={page.page_id}&sk={section_key}'
            elif page.username:
                url = f'https://www.facebook.com/{page.username}/{section_key}'
            else:
                continue
            try:
                result = await self.engine.fetch_and_extract(url)
                fields = result.get('profile_fields', [])
                if fields:
                    self._apply_fields(fields, page)
                await self.rate_limiter.on_request_complete()
                await self.rate_limiter.section_delay()
            except Exception as e:
                log.warning(f'  ⚠️ Failed {section_key}: {e}')

    def _apply_fields(self, fields: list, page: PageData):
        for field in fields:
            if not isinstance(field, dict):
                continue
            ft = field.get('field_type', '')
            title_text, content_text = _get_field_texts(field)
            value = _unescape(title_text or content_text)
            if not value:
                continue

            if ft == 'bio' and not page.short_description:
                page.short_description = value
            elif ft == 'description' and not page.description:
                page.description = value
            elif ft == 'category' and not page.category:
                page.category = value
            elif ft == 'current_city' and not page.address_city:
                page.address_city = value
            elif ft == 'address' and not page.full_address:
                page.full_address = value
            elif ft == 'website':
                if not page.website:
                    page.website = value
                elif value not in page.additional_websites and value != page.website:
                    page.additional_websites.append(value)
            elif ft == 'phone' and not page.phone:
                page.phone = value
            elif ft in ('email_address', 'email') and not page.email:
                if '@' in value:
                    page.email = value
            elif ft == 'screenname':
                val_lower = value.lower()
                if 'instagram' in val_lower or 'instagram' in content_text.lower():
                    if not page.instagram_url:
                        page.instagram_url = value
                elif 'twitter' in val_lower or 'x.com' in val_lower:
                    if not page.twitter_url:
                        page.twitter_url = value
                elif 'whatsapp' in val_lower:
                    if not page.whatsapp_number:
                        page.whatsapp_number = value
            elif ft == 'impressum' and not page.impressum:
                page.impressum = value
            elif ft == 'founded' and not page.founded:
                page.founded = value
            elif ft == 'mission' and not page.mission:
                page.mission = value
            elif ft == 'company_overview' and not page.company_overview:
                page.company_overview = value
            elif ft == 'products' and not page.products:
                page.products = value
            elif ft == 'price_range' and not page.price_range:
                page.price_range = value
            elif ft == 'hours':
                if not page.hours:
                    page.hours = BusinessHours()
                _parse_hours(value, page.hours)


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


def _unescape(text: str) -> str:
    if not text:
        return ''
    try:
        return text.encode().decode('unicode_escape', errors='ignore')
    except Exception:
        return text


def _parse_hours(value: str, hours: BusinessHours):
    val_lower = value.lower()
    day_map = {'mon': 'monday', 'tue': 'tuesday', 'wed': 'wednesday',
               'thu': 'thursday', 'fri': 'friday', 'sat': 'saturday', 'sun': 'sunday'}
    for abbr, attr in day_map.items():
        if abbr in val_lower and not getattr(hours, attr):
            setattr(hours, attr, value)
            return
