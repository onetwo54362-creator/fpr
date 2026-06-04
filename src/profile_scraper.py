"""Facebook personal profile details scraper.

Uses directory_* about section URLs to extract comprehensive profile data.
"""

from __future__ import annotations
import logging
import re
from .graphql_engine import GraphQLEngine
from .rate_limiter import RateLimiter
from .models import ProfileData, WorkExperience, Education, FamilyMember

log = logging.getLogger(__name__)

# Facebook internal module names that incorrectly match "name" regex
INVALID_NAMES = {
    "WAWebOpusRecorderWorkerBundle", "WebWizRecorderWorkerBundle",
    "WAWebWorkerBundle", "CometMediaViewerPhoto", "CometFeed",
    "RelayModern", "CometSinglePostRoute", "ProfileCometTimelineRoute",
    "", "Facebook", "undefined", "null",
}


class ProfileScraper:
    def __init__(self, engine: GraphQLEngine, rate_limiter: RateLimiter, scrape_about: bool = True):
        self.engine = engine
        self.rate_limiter = rate_limiter
        self.scrape_about = scrape_about

    async def scrape(self, url: str, username: str, user_id: str = "", initial_html: str = "") -> ProfileData:
        log.info(f"👤 Scraping profile: {url}")
        profile = ProfileData(username=username, user_id=user_id, profile_url=url)

        # Extract from initial HTML
        html = initial_html or await self.engine.fetch_page_html(url)
        if html:
            self._extract_from_html(html, profile)
            self._classify_profile(html, profile)
            await self.rate_limiter.on_request_complete()

        # Scrape about sections (skip for locked/deactivated)
        if self.scrape_about and profile.profile_type not in ("Locked Profile", "Deactivated Profile", "Unavailable Profile"):
            await self._scrape_about_sections(profile)
            self._reclassify_after_about(profile)

        log.info(f"✅ Profile scraped: {profile.name} [{profile.profile_type}] (ID: {profile.user_id})")
        return profile

    def _extract_from_html(self, html: str, profile: ProfileData):
        """Extract profile data from the main profile page HTML."""
        # ── Name: use <title> and og:title (most reliable) ──
        self._extract_name(html, profile)

        # ── User ID ──
        if not profile.user_id:
            for p in [r'"userID"\s*:\s*"(\d+)"', r'"user_id"\s*:\s*"(\d+)"',
                      r'"profile_owner"\s*:\s*\{[^}]*"id"\s*:\s*"(\d+)"',
                      r'"ownerID"\s*:\s*"(\d+)"', r'"actorID"\s*:\s*"(\d+)"',
                      r'"entity_id"\s*:\s*"(\d+)"']:
                m = re.search(p, html)
                if m and m.group(1) != self.engine.cookies.get("c_user", ""):
                    profile.user_id = m.group(1)
                    break

        # ── Profile picture ──
        for p in [r'"profilePicLarge"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"',
                  r'"profilePhoto"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"',
                  r'"profile_picture"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"',
                  r'meta\s+property="og:image"\s+content="([^"]+)"']:
            m = re.search(p, html)
            if m:
                profile.profile_picture_url = m.group(1).replace("\\/", "/")
                break

        # ── Cover photo ──
        m = re.search(r'"coverPhoto"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if m:
            profile.cover_photo_url = m.group(1).replace("\\/", "/")

        # ── Verified ──
        if '"is_verified":true' in html or '"isVerified":true' in html:
            profile.verified = True

        # ── Friends count ──
        for p in [r'"friends_count"\s*:\s*(\d+)',
                  r'"text"\s*:\s*"([\d,]+)\s+friends?"']:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                profile.friends_count = int(m.group(1).replace(",", ""))
                break

        # ── Followers count ──
        for p in [r'"follower_count"\s*:\s*(\d+)',
                  r'"text"\s*:\s*"([\d,.KMB]+)\s+follower']:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                profile.followers_count = _parse_count(m.group(1))
                break

        # ── Bio ──
        for p in [r'"bio_text"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"',
                  r'"profile_intro_card"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"']:
            m = re.search(p, html)
            if m:
                profile.bio = _unescape(m.group(1))
                break

        # ── Gender ──
        m = re.search(r'"gender"\s*:\s*"(MALE|FEMALE|CUSTOM)"', html, re.IGNORECASE)
        if m:
            profile.gender = m.group(1).capitalize()

    def _extract_name(self, html: str, profile: ProfileData):
        """Extract real name using title tag and og:title — filters out FB internal module names."""
        candidates = []

        # 1. <title> tag — most reliable
        m = re.search(r'<title[^>]*>([^<]+)</title>', html)
        if m:
            name = m.group(1).strip()
            for suffix in [" | Facebook", " - Facebook", " — Facebook", " · Facebook"]:
                name = name.replace(suffix, "")
            name = name.strip()
            if name:
                candidates.append(name)

        # 2. og:title meta tag
        m = re.search(r'<meta\s+(?:property|name)="og:title"\s+content="([^"]+)"', html)
        if not m:
            m = re.search(r'content="([^"]+)"\s+(?:property|name)="og:title"', html)
        if m:
            name = m.group(1).strip()
            for suffix in [" | Facebook", " - Facebook"]:
                name = name.replace(suffix, "")
            if name:
                candidates.append(name)

        # 3. Structured data name with __typename User context
        m = re.search(r'"__typename"\s*:\s*"User"[^}]*"name"\s*:\s*"([^"]{2,80})"', html)
        if m:
            candidates.append(m.group(1))

        # 4. Profile header name pattern
        m = re.search(r'"profile_header_renderer"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
        if m:
            candidates.append(m.group(1))

        # Pick the first valid candidate
        for name in candidates:
            name = _unescape(name)
            if name and name not in INVALID_NAMES and len(name) < 80 and not name.startswith("WAWeb"):
                profile.name = name
                return

    def _classify_profile(self, html: str, profile: ProfileData):
        """Classify profile access level."""
        snippet = html[:200000]

        deactivated = ["This content isn't available", "this page isn't available",
                       "The link you followed may be broken", "this account has been deactivated",
                       '"is_deactivated":true']
        for sig in deactivated:
            if sig.lower() in snippet.lower():
                profile.profile_type = "Deactivated Profile"
                return

        if "Sorry, this content isn" in snippet or "content isn\u2019t available" in snippet:
            profile.profile_type = "Unavailable Profile"
            return

        locked = ['"is_profile_locked":true', '"profile_locked":true', '"is_locked":true',
                  'ProfileLockSection', 'ProfileLockedContent', 'This profile is locked']
        if any(sig in snippet for sig in locked):
            profile.profile_type = "Locked Profile"
            return

        private = ['"is_private":true', '"timeline_visibility":"SELF"']
        if any(sig in snippet for sig in private):
            profile.profile_type = "Private Profile"
            return

        profile.profile_type = "Public Profile"

    def _reclassify_after_about(self, profile: ProfileData):
        if profile.profile_type == "Public Profile":
            data_points = sum([len(profile.work) > 0, len(profile.education) > 0,
                               bool(profile.current_city), bool(profile.hometown),
                               len(profile.phone_numbers) > 0 or len(profile.emails) > 0,
                               bool(profile.relationship_status), bool(profile.bio)])
            if data_points == 0 and profile.name:
                profile.profile_type = "Limited Profile"

    # =========================================================================
    # About Section Scraping — uses directory_* URLs
    # =========================================================================
    async def _scrape_about_sections(self, profile: ProfileData):
        """Scrape all about section pages using the directory_* URL pattern."""
        base_id = profile.user_id or profile.username

        sections = [
            ("directory_intro", self._parse_intro),
            ("directory_work", self._parse_work),
            ("directory_education", self._parse_education),
            ("directory_contact_info", self._parse_contact),
            ("directory_basic_info", self._parse_basic_info),
            ("directory_personal_details", self._parse_personal_details),
            ("directory_links", self._parse_links),
            ("about_places", self._parse_places),
            ("about_family_and_relationships", self._parse_relationships),
            ("about_details", self._parse_details),
        ]

        for section_key, parser in sections:
            # Build URL based on whether we have numeric ID or username
            if profile.user_id and profile.user_id.isdigit():
                section_url = f"https://www.facebook.com/profile.php?id={profile.user_id}&sk={section_key}"
            else:
                section_url = f"https://www.facebook.com/{profile.username}/{section_key}"

            try:
                html = await self.engine.fetch_page_html(section_url)
                if html:
                    parser(html, profile)
                    await self.rate_limiter.on_request_complete()
                await self.rate_limiter.section_delay()
            except Exception as e:
                log.warning(f"  ⚠️ Failed {section_key}: {e}")

    def _parse_intro(self, html: str, profile: ProfileData):
        """Parse intro / bio section."""
        texts = _extract_all_text_values(html)
        for t in texts:
            if len(t) > 10 and not _is_boilerplate(t):
                if not profile.bio:
                    profile.bio = t
                elif not profile.intro and t != profile.bio:
                    profile.intro = t

    def _parse_work(self, html: str, profile: ProfileData):
        """Parse work/employment section."""
        # Structured employer+position
        for m in re.finditer(r'"employer"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html):
            company = _unescape(m.group(1))
            pos_m = re.search(r'"position"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html[m.end():m.end()+500])
            position = _unescape(pos_m.group(1)) if pos_m else ""
            if company not in INVALID_NAMES:
                profile.work.append(WorkExperience(company=company, position=position))

        # "Works at X" / "Worked at X" patterns
        for m in re.finditer(r'"text"\s*:\s*"((?:Works?|Worked)\s+at\s+[^"]+)"', html):
            text = _unescape(m.group(1))
            company = re.sub(r'^(?:Works?|Worked)\s+at\s+', '', text).strip()
            if company and not any(w.company == company for w in profile.work):
                profile.work.append(WorkExperience(company=company))

        # Self-employed / freelancer patterns
        for m in re.finditer(r'"text"\s*:\s*"(Self-[Ee]mployed|Freelanc(?:e|er|ing)[^"]*)"', html):
            text = _unescape(m.group(1))
            if not any(w.company == text for w in profile.work):
                profile.work.append(WorkExperience(company=text))

    def _parse_education(self, html: str, profile: ProfileData):
        """Parse education section."""
        # Structured school
        for m in re.finditer(r'"school"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html):
            school = _unescape(m.group(1))
            if school not in INVALID_NAMES:
                conc_m = re.search(r'"concentration"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html[m.end():m.end()+500])
                profile.education.append(Education(
                    school=school,
                    concentration=_unescape(conc_m.group(1)) if conc_m else "",
                ))

        # "Studied at X" / "Went to X" / "Goes to X"
        for m in re.finditer(r'"text"\s*:\s*"((?:Studied|Studies|Went to|Goes to)\s+(?:at\s+)?[^"]+)"', html):
            text = _unescape(m.group(1))
            school = re.sub(r'^(?:Studied|Studies|Went to|Goes to)\s+(?:at\s+)?', '', text).strip()
            if school and not any(e.school == school for e in profile.education):
                profile.education.append(Education(school=school))

    def _parse_contact(self, html: str, profile: ProfileData):
        """Parse contact info section."""
        # Phone numbers
        phones = re.findall(r'"text"\s*:\s*"(\+?[\d\s\-\(\)]{7,20})"', html)
        for p in phones:
            p = p.strip()
            if p and p not in profile.phone_numbers and len(p) >= 7:
                profile.phone_numbers.append(p)

        # Emails
        emails = re.findall(r'"text"\s*:\s*"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})"', html)
        for e in emails:
            if e not in profile.emails:
                profile.emails.append(e)

        # Birthday
        for p in [r'"text"\s*:\s*"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,\s*\d{4})?)"',
                  r'"birthday"\s*:\s*"([^"]+)"']:
            m = re.search(p, html)
            if m and not profile.birthday:
                profile.birthday = m.group(1).strip()

    def _parse_basic_info(self, html: str, profile: ProfileData):
        """Parse basic info section (gender, languages, etc)."""
        # Gender
        if not profile.gender:
            m = re.search(r'"gender"\s*:\s*"(MALE|FEMALE|CUSTOM)"', html, re.IGNORECASE)
            if m:
                profile.gender = m.group(1).capitalize()
            else:
                for g in ["Male", "Female"]:
                    if f'"text":"{g}"' in html:
                        profile.gender = g
                        break

        # Languages
        langs = re.findall(r'"text"\s*:\s*"((?:Speaks|Knows)\s+[^"]+)"', html)
        for l in langs:
            lang_text = re.sub(r'^(?:Speaks|Knows)\s+', '', l)
            for lang in re.split(r'\s*(?:,|and)\s*', lang_text):
                lang = lang.strip()
                if lang and lang not in profile.languages:
                    profile.languages.append(lang)

    def _parse_personal_details(self, html: str, profile: ProfileData):
        """Parse personal details (interested in, religion, politics)."""
        texts = _extract_all_text_values(html)
        for t in texts:
            t_lower = t.lower()
            if "interested in" in t_lower and not profile.interested_in:
                profile.interested_in = re.sub(r'^interested in\s*', '', t, flags=re.IGNORECASE).strip()
            elif any(r in t_lower for r in ["religious", "religion", "christian", "muslim", "catholic", "buddhist", "hindu", "jewish"]):
                if not profile.religious_views:
                    profile.religious_views = t
            elif any(p in t_lower for p in ["political", "liberal", "conservative", "moderate"]):
                if not profile.political_views:
                    profile.political_views = t

    def _parse_links(self, html: str, profile: ProfileData):
        """Parse links / websites / social links section."""
        # Websites
        urls = re.findall(r'"url"\s*:\s*"(https?://[^"]+)"', html)
        for u in urls:
            u = u.replace("\\/", "/")
            if "facebook.com" not in u and "fbcdn" not in u and u not in profile.websites:
                if any(s in u.lower() for s in ["instagram", "twitter", "tiktok", "youtube", "linkedin"]):
                    if u not in profile.social_links:
                        profile.social_links.append(u)
                else:
                    profile.websites.append(u)

        # Text-based links
        links = re.findall(r'"text"\s*:\s*"((?:https?://|www\.)[^"]+)"', html)
        for link in links:
            if link not in profile.websites and "facebook.com" not in link:
                profile.websites.append(link)

    def _parse_places(self, html: str, profile: ProfileData):
        """Parse places lived section."""
        m = re.search(r'"text"\s*:\s*"(?:Lives in|Current [Cc]ity)\s+([^"]+)"', html)
        if m and not profile.current_city:
            profile.current_city = _unescape(m.group(1))
        m = re.search(r'"text"\s*:\s*"(?:From|Hometown)\s+([^"]+)"', html)
        if m and not profile.hometown:
            profile.hometown = _unescape(m.group(1))
        for m in re.finditer(r'"text"\s*:\s*"(?:Moved to|Lived in)\s+([^"]+)"', html):
            place = _unescape(m.group(1))
            if place not in profile.places_lived:
                profile.places_lived.append(place)

    def _parse_relationships(self, html: str, profile: ProfileData):
        """Parse relationships and family section."""
        statuses = ["Single", "In a relationship", "Engaged", "Married", "In a civil union",
                    "In a domestic partnership", "In an open relationship", "It's complicated",
                    "Separated", "Divorced", "Widowed"]
        for s in statuses:
            if f'"{s}"' in html or f">{s}<" in html:
                profile.relationship_status = s
                break

        # Family members
        for m in re.finditer(r'"text"\s*:\s*"([^"]+)"[^}]*"(?:subtitle|secondary)"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"', html):
            name, rel = _unescape(m.group(1)), _unescape(m.group(2))
            family_words = ["sister", "brother", "mother", "father", "son", "daughter", "wife", "husband",
                           "aunt", "uncle", "cousin", "grandmother", "grandfather", "niece", "nephew"]
            if any(w in rel.lower() for w in family_words):
                if not any(f.name == name for f in profile.family_members):
                    profile.family_members.append(FamilyMember(name=name, relationship=rel))

    def _parse_details(self, html: str, profile: ProfileData):
        """Parse extra details section."""
        # Favorite quotes
        m = re.search(r'"favorite_quotes"\s*:\s*"([^"]+)"', html)
        if m:
            profile.favorite_quotes = _unescape(m.group(1))

        # Name pronunciation, nicknames, etc — capture any remaining text
        texts = _extract_all_text_values(html)
        for t in texts:
            if "quote" in t.lower() and not profile.favorite_quotes:
                profile.favorite_quotes = t


# =============================================================================
# Utility functions
# =============================================================================

def _extract_all_text_values(html: str) -> list[str]:
    """Extract all 'text' field values from Facebook HTML JSON."""
    results = []
    for m in re.finditer(r'"text"\s*:\s*"([^"]{3,500})"', html):
        text = _unescape(m.group(1))
        if text and not _is_boilerplate(text):
            results.append(text)
    return results


def _is_boilerplate(text: str) -> bool:
    """Filter out Facebook UI boilerplate text."""
    boilerplate = [
        "See more", "See less", "Like", "Comment", "Share", "Send",
        "Write a comment", "Log in", "Sign up", "Create new account",
        "Forgot password", "Privacy Policy", "Terms of Service",
        "Cookie Policy", "Accessibility", "Report", "Block",
        "Anyone can see", "Only members", "Visible", "Public",
        "WAWeb", "RelayModern", "CometFeed", "undefined",
    ]
    return any(bp.lower() == text.lower() or text.startswith(bp) for bp in boilerplate)


def _unescape(text: str) -> str:
    """Decode unicode escapes in Facebook JSON strings."""
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
