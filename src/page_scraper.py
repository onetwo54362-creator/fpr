"""Facebook page details scraper."""

from __future__ import annotations
import logging
import re
from typing import Optional
from .graphql_engine import GraphQLEngine
from .rate_limiter import RateLimiter
from .response_parser import find_nested_value, find_all_nested_values
from .models import PageData, BusinessHours

log = logging.getLogger(__name__)


class PageScraper:
    def __init__(self, engine: GraphQLEngine, rate_limiter: RateLimiter, scrape_about: bool = True):
        self.engine = engine
        self.rate_limiter = rate_limiter
        self.scrape_about = scrape_about

    async def scrape(self, url: str, page_id: str = "", username: str = "", initial_html: str = "") -> PageData:
        log.info(f"📄 Scraping page: {url}")
        page = PageData(page_id=page_id, page_url=url, username=username)

        html = initial_html or await self.engine.fetch_page_html(url)
        if html:
            self._extract_from_html(html, page)
            self._classify_page(html, page)
            await self.rate_limiter.on_request_complete()

        if self.scrape_about:
            about_url = f"https://www.facebook.com/{username or page_id}/about" if (username or page_id) else f"{url}/about"
            about_html = await self.engine.fetch_page_html(about_url)
            if about_html:
                self._extract_about(about_html, page)
                await self.rate_limiter.on_request_complete()

        log.info(f"✅ Page scraped: {page.name} [{page.page_type}] (likes: {page.likes_count}, followers: {page.followers_count})")
        return page

    def _extract_from_html(self, html: str, page: PageData):
        # Name
        for p in [r'"name"\s*:\s*"([^"]{2,100})".*?"__typename"\s*:\s*"Page"',
                  r'<title>([^<]+)</title>']:
            m = re.search(p, html, re.DOTALL)
            if m:
                name = m.group(1).replace(" | Facebook", "").replace(" - Facebook", "").strip()
                if name and name != "Facebook":
                    page.name = name
                    break

        if not page.name:
            m = re.search(r'"__typename"\s*:\s*"Page"[^}]*"name"\s*:\s*"([^"]+)"', html)
            if m:
                page.name = m.group(1)

        # Page ID
        if not page.page_id:
            m = re.search(r'"pageID"\s*:\s*"(\d+)"', html)
            if m:
                page.page_id = m.group(1)

        # Category
        m = re.search(r'"category_name"\s*:\s*"([^"]+)"', html)
        if m:
            page.category = m.group(1)
        else:
            m = re.search(r'"category"\s*:\s*"([^"]+)"', html)
            if m:
                page.category = m.group(1)

        # Sub-categories
        subcats = re.findall(r'"sub_category"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
        if subcats:
            page.sub_categories = subcats

        # Verified
        if '"is_verified":true' in html or '"isVerified":true' in html:
            page.verified = True

        # Profile picture
        m = re.search(r'"profilePicLarge"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if m:
            page.profile_picture_url = m.group(1).replace("\\/", "/")

        # Cover photo
        m = re.search(r'"coverPhoto"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if m:
            page.cover_photo_url = m.group(1).replace("\\/", "/")

        # Likes
        m = re.search(r'"page_likers"\s*:\s*\{[^}]*"count"\s*:\s*(\d+)', html)
        if m:
            page.likes_count = int(m.group(1))
        else:
            m = re.search(r'"text"\s*:\s*"([\d,.KMB]+)\s+(?:people like|likes?)"', html, re.IGNORECASE)
            if m:
                page.likes_count = self._parse_count(m.group(1))

        # Followers
        m = re.search(r'"follower_count"\s*:\s*(\d+)', html)
        if m:
            page.followers_count = int(m.group(1))
        else:
            m = re.search(r'"text"\s*:\s*"([\d,.KMB]+)\s+(?:people follow|followers?)"', html, re.IGNORECASE)
            if m:
                page.followers_count = self._parse_count(m.group(1))

        # Check-ins
        m = re.search(r'"checkins"\s*:\s*\{[^}]*"count"\s*:\s*(\d+)', html)
        if m:
            page.checkins_count = int(m.group(1))
        else:
            m = re.search(r'"text"\s*:\s*"([\d,.]+)\s+(?:were here|check-?ins?)"', html, re.IGNORECASE)
            if m:
                page.checkins_count = self._parse_count(m.group(1))

        # Rating
        m = re.search(r'"overall_star_rating"\s*:\s*([\d.]+)', html)
        if m:
            page.rating = float(m.group(1))
        m = re.search(r'"rating_count"\s*:\s*(\d+)', html)
        if m:
            page.review_count = int(m.group(1))

        # Talking about
        m = re.search(r'"talking_about_count"\s*:\s*(\d+)', html)
        if m:
            page.talking_about_count = int(m.group(1))

        # Description
        m = re.search(r'"page_about_fields"\s*:\s*\{[^}]*"blurb"\s*:\s*"([^"]+)"', html)
        if m:
            page.short_description = m.group(1).encode().decode('unicode_escape', errors='ignore')
        m = re.search(r'"description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]{10,})"', html)
        if m:
            page.description = m.group(1).encode().decode('unicode_escape', errors='ignore')

        # Username
        if not page.username:
            m = re.search(r'"vanity"\s*:\s*"([^"]+)"', html)
            if m:
                page.username = m.group(1)

    def _classify_page(self, html: str, page: PageData):
        """Classify page type based on HTML signals."""
        snippet = html[:200000]

        # Unavailable
        unavailable_signals = [
            "This content isn't available",
            "this page isn't available",
            "The link you followed may be broken",
            "this page has been removed",
        ]
        for sig in unavailable_signals:
            if sig.lower() in snippet.lower():
                page.page_type = "Unavailable Page"
                return

        # Unpublished
        if '"is_published":false' in snippet or '"isPublished":false' in snippet or 'Unpublished' in snippet:
            page.page_type = "Unpublished Page"
            return

        # Verified
        is_verified = page.verified or '"is_verified":true' in snippet or '"isVerified":true' in snippet

        # Business page detection
        business_signals = [
            '"hours"', '"price_range"', '"restaurant_specialties"',
            '"restaurant_services"', '"parking"', '"payment_options"',
            '"business"', '"LocalBusiness"',
        ]
        business_count = sum(1 for sig in business_signals if sig in snippet)

        # Community page
        community_signals = [
            '"COMMUNITY"', '"community_page"', 'Community Organization',
            '"Interest"', '"Cause"',
        ]
        community_count = sum(1 for sig in community_signals if sig in snippet)

        # Government / Official
        official_signals = [
            'Government', '"GOVERNMENT"', 'Political Organization',
            'Political Party', 'Public Figure',
        ]
        official_count = sum(1 for sig in official_signals if sig in snippet)

        # Classify with priority
        if official_count >= 1 and is_verified:
            page.page_type = "Verified Official Page"
        elif is_verified and business_count >= 2:
            page.page_type = "Verified Business Page"
        elif is_verified:
            page.page_type = "Verified Page"
        elif business_count >= 2:
            page.page_type = "Business Page"
        elif community_count >= 1:
            page.page_type = "Community Page"
        elif official_count >= 1:
            page.page_type = "Official Page"
        else:
            page.page_type = "Public Page"

    def _extract_about(self, html: str, page: PageData):
        # Phone
        m = re.search(r'"text"\s*:\s*"([\+\d\s\-\(\)]{7,20})"[^}]*"(?:phone|Phone|mobile|Mobile)"', html)
        if m:
            page.phone = m.group(1)
        elif not page.phone:
            m = re.search(r'"phone"\s*:\s*"([^"]+)"', html)
            if m:
                page.phone = m.group(1)

        # Email
        m = re.search(r'"text"\s*:\s*"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})"', html)
        if m:
            page.email = m.group(1)
        elif not page.email:
            m = re.search(r'"email"\s*:\s*"([^"]+)"', html)
            if m:
                page.email = m.group(1)

        # Website
        m = re.search(r'"website"\s*:\s*"([^"]+)"', html)
        if m:
            page.website = m.group(1).replace("\\/", "/")

        # Social links
        for platform, attr in [("instagram", "instagram_url"), ("twitter", "twitter_url")]:
            m = re.search(rf'"(?:{platform})\w*"\s*:\s*"([^"]+)"', html, re.IGNORECASE)
            if m:
                setattr(page, attr, m.group(1).replace("\\/", "/"))

        # Address
        addr_parts = []
        for key in ["street", "city", "state", "zip", "country"]:
            m = re.search(rf'"(?:address_)?{key}"\s*:\s*"([^"]+)"', html)
            if m:
                val = m.group(1)
                setattr(page, f"address_{key}" if key != "country" else "address_country", val)
                addr_parts.append(val)
        if addr_parts:
            page.full_address = ", ".join(addr_parts)

        # Coordinates
        m = re.search(r'"latitude"\s*:\s*([\d.\-]+)', html)
        if m:
            page.latitude = float(m.group(1))
        m = re.search(r'"longitude"\s*:\s*([\d.\-]+)', html)
        if m:
            page.longitude = float(m.group(1))

        # Hours
        hours_match = re.findall(r'"(?:mon|tue|wed|thu|fri|sat|sun)\w*"\s*:\s*"([^"]+)"', html, re.IGNORECASE)
        if len(hours_match) >= 2:
            bh = BusinessHours()
            day_map = {"mon": "monday", "tue": "tuesday", "wed": "wednesday",
                       "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday"}
            for m2 in re.finditer(r'"(mon|tue|wed|thu|fri|sat|sun)\w*"\s*:\s*"([^"]+)"', html, re.IGNORECASE):
                day_key = m2.group(1).lower()[:3]
                if day_key in day_map:
                    setattr(bh, day_map[day_key], m2.group(2))
            page.hours = bh

        # Price range
        m = re.search(r'"price_range"\s*:\s*"([^"]+)"', html)
        if m:
            page.price_range = m.group(1)

        # Mission
        m = re.search(r'"mission"\s*:\s*"([^"]+)"', html)
        if m:
            page.mission = m.group(1).encode().decode('unicode_escape', errors='ignore')

        # Company overview
        m = re.search(r'"company_overview"\s*:\s*"([^"]+)"', html)
        if m:
            page.company_overview = m.group(1).encode().decode('unicode_escape', errors='ignore')

        # Founded
        m = re.search(r'"founded"\s*:\s*"([^"]+)"', html)
        if m:
            page.founded = m.group(1)

        # Products
        m = re.search(r'"products"\s*:\s*"([^"]+)"', html)
        if m:
            page.products = m.group(1)

        # Impressum
        m = re.search(r'"impressum"\s*:\s*"([^"]+)"', html)
        if m:
            page.impressum = m.group(1).encode().decode('unicode_escape', errors='ignore')

        # Better description from about page
        m = re.search(r'"description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]{10,})"', html)
        if m:
            desc = m.group(1).encode().decode('unicode_escape', errors='ignore')
            if len(desc) > len(page.description):
                page.description = desc

        # WhatsApp
        m = re.search(r'"whatsapp_number"\s*:\s*"([^"]+)"', html)
        if m:
            page.whatsapp_number = m.group(1)

    @staticmethod
    def _parse_count(text: str) -> int:
        text = text.replace(",", "").strip()
        mult = 1
        if text.upper().endswith("K"): mult, text = 1000, text[:-1]
        elif text.upper().endswith("M"): mult, text = 1000000, text[:-1]
        elif text.upper().endswith("B"): mult, text = 1000000000, text[:-1]
        try: return int(float(text) * mult)
        except ValueError: return 0
