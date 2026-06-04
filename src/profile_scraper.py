"""Profile scraper using Facebook's embedded profile_fields JSON structure.

Extracts data from each directory_* about section by parsing the structured
JSON in <script type="application/json"> tags. Each section contains
"profile_fields":{"nodes":[...]} with field_type identifiers.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from .graphql_engine import GraphQLEngine
from .models import Education, FamilyMember, ProfileData, WorkExperience
from .rate_limiter import RateLimiter

log = logging.getLogger(__name__)

# Facebook internal module names that appear as "name" keys — never a real person
INVALID_NAMES = {
    'WAWebOpusRecorderWorkerBundle', 'WebWizRecorderWorkerBundle',
    'WAWebWorkerBundle', 'CometMediaViewerPhoto', 'CometFeed',
    'RelayModern', 'MAWMainV4WebWorkerBundle', 'BlobStorageWorkerBundle',
    'ZenonSignalingSharedWorkerV2Bundle', 'Facebook', '', 'undefined', 'null',
    'About', 'Intro', 'Mentions',
}

# Sections to crawl for about data
ABOUT_SECTIONS = [
    'directory_intro', 'directory_category', 'directory_personal_details',
    'directory_basic_info', 'directory_links', 'directory_specialties',
    'directory_offers', 'directory_work', 'directory_education',
    'directory_activites', 'directory_interests', 'directory_travel',
    'directory_contact_info', 'directory_privacy_and_legal_info',
    'directory_names', 'directory_communities',
]


class ProfileScraper:
    """Scrapes Facebook profile pages for comprehensive personal data."""

    def __init__(self, engine: GraphQLEngine, rate_limiter: RateLimiter,
                 scrape_about: bool = True, **kwargs):
        self.engine = engine
        self.rate_limiter = rate_limiter
        self.scrape_about = scrape_about

    async def scrape(self, url: str, username: str = '',
                     user_id: str = '', initial_html: str = '') -> ProfileData:
        """Main entry point — scrape a profile URL."""
        profile = ProfileData(username=username, user_id=user_id, profile_url=url)

        # Step 1: Fetch main page
        html = initial_html or await self.engine.fetch_page_html(url)
        if html:
            self._extract_basic(html, profile)
            self._classify_profile(html, profile)
            await self.rate_limiter.on_request_complete()

        # Step 2: Scrape all about sections
        skip_types = ('Locked Profile', 'Deactivated Profile', 'Unavailable Profile')
        if self.scrape_about and profile.profile_type not in skip_types:
            await self._scrape_all_about_sections(profile)
            self._reclassify(profile)

        return profile

    # =========================================================================
    # Basic info extraction from main page HTML
    # =========================================================================
    def _extract_basic(self, html: str, profile: ProfileData):
        """Extract name, ID, pictures, gender, counts from the main page."""
        # --- Name from <title> ---
        m = re.search(r'<title[^>]*>([^<]+)</title>', html)
        if m:
            name = m.group(1).strip()
            for sfx in (' | Facebook', ' - Facebook', ' \u2014 Facebook', ' \u00b7 Facebook'):
                name = name.replace(sfx, '')
            if name and name not in INVALID_NAMES and not name.startswith('WAWeb'):
                profile.name = name

        # --- Fallback: og:title ---
        if not profile.name:
            m = re.search(r'property="og:title"\s+content="([^"]+)"', html)
            if not m:
                m = re.search(r'content="([^"]+)"\s+property="og:title"', html)
            if m:
                name = m.group(1).replace(' | Facebook', '').replace(' - Facebook', '').strip()
                if name and name not in INVALID_NAMES:
                    profile.name = name

        # --- User ID ---
        if not profile.user_id:
            c_user = ''
            if hasattr(self.engine, 'cookies') and isinstance(self.engine.cookies, dict):
                c_user = self.engine.cookies.get('c_user', '')
            for pat in [r'"userID"\s*:\s*"(\d+)"', r'"user_id"\s*:\s*"(\d+)"',
                        r'"ownerID"\s*:\s*"(\d+)"', r'"pageID"\s*:\s*"(\d+)"',
                        r'"entity_id"\s*:\s*"(\d+)"']:
                m = re.search(pat, html)
                if m and m.group(1) != c_user:
                    profile.user_id = m.group(1)
                    break

        # --- Profile picture ---
        m = re.search(r'"profile_picture_for_sticky_bar"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if m:
            profile.profile_picture_url = m.group(1).replace('\\/', '/')
        elif not profile.profile_picture_url:
            m = re.search(r'property="og:image"\s+content="([^"]+)"', html)
            if m:
                profile.profile_picture_url = m.group(1)

        # --- Cover photo ---
        m = re.search(r'"coverPhoto"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if not m:
            m = re.search(r'"cover_photo"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if m:
            profile.cover_photo_url = m.group(1).replace('\\/', '/')

        # --- Gender ---
        m = re.search(r'"gender"\s*:\s*"(MALE|FEMALE|CUSTOM)"', html, re.IGNORECASE)
        if m:
            profile.gender = m.group(1).capitalize()

        # --- Verified ---
        if '"is_verified":true' in html or '"isVerified":true' in html:
            profile.verified = True

        # --- Friends count ---
        m = re.search(r'"friend_count"\s*:\s*(\d+)', html)
        if m:
            profile.friends_count = int(m.group(1))

        # --- Followers count ---
        m = re.search(r'"text"\s*:\s*"([\d,.KMB]+)\s+followers?"', html, re.IGNORECASE)
        if m:
            profile.followers_count = _parse_count(m.group(1))

        # --- Following count ---
        m = re.search(r'"text"\s*:\s*"([\d,.KMB]+)\s+following"', html, re.IGNORECASE)
        if m:
            profile.following_count = _parse_count(m.group(1))

    # =========================================================================
    # Profile classification
    # =========================================================================
    def _classify_profile(self, html: str, profile: ProfileData):
        snippet = html[:200000]
        s_lower = snippet.lower()

        deactivated_sigs = ["this content isn't available", "this page isn't available",
                            "this account has been deactivated", '"is_deactivated":true']
        for sig in deactivated_sigs:
            if sig.lower() in s_lower:
                profile.profile_type = 'Deactivated Profile'
                return

        locked_sigs = ['"is_profile_locked":true', '"profile_locked":true',
                       'ProfileLockSection', 'This profile is locked']
        if any(sig in snippet for sig in locked_sigs):
            profile.profile_type = 'Locked Profile'
            return

        if '"is_private":true' in snippet:
            profile.profile_type = 'Private Profile'
            return

        profile.profile_type = 'Public Profile'

    def _reclassify(self, profile: ProfileData):
        """Downgrade to 'Limited Profile' if almost no data was found."""
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

    # =========================================================================
    # About section scraping
    # =========================================================================
    async def _scrape_all_about_sections(self, profile: ProfileData):
        """Navigate to each directory_* URL and extract profile_fields data."""
        for section_key in ABOUT_SECTIONS:
            if profile.user_id and profile.user_id.isdigit():
                url = f'https://www.facebook.com/profile.php?id={profile.user_id}&sk={section_key}'
            else:
                url = f'https://www.facebook.com/{profile.username}/{section_key}'

            try:
                html = await self.engine.fetch_page_html(url)
                if html:
                    self._extract_profile_fields(html, profile, section_key)
                    await self.rate_limiter.on_request_complete()
                await self.rate_limiter.section_delay()
            except Exception as e:
                log.warning(f'  \u26a0\ufe0f Failed {section_key}: {e}')

    # =========================================================================
    # Core JSON extraction: profile_fields
    # =========================================================================
    def _extract_profile_fields(self, html: str, profile: ProfileData, section_key: str):
        """Parse all profile_fields nodes from embedded <script> JSON."""
        all_fields = self._parse_profile_fields_json(html)

        for field in all_fields:
            if not isinstance(field, dict):
                continue

            ft = field.get('field_type', '')
            title_text, content_text = _get_field_texts(field)
            value = _unescape(title_text or content_text)
            if not value:
                continue

            # ── Map field_type to ProfileData attributes ──
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
                        profile.relationship_status += f' \u2014 {_unescape(content_text)}'
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

    # =========================================================================
    # JSON array parser
    # =========================================================================
    def _parse_profile_fields_json(self, html: str) -> list[dict]:
        """Find and parse all profile_fields.nodes arrays from the page HTML."""
        all_fields = []

        # Strategy 1: Structured JSON extraction via brace-counting
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


# =============================================================================
# Helper functions
# =============================================================================

def _get_field_texts(field: dict) -> tuple[str, str]:
    """Extract title text and content text from a profile_fields node."""
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
    """Extract relationship type from family_member field."""
    relationship = ''

    # Check list_item_groups first
    if isinstance(field.get('list_item_groups'), list):
        for group in field['list_item_groups']:
            if isinstance(group, dict):
                for item in group.get('list_items', []):
                    if isinstance(item, dict):
                        tc = item.get('text_content')
                        if isinstance(tc, dict):
                            relationship = tc.get('text', '')

    # Fallback: text_content different from name
    if not relationship and content_text and content_text != name:
        relationship = content_text

    return relationship


def _unescape(text: str) -> str:
    """Decode unicode escape sequences in text from Facebook JSON."""
    if not text:
        return ''
    try:
        return text.encode().decode('unicode_escape', errors='ignore')
    except Exception:
        return text


def _parse_count(text: str) -> int:
    """Parse human-readable counts like '1.5K', '2M', etc."""
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
