"""Facebook personal profile details scraper."""

from __future__ import annotations
import logging
import re
from typing import Optional
from .graphql_engine import GraphQLEngine
from .rate_limiter import RateLimiter
from .response_parser import find_nested_value, find_all_nested_values, find_typed_objects, extract_json_from_html
from .models import ProfileData, WorkExperience, Education, FamilyMember

log = logging.getLogger(__name__)


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

        # Scrape about sections (skip for locked/deactivated profiles)
        if self.scrape_about and profile.profile_type not in ("Locked Profile", "Deactivated Profile", "Unavailable Profile"):
            await self._scrape_about_sections(url, username, profile)
            # Re-classify after getting more data
            self._reclassify_after_about(profile)

        log.info(f"✅ Profile scraped: {profile.name} [{profile.profile_type}] (ID: {profile.user_id})")
        return profile

    def _extract_from_html(self, html: str, profile: ProfileData):
        """Extract profile data from the main profile page HTML."""
        # Name
        for pattern in [r'"name"\s*:\s*"([^"]{2,80})"', r'<title>([^<]+)</title>']:
            m = re.search(pattern, html)
            if m:
                name = m.group(1).replace("\\u0040", "@").replace(" | Facebook", "").replace(" - Facebook", "").strip()
                if name and name != "Facebook" and len(name) < 80:
                    profile.name = name
                    break

        # User ID
        if not profile.user_id:
            for p in [r'"userID"\s*:\s*"(\d+)"', r'"profile_id"\s*:\s*"(\d+)"', r'"entity_id"\s*:\s*"(\d+)"']:
                m = re.search(p, html)
                if m and m.group(1) != self.engine.cookies.get("c_user", ""):
                    profile.user_id = m.group(1)
                    break

        # Profile picture
        for p in [r'"profilePicLarge"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"',
                  r'"profilePhoto"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"',
                  r'"profile_picture"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"']:
            m = re.search(p, html)
            if m:
                profile.profile_picture_url = m.group(1).replace("\\/", "/")
                break

        # Cover photo
        m = re.search(r'"coverPhoto"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if m:
            profile.cover_photo_url = m.group(1).replace("\\/", "/")

        # Verified
        if '"is_verified":true' in html or '"isVerified":true' in html:
            profile.verified = True

        # Friends count
        m = re.search(r'"friends_count"\s*:\s*(\d+)', html)
        if m:
            profile.friends_count = int(m.group(1))
        else:
            m = re.search(r'"text"\s*:\s*"([\d,]+)\s+friends?"', html, re.IGNORECASE)
            if m:
                profile.friends_count = int(m.group(1).replace(",", ""))

        # Followers count
        m = re.search(r'"follower_count"\s*:\s*(\d+)', html)
        if m:
            profile.followers_count = int(m.group(1))
        else:
            m = re.search(r'"text"\s*:\s*"([\d,.KMB]+)\s+follower', html, re.IGNORECASE)
            if m:
                profile.followers_count = self._parse_count(m.group(1))

        # Bio / intro
        for p in [r'"bio_text"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"',
                  r'"profile_intro_card"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"']:
            m = re.search(p, html)
            if m:
                profile.bio = m.group(1).encode().decode('unicode_escape', errors='ignore')
                break

        # Gender
        m = re.search(r'"gender"\s*:\s*"(MALE|FEMALE|CUSTOM)"', html, re.IGNORECASE)
        if m:
            profile.gender = m.group(1).capitalize()

    def _classify_profile(self, html: str, profile: ProfileData):
        """Classify profile access level based on HTML signals."""
        snippet = html[:200000]

        # Deactivated / unavailable
        deactivated_signals = [
            "This content isn't available",
            "this page isn't available",
            "The link you followed may be broken",
            "this account has been deactivated",
            '"is_deactivated":true',
        ]
        for sig in deactivated_signals:
            if sig.lower() in snippet.lower():
                profile.profile_type = "Deactivated Profile"
                return

        # Content not available (deleted/banned)
        if "Sorry, this content isn" in snippet or "content isn\u2019t available" in snippet:
            profile.profile_type = "Unavailable Profile"
            return

        # Locked profile indicators
        locked_signals = [
            '"is_profile_locked":true',
            '"profile_locked":true',
            '"is_locked":true',
            'profile_lock_section',
            'ProfileLockSection',
            'ProfileLockedContent',
            '"lockProfileOverride"',
            'This profile is locked',
        ]
        locked_count = sum(1 for sig in locked_signals if sig in snippet)
        if locked_count >= 1:
            profile.profile_type = "Locked Profile"
            return

        # Private profile (limited visibility)
        private_signals = [
            '"is_private":true',
            '"timeline_visibility":"SELF"',
            '"privacy":"SELF"',
            '"visibility":"SELF"',
        ]
        private_count = sum(1 for sig in private_signals if sig in snippet)
        if private_count >= 1:
            profile.profile_type = "Private Profile"
            return

        # Limited profile (some sections hidden)
        limited_signals = [
            '"profileTabSections":[]',
            '"timeline_sections":[]',
            '"about_count":0',
        ]
        limited_count = sum(1 for sig in limited_signals if sig in snippet)
        if limited_count >= 1:
            profile.profile_type = "Limited Profile"
            return

        # Default: public
        profile.profile_type = "Public Profile"

    def _reclassify_after_about(self, profile: ProfileData):
        """Re-check classification after about sections are scraped."""
        if profile.profile_type == "Public Profile":
            # If we got almost no data from a supposedly public profile, it's likely limited
            has_work = len(profile.work) > 0
            has_education = len(profile.education) > 0
            has_city = bool(profile.current_city)
            has_hometown = bool(profile.hometown)
            has_contact = len(profile.phone_numbers) > 0 or len(profile.emails) > 0
            has_relationship = bool(profile.relationship_status)
            has_bio = bool(profile.bio)

            data_points = sum([has_work, has_education, has_city, has_hometown,
                               has_contact, has_relationship, has_bio])
            if data_points == 0 and profile.name:
                profile.profile_type = "Limited Profile"

    async def _scrape_about_sections(self, base_url: str, username: str, profile: ProfileData):
        """Scrape individual about section pages."""
        sections = {
            "about_work_and_education": self._parse_work_education,
            "about_places": self._parse_places,
            "about_contact_and_basic_info": self._parse_contact,
            "about_family_and_relationships": self._parse_relationships,
            "about_details": self._parse_details,
            "about_overview": self._parse_overview,
        }

        for section, parser in sections.items():
            section_url = f"https://www.facebook.com/{username}/{section}"
            if profile.user_id and not username.replace(".", "").isalpha():
                section_url = f"https://www.facebook.com/profile.php?id={profile.user_id}&sk={section}"

            try:
                html = await self.engine.fetch_page_html(section_url)
                if html:
                    parser(html, profile)
                    await self.rate_limiter.on_request_complete()
                await self.rate_limiter.section_delay()
            except Exception as e:
                log.warning(f"  ⚠️ Failed to scrape {section}: {e}")

    def _parse_work_education(self, html: str, profile: ProfileData):
        # Work - look for employer/position patterns
        work_patterns = re.findall(
            r'"employer"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"[^}]*\}[^}]*"position"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]*)"',
            html, re.DOTALL
        )
        for company, position in work_patterns:
            profile.work.append(WorkExperience(company=company, position=position))

        # Simpler work patterns
        if not profile.work:
            companies = re.findall(r'"text"\s*:\s*"(?:Works at|Worked at)\s+([^"]+)"', html)
            for c in companies:
                profile.work.append(WorkExperience(company=c))

        # Education
        edu_patterns = re.findall(r'"text"\s*:\s*"(?:Studied|Studies|Went to|Goes to)\s+(?:at\s+)?([^"]+)"', html)
        for school in edu_patterns:
            profile.education.append(Education(school=school))

        if not profile.education:
            schools = re.findall(r'"school"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"', html)
            for s in schools:
                profile.education.append(Education(school=s))

    def _parse_places(self, html: str, profile: ProfileData):
        m = re.search(r'"text"\s*:\s*"(?:Lives in|Current city)\s+([^"]+)"', html)
        if m:
            profile.current_city = m.group(1)
        m = re.search(r'"text"\s*:\s*"(?:From|Hometown)\s+([^"]+)"', html)
        if m:
            profile.hometown = m.group(1)
        places = re.findall(r'"text"\s*:\s*"(?:Moved to|Lived in)\s+([^"]+)"', html)
        profile.places_lived.extend(places)

    def _parse_contact(self, html: str, profile: ProfileData):
        phones = re.findall(r'"text"\s*:\s*"([\+\d\s\-\(\)]{7,20})"', html)
        profile.phone_numbers.extend(phones[:5])
        emails = re.findall(r'"text"\s*:\s*"([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})"', html)
        profile.emails.extend(emails[:5])
        websites = re.findall(r'"website"\s*:\s*"([^"]+)"', html)
        profile.websites.extend(websites)
        m = re.search(r'"text"\s*:\s*"(?:Birthday|Born on)\s*\\n\s*([^"]+)"', html)
        if m:
            profile.birthday = m.group(1).strip()
        m = re.search(r'"text"\s*:\s*"(?:Birthday)\s+([^"]+)"', html)
        if m and not profile.birthday:
            profile.birthday = m.group(1).strip()
        # Languages
        langs = re.findall(r'"text"\s*:\s*"(?:Speaks|Knows)\s+([^"]+)"', html)
        for l in langs:
            profile.languages.extend([x.strip() for x in l.replace(" and ", ", ").split(",")])

    def _parse_relationships(self, html: str, profile: ProfileData):
        statuses = ["Single", "In a relationship", "Engaged", "Married", "In a civil union",
                     "In a domestic partnership", "In an open relationship", "It's complicated",
                     "Separated", "Divorced", "Widowed"]
        for status in statuses:
            if f'"{status}"' in html or f">{status}<" in html:
                profile.relationship_status = status
                break
        family = re.findall(r'"text"\s*:\s*"([^"]+)"[^}]*"subtitle"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"', html)
        for name, rel in family:
            if any(r in rel.lower() for r in ["sister", "brother", "mother", "father", "son", "daughter", "wife", "husband"]):
                profile.family_members.append(FamilyMember(name=name, relationship=rel))

    def _parse_details(self, html: str, profile: ProfileData):
        m = re.search(r'"text"\s*:\s*"(?:Interested in)\s+([^"]+)"', html)
        if m:
            profile.interested_in = m.group(1)
        m = re.search(r'"text"\s*:\s*"([^"]*)"[^}]*"(?:Religious views|Religion)"', html)
        if m:
            profile.religious_views = m.group(1)
        m = re.search(r'"text"\s*:\s*"([^"]*)"[^}]*"(?:Political views|Politics)"', html)
        if m:
            profile.political_views = m.group(1)

    def _parse_overview(self, html: str, profile: ProfileData):
        # Catch anything missed from other sections
        if not profile.current_city:
            m = re.search(r'"text"\s*:\s*"Lives in\s+([^"]+)"', html)
            if m:
                profile.current_city = m.group(1)
        if not profile.work:
            m = re.search(r'"text"\s*:\s*"Works at\s+([^"]+)"', html)
            if m:
                profile.work.append(WorkExperience(company=m.group(1)))
        if not profile.education:
            m = re.search(r'"text"\s*:\s*"(?:Studied|Went to)\s+(?:at\s+)?([^"]+)"', html)
            if m:
                profile.education.append(Education(school=m.group(1)))

    @staticmethod
    def _parse_count(text: str) -> int:
        text = text.replace(",", "").strip()
        multiplier = 1
        if text.upper().endswith("K"):
            multiplier = 1000
            text = text[:-1]
        elif text.upper().endswith("M"):
            multiplier = 1000000
            text = text[:-1]
        elif text.upper().endswith("B"):
            multiplier = 1000000000
            text = text[:-1]
        try:
            return int(float(text) * multiplier)
        except ValueError:
            return 0
