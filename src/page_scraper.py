"""Facebook page details scraper.

Uses directory_* about section URLs (same as profiles) to extract page metadata.
"""

from __future__ import annotations
import logging
import re
from .graphql_engine import GraphQLEngine
from .rate_limiter import RateLimiter
from .models import PageData, BusinessHours

log = logging.getLogger(__name__)

INVALID_NAMES = {
    "WAWebOpusRecorderWorkerBundle", "WebWizRecorderWorkerBundle",
    "WAWebWorkerBundle", "CometMediaViewerPhoto", "CometFeed",
    "RelayModern", "CometSinglePostRoute", "ProfileCometTimelineRoute",
    "", "Facebook", "undefined", "null",
}


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
            await self._scrape_about_sections(page)

        log.info(f"✅ Page scraped: {page.name} [{page.page_type}] (likes: {page.likes_count}, followers: {page.followers_count})")
        return page

    def _extract_from_html(self, html: str, page: PageData):
        """Extract page data from the main page HTML."""
        # ── Name ──
        self._extract_name(html, page)

        # ── Page ID ──
        if not page.page_id:
            for p in [r'"pageID"\s*:\s*"(\d+)"', r'"page_id"\s*:\s*"(\d+)"',
                      r'"ownerID"\s*:\s*"(\d+)"', r'"entity_id"\s*:\s*"(\d+)"']:
                m = re.search(p, html)
                if m:
                    page.page_id = m.group(1)
                    break

        # ── Category ──
        for p in [r'"category_name"\s*:\s*"([^"]+)"', r'"category"\s*:\s*"([^"]+)"',
                  r'"category_type"\s*:\s*"([^"]+)"']:
            m = re.search(p, html)
            if m:
                cat = _unescape(m.group(1))
                if cat not in INVALID_NAMES:
                    page.category = cat
                    break

        # ── Sub-categories ──
        for m in re.finditer(r'"sub_category"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html):
            page.sub_categories.append(_unescape(m.group(1)))

        # ── Verified ──
        if '"is_verified":true' in html or '"isVerified":true' in html:
            page.verified = True

        # ── Profile picture ──
        for p in [r'"profilePicLarge"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"',
                  r'meta\s+property="og:image"\s+content="([^"]+)"']:
            m = re.search(p, html)
            if m:
                page.profile_picture_url = m.group(1).replace("\\/", "/")
                break

        # ── Cover photo ──
        m = re.search(r'"coverPhoto"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if m:
            page.cover_photo_url = m.group(1).replace("\\/", "/")

        # ── Likes ──
        for p in [r'"page_likers"\s*:\s*\{[^}]*"count"\s*:\s*(\d+)',
                  r'"follower_count"\s*:\s*(\d+).*?"page_likers"',
                  r'"text"\s*:\s*"([\d,.KMB]+)\s+(?:people like|likes?)"']:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                page.likes_count = _parse_count(m.group(1))
                break

        # ── Followers ──
        for p in [r'"follower_count"\s*:\s*(\d+)',
                  r'"text"\s*:\s*"([\d,.KMB]+)\s+(?:people follow|followers?)"']:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                page.followers_count = _parse_count(m.group(1))
                break

        # ── Check-ins ──
        for p in [r'"checkins"\s*:\s*\{[^}]*"count"\s*:\s*(\d+)',
                  r'"text"\s*:\s*"([\d,.]+)\s+(?:were here|check-?ins?)"']:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                page.checkins_count = _parse_count(m.group(1))
                break

        # ── Rating ──
        m = re.search(r'"overall_star_rating"\s*:\s*([\d.]+)', html)
        if m:
            page.rating = float(m.group(1))
        m = re.search(r'"rating_count"\s*:\s*(\d+)', html)
        if m:
            page.review_count = int(m.group(1))

        # ── Talking about ──
        m = re.search(r'"talking_about_count"\s*:\s*(\d+)', html)
        if m:
            page.talking_about_count = int(m.group(1))

        # ── Description / bio ──
        m = re.search(r'"page_about_fields"\s*:\s*\{[^}]*"blurb"\s*:\s*"([^"]+)"', html)
        if m:
            page.short_description = _unescape(m.group(1))
        m = re.search(r'"description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]{10,})"', html)
        if m:
            page.description = _unescape(m.group(1))

        # ── Username ──
        if not page.username:
            m = re.search(r'"vanity"\s*:\s*"([^"]+)"', html)
            if m:
                page.username = m.group(1)

    def _extract_name(self, html: str, page: PageData):
        """Extract real page name from <title>, og:title, and typed objects."""
        candidates = []

        m = re.search(r'<title[^>]*>([^<]+)</title>', html)
        if m:
            name = m.group(1).strip()
            for suffix in [" | Facebook", " - Facebook", " — Facebook", " · Facebook"]:
                name = name.replace(suffix, "")
            if name:
                candidates.append(name.strip())

        m = re.search(r'<meta\s+(?:property|name)="og:title"\s+content="([^"]+)"', html)
        if not m:
            m = re.search(r'content="([^"]+)"\s+(?:property|name)="og:title"', html)
        if m:
            name = m.group(1).replace(" | Facebook", "").replace(" - Facebook", "").strip()
            if name:
                candidates.append(name)

        m = re.search(r'"__typename"\s*:\s*"Page"[^}]*"name"\s*:\s*"([^"]{2,100})"', html)
        if m:
            candidates.append(m.group(1))

        for name in candidates:
            name = _unescape(name)
            if name and name not in INVALID_NAMES and not name.startswith("WAWeb"):
                page.name = name
                return

    def _classify_page(self, html: str, page: PageData):
        """Classify page type."""
        snippet = html[:200000]

        unavail = ["This content isn't available", "this page isn't available",
                   "The link you followed may be broken", "this page has been removed"]
        for sig in unavail:
            if sig.lower() in snippet.lower():
                page.page_type = "Unavailable Page"
                return

        if '"is_published":false' in snippet or '"isPublished":false' in snippet:
            page.page_type = "Unpublished Page"
            return

        is_verified = page.verified
        business_count = sum(1 for s in ['"hours"', '"price_range"', '"restaurant_specialties"',
                                         '"business"', '"LocalBusiness"'] if s in snippet)
        community_count = sum(1 for s in ['"COMMUNITY"', '"community_page"', 'Community Organization'] if s in snippet)
        official_count = sum(1 for s in ['Government', 'Political Organization', 'Public Figure'] if s in snippet)

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
        else:
            page.page_type = "Public Page"

    # =========================================================================
    # About Section Scraping — uses directory_* URLs (same as profiles)
    # =========================================================================
    async def _scrape_about_sections(self, page: PageData):
        """Scrape about details using directory_* URLs."""
        identifier = page.username or page.page_id
        if not identifier:
            return

        sections = [
            ("directory_intro", self._parse_intro),
            ("directory_contact_info", self._parse_contact),
            ("directory_basic_info", self._parse_basic_info),
            ("directory_links", self._parse_links),
            ("directory_category", self._parse_category),
            ("directory_personal_details", self._parse_details),
        ]

        for section_key, parser in sections:
            if page.page_id and page.page_id.isdigit():
                section_url = f"https://www.facebook.com/profile.php?id={page.page_id}&sk={section_key}"
            else:
                section_url = f"https://www.facebook.com/{identifier}/{section_key}"

            try:
                html = await self.engine.fetch_page_html(section_url)
                if html:
                    parser(html, page)
                    await self.rate_limiter.on_request_complete()
                await self.rate_limiter.section_delay()
            except Exception as e:
                log.warning(f"  ⚠️ Failed {section_key}: {e}")

        # Also try the classic /about page
        about_url = f"https://www.facebook.com/{identifier}/about"
        try:
            html = await self.engine.fetch_page_html(about_url)
            if html:
                self._parse_about_page(html, page)
                await self.rate_limiter.on_request_complete()
        except Exception as e:
            log.warning(f"  ⚠️ Failed about page: {e}")

    def _parse_intro(self, html: str, page: PageData):
        """Parse intro section for description/mission."""
        texts = _extract_all_text_values(html)
        for t in texts:
            if len(t) > 20:
                if not page.description:
                    page.description = t
                elif t != page.description and not page.mission and len(t) > 30:
                    page.mission = t

    def _parse_contact(self, html: str, page: PageData):
        """Parse contact info."""
        # Phone
        if not page.phone:
            m = re.search(r'"text"\s*:\s*"(\+?[\d\s\-\(\)]{7,20})"', html)
            if m:
                page.phone = m.group(1).strip()
            else:
                m = re.search(r'"phone"\s*:\s*"([^"]+)"', html)
                if m:
                    page.phone = m.group(1)

        # Email
        if not page.email:
            m = re.search(r'"text"\s*:\s*"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})"', html)
            if m:
                page.email = m.group(1)

        # Website
        if not page.website:
            m = re.search(r'"website"\s*:\s*"([^"]+)"', html)
            if m:
                page.website = m.group(1).replace("\\/", "/")

        # Address
        addr_parts = []
        for key in ["street", "city", "state", "zip", "country"]:
            m = re.search(rf'"(?:address_)?{key}"\s*:\s*"([^"]+)"', html)
            if m:
                val = _unescape(m.group(1))
                setattr(page, f"address_{key}" if key != "country" else "address_country", val)
                addr_parts.append(val)
        if addr_parts and not page.full_address:
            page.full_address = ", ".join(addr_parts)

        # Coordinates
        m = re.search(r'"latitude"\s*:\s*([\d.\-]+)', html)
        if m:
            page.latitude = float(m.group(1))
        m = re.search(r'"longitude"\s*:\s*([\d.\-]+)', html)
        if m:
            page.longitude = float(m.group(1))

    def _parse_basic_info(self, html: str, page: PageData):
        """Parse basic info (hours, price range, founded, etc)."""
        # Hours
        day_map = {"mon": "monday", "tue": "tuesday", "wed": "wednesday",
                   "thu": "thursday", "fri": "friday", "sat": "saturday", "sun": "sunday"}
        hours_found = {}
        for m in re.finditer(r'"(mon|tue|wed|thu|fri|sat|sun)\w*"\s*:\s*"([^"]+)"', html, re.IGNORECASE):
            day_key = m.group(1).lower()[:3]
            if day_key in day_map:
                hours_found[day_map[day_key]] = m.group(2)
        if hours_found:
            bh = BusinessHours(**hours_found)
            page.hours = bh

        for key, attr in [("price_range", "price_range"), ("founded", "founded"),
                          ("mission", "mission"), ("company_overview", "company_overview"),
                          ("products", "products"), ("impressum", "impressum")]:
            if not getattr(page, attr):
                m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', html)
                if m:
                    setattr(page, attr, _unescape(m.group(1)))

    def _parse_links(self, html: str, page: PageData):
        """Parse links section."""
        urls = re.findall(r'"url"\s*:\s*"(https?://[^"]+)"', html)
        for u in urls:
            u = u.replace("\\/", "/")
            if "facebook.com" in u or "fbcdn" in u:
                continue
            if "instagram.com" in u.lower() and not page.instagram_url:
                page.instagram_url = u
            elif "twitter.com" in u.lower() or "x.com" in u.lower():
                if not page.twitter_url:
                    page.twitter_url = u
            elif u not in page.additional_websites and u != page.website:
                page.additional_websites.append(u)

        # WhatsApp
        m = re.search(r'"whatsapp_number"\s*:\s*"([^"]+)"', html)
        if m:
            page.whatsapp_number = m.group(1)

    def _parse_category(self, html: str, page: PageData):
        """Parse category page for sub-categories."""
        cats = re.findall(r'"category_name"\s*:\s*"([^"]+)"', html)
        for c in cats:
            c = _unescape(c)
            if c and c not in INVALID_NAMES and c not in page.sub_categories and c != page.category:
                page.sub_categories.append(c)

    def _parse_details(self, html: str, page: PageData):
        """Parse personal details / additional info."""
        texts = _extract_all_text_values(html)
        for t in texts:
            if len(t) > 20 and not _is_boilerplate(t):
                if not page.description and len(t) > 50:
                    page.description = t

    def _parse_about_page(self, html: str, page: PageData):
        """Parse the classic /about page for any remaining fields."""
        # Fill in gaps from the classic about page
        if not page.description:
            m = re.search(r'"description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]{10,})"', html)
            if m:
                page.description = _unescape(m.group(1))

        if not page.phone:
            m = re.search(r'"phone"\s*:\s*"([^"]+)"', html)
            if m:
                page.phone = m.group(1)

        if not page.email:
            m = re.search(r'"email"\s*:\s*"([^"]+)"', html)
            if m:
                page.email = m.group(1)

        if not page.website:
            m = re.search(r'"website"\s*:\s*"([^"]+)"', html)
            if m:
                page.website = m.group(1).replace("\\/", "/")


# =============================================================================
# Utility functions
# =============================================================================

def _extract_all_text_values(html: str) -> list[str]:
    results = []
    for m in re.finditer(r'"text"\s*:\s*"([^"]{3,500})"', html):
        text = _unescape(m.group(1))
        if text and not _is_boilerplate(text):
            results.append(text)
    return results


def _is_boilerplate(text: str) -> bool:
    boilerplate = ["See more", "See less", "Like", "Comment", "Share",
                   "Log in", "Sign up", "Privacy Policy", "Terms of Service",
                   "WAWeb", "RelayModern", "CometFeed", "Anyone can see"]
    return any(bp.lower() == text.lower() or text.startswith(bp) for bp in boilerplate)


def _unescape(text: str) -> str:
    try:
        return text.encode().decode('unicode_escape', errors='ignore')
    except Exception:
        return text


def _parse_count(text: str) -> int:
    text = text.replace(",", "").strip()
    mult = 1
    if text.upper().endswith("K"): mult, text = 1000, text[:-1]
    elif text.upper().endswith("M"): mult, text = 1000000, text[:-1]
    elif text.upper().endswith("B"): mult, text = 1000000000, text[:-1]
    try: return int(float(text) * mult)
    except ValueError: return 0
