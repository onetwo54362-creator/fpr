"""Page scraper using Facebook's embedded profile_fields JSON structure.

Extracts data from each directory_* about section by parsing the structured
JSON in <script type="application/json"> tags, same approach as profile_scraper.
"""

from __future__ import annotations

import json
import logging
import re

from .graphql_engine import GraphQLEngine
from .models import BusinessHours, PageData
from .rate_limiter import RateLimiter

log = logging.getLogger(__name__)

INVALID_NAMES = {
    'WAWebOpusRecorderWorkerBundle', 'WebWizRecorderWorkerBundle',
    'WAWebWorkerBundle', 'CometMediaViewerPhoto', 'CometFeed',
    'RelayModern', 'MAWMainV4WebWorkerBundle', 'BlobStorageWorkerBundle',
    'ZenonSignalingSharedWorkerV2Bundle', 'Facebook', '', 'undefined', 'null',
    'About', 'Intro', 'Mentions',
}

ABOUT_SECTIONS = [
    'directory_intro', 'directory_category', 'directory_personal_details',
    'directory_basic_info', 'directory_links', 'directory_specialties',
    'directory_offers', 'directory_work', 'directory_education',
    'directory_activites', 'directory_interests', 'directory_travel',
    'directory_contact_info', 'directory_privacy_and_legal_info',
    'directory_names', 'directory_communities',
]


class PageScraper:
    """Scrapes Facebook page details."""

    def __init__(self, engine: GraphQLEngine, rate_limiter: RateLimiter,
                 scrape_about: bool = True, **kwargs):
        self.engine = engine
        self.rate_limiter = rate_limiter
        self.scrape_about = scrape_about

    async def scrape(self, url: str, page_id: str = '', username: str = '',
                     initial_html: str = '') -> PageData:
        page = PageData(page_id=page_id, username=username, page_url=url)

        html = initial_html or await self.engine.fetch_page_html(url)
        if html:
            self._extract_basic(html, page)
            self._classify_page(html, page)
            await self.rate_limiter.on_request_complete()

        if self.scrape_about and page.page_type not in ('Unavailable Page', 'Unpublished Page'):
            await self._scrape_all_about_sections(page)

        return page

    # =========================================================================
    # Basic extraction
    # =========================================================================
    def _extract_basic(self, html: str, page: PageData):
        # --- Name ---
        m = re.search(r'<title[^>]*>([^<]+)</title>', html)
        if m:
            name = m.group(1).strip()
            for sfx in (' | Facebook', ' - Facebook', ' \u2014 Facebook', ' \u00b7 Facebook'):
                name = name.replace(sfx, '')
            if name and name not in INVALID_NAMES and not name.startswith('WAWeb'):
                page.name = name

        if not page.name:
            m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
            if not m:
                m = re.search(r'content="([^"]+)"\s+property="og:title"', html)
            if m:
                name = m.group(1).replace(' | Facebook', '').replace(' - Facebook', '').strip()
                if name and name not in INVALID_NAMES:
                    page.name = name

        # --- Page ID ---
        if not page.page_id:
            c_user = ''
            if hasattr(self.engine, 'cookies') and isinstance(self.engine.cookies, dict):
                c_user = self.engine.cookies.get('c_user', '')
            for pat in [r'"pageID"\s*:\s*"(\d+)"', r'"page_id"\s*:\s*"(\d+)"',
                        r'"userID"\s*:\s*"(\d+)"', r'"entity_id"\s*:\s*"(\d+)"']:
                m = re.search(pat, html)
                if m and m.group(1) != c_user:
                    page.page_id = m.group(1)
                    break

        # --- Category ---
        m = re.search(r'"category_name"\s*:\s*"([^"]+)"', html)
        if not m:
            m = re.search(r'"category_type"\s*:\s*"([^"]+)"', html)
        if m:
            page.category = _unescape(m.group(1))

        # --- Profile picture ---
        m = re.search(r'"profile_picture_for_sticky_bar"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if m:
            page.profile_picture_url = m.group(1).replace('\\/', '/')
        elif not page.profile_picture_url:
            m = re.search(r'property="og:image"\s+content="([^"]+)"', html)
            if m:
                page.profile_picture_url = m.group(1)

        # --- Cover photo ---
        m = re.search(r'"coverPhoto"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if not m:
            m = re.search(r'"cover_photo"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if m:
            page.cover_photo_url = m.group(1).replace('\\/', '/')

        # --- Verified ---
        if '"is_verified":true' in html or '"isVerified":true' in html:
            page.verified = True

        # --- Likes ---
        m = re.search(r'"page_likers"\s*:\s*\{\s*"count"\s*:\s*(\d+)', html)
        if m:
            page.likes_count = int(m.group(1))

        # --- Followers ---
        m = re.search(r'"follower_count"\s*:\s*(\d+)', html)
        if m:
            page.followers_count = int(m.group(1))
        if not page.followers_count:
            m = re.search(r'"text"\s*:\s*"([\d,.KMB]+)\s+followers?"', html, re.IGNORECASE)
            if m:
                page.followers_count = _parse_count(m.group(1))

        # --- Checkins ---
        m = re.search(r'"checkin_count"\s*:\s*(\d+)', html)
        if not m:
            m = re.search(r'"checkins"\s*:\s*\{\s*"count"\s*:\s*(\d+)', html)
        if m:
            page.checkins_count = int(m.group(1))

        # --- Rating ---
        m = re.search(r'"overall_star_rating"\s*:\s*([\d.]+)', html)
        if m:
            page.rating = float(m.group(1))

        # --- Review count ---
        m = re.search(r'"rating_count"\s*:\s*(\d+)', html)
        if m:
            page.review_count = int(m.group(1))

        # --- Talking about ---
        m = re.search(r'"talking_about_count"\s*:\s*(\d+)', html)
        if m:
            page.talking_about_count = int(m.group(1))

        # --- Username ---
        if not page.username:
            m = re.search(r'"vanity"\s*:\s*"([^"]+)"', html)
            if not m:
                m = re.search(r'"username"\s*:\s*"([^"]+)"', html)
            if m and m.group(1):
                page.username = m.group(1)

        # --- Latitude/Longitude ---
        m = re.search(r'"latitude"\s*:\s*([\d.-]+)', html)
        if m:
            page.latitude = float(m.group(1))
        m = re.search(r'"longitude"\s*:\s*([\d.-]+)', html)
        if m:
            page.longitude = float(m.group(1))

        # --- Description from og:description ---
        m = re.search(r'property="og:description"\s+content="([^"]+)"', html)
        if not m:
            m = re.search(r'content="([^"]+)"\s+property="og:description"', html)
        if m:
            desc = _unescape(m.group(1))
            if desc and len(desc) > 10:
                page.description = desc

    # =========================================================================
    # Classification
    # =========================================================================
    def _classify_page(self, html: str, page: PageData):
        snippet = html[:200000]
        s_lower = snippet.lower()

        if "this page isn't available" in s_lower or "this content isn't available" in s_lower:
            page.page_type = 'Unavailable Page'
            return
        if '"is_published":false' in snippet:
            page.page_type = 'Unpublished Page'
            return

        is_verified = page.verified
        is_business = any(k in snippet for k in ['"is_business_page":true', 'BusinessPage',
                                                  '"page_type":"BUSINESS"'])
        is_community = '"is_community_page":true' in snippet or 'CommunityPage' in snippet

        if is_verified and is_business:
            page.page_type = 'Verified Business Page'
        elif is_verified:
            page.page_type = 'Verified Page'
        elif is_business:
            page.page_type = 'Business Page'
        elif is_community:
            page.page_type = 'Community Page'
        else:
            page.page_type = 'Public Page'

    # =========================================================================
    # About section scraping
    # =========================================================================
    async def _scrape_all_about_sections(self, page: PageData):
        for section_key in ABOUT_SECTIONS:
            if page.page_id and page.page_id.isdigit():
                url = f'https://www.facebook.com/profile.php?id={page.page_id}&sk={section_key}'
            elif page.username:
                url = f'https://www.facebook.com/{page.username}/{section_key}'
            else:
                continue

            try:
                html = await self.engine.fetch_page_html(url)
                if html:
                    self._extract_profile_fields(html, page, section_key)
                    await self.rate_limiter.on_request_complete()
                await self.rate_limiter.section_delay()
            except Exception as e:
                log.warning(f'  \u26a0\ufe0f Failed {section_key}: {e}')

    # =========================================================================
    # Core JSON extraction
    # =========================================================================
    def _extract_profile_fields(self, html: str, page: PageData, section_key: str):
        all_fields = _parse_profile_fields_json(html)

        for field in all_fields:
            if not isinstance(field, dict):
                continue

            ft = field.get('field_type', '')
            title_text, content_text = _get_field_texts(field)
            value = _unescape(title_text or content_text)
            if not value:
                continue

            # ── Map to PageData ──
            if ft == 'bio' and not page.short_description:
                page.short_description = value
            elif ft == 'description' and not page.description:
                page.description = value
            elif ft == 'category' and not page.category:
                page.category = value
            elif ft == 'current_city' and not page.address_city:
                page.address_city = value
            elif ft == 'address':
                if not page.full_address:
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


def _parse_hours(value: str, hours: BusinessHours):
    val_lower = value.lower()
    day_map = {'mon': 'monday', 'tue': 'tuesday', 'wed': 'wednesday',
               'thu': 'thursday', 'fri': 'friday', 'sat': 'saturday', 'sun': 'sunday'}
    for abbr, attr in day_map.items():
        if abbr in val_lower and not getattr(hours, attr):
            setattr(hours, attr, value)
            return
