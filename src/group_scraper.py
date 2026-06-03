"""Facebook group details scraper."""

from __future__ import annotations
import logging
import re
from typing import Optional
from .graphql_engine import GraphQLEngine
from .rate_limiter import RateLimiter
from .response_parser import find_nested_value, find_all_nested_values, find_typed_objects
from .models import GroupData, GroupMember

log = logging.getLogger(__name__)


class GroupScraper:
    def __init__(self, engine: GraphQLEngine, rate_limiter: RateLimiter,
                 scrape_about: bool = True, scrape_members: bool = False, max_members: int = 100):
        self.engine = engine
        self.rate_limiter = rate_limiter
        self.scrape_about = scrape_about
        self.scrape_members = scrape_members
        self.max_members = max_members

    async def scrape(self, url: str, group_id: str, initial_html: str = "") -> GroupData:
        log.info(f"👥 Scraping group: {url}")
        group = GroupData(group_id=group_id, group_url=url)

        # Main page HTML
        html = initial_html or await self.engine.fetch_page_html(url)
        if html:
            self._extract_from_html(html, group)
            self._classify_group(html, group)
            await self.rate_limiter.on_request_complete()

        # About page
        if self.scrape_about:
            about_url = f"https://www.facebook.com/groups/{group_id}/about"
            about_html = await self.engine.fetch_page_html(about_url)
            if about_html:
                self._extract_about(about_html, group)
                await self.rate_limiter.on_request_complete()
            await self.rate_limiter.section_delay()

            # Members page (admins/moderators)
            members_url = f"https://www.facebook.com/groups/{group_id}/members"
            members_html = await self.engine.fetch_page_html(members_url)
            if members_html:
                self._extract_members(members_html, group)
                await self.rate_limiter.on_request_complete()

        log.info(f"✅ Group scraped: {group.name} [{group.group_type}] ({group.member_count} members)")
        return group

    def _extract_from_html(self, html: str, group: GroupData):
        # Name
        for p in [r'"name"\s*:\s*"([^"]{2,100})".*?"__typename"\s*:\s*"Group"',
                  r'<title>([^<]+)</title>']:
            m = re.search(p, html, re.DOTALL)
            if m:
                name = m.group(1).replace(" | Facebook", "").replace(" - Facebook", "").strip()
                if name and name != "Facebook" and len(name) < 100:
                    group.name = name
                    break

        # Fallback name from Group typed objects
        if not group.name:
            m = re.search(r'"__typename"\s*:\s*"Group"[^}]*"name"\s*:\s*"([^"]+)"', html)
            if m:
                group.name = m.group(1)

        # Group ID
        if not group.group_id:
            m = re.search(r'"groupID"\s*:\s*"(\d+)"', html)
            if m:
                group.group_id = m.group(1)

        # Privacy
        if '"PUBLIC"' in html or '"OPEN"' in html:
            group.privacy = "Public"
        elif '"CLOSED"' in html or '"PRIVATE"' in html:
            group.privacy = "Private"
        elif '"SECRET"' in html:
            group.privacy = "Private"
            group.visibility = "Hidden"

        # Member count
        m = re.search(r'"member_count"\s*:\s*(\d+)', html)
        if m:
            group.member_count = int(m.group(1))
        else:
            m = re.search(r'"text"\s*:\s*"([\d,.KMB]+)\s+(?:members?|total members?)"', html, re.IGNORECASE)
            if m:
                group.member_count = self._parse_count(m.group(1))

        # Cover photo
        m = re.search(r'"cover_photo"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if m:
            group.cover_photo_url = m.group(1).replace("\\/", "/")

        # Description
        m = re.search(r'"description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]{10,})"', html)
        if m:
            group.description = m.group(1).encode().decode('unicode_escape', errors='ignore')

        # Created
        m = re.search(r'"creation_time"\s*:\s*(\d+)', html)
        if m:
            from datetime import datetime, timezone
            try:
                dt = datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)
                group.created_at = dt.strftime("%Y-%m-%d")
            except Exception:
                pass

    def _classify_group(self, html: str, group: GroupData):
        """Classify group type based on HTML signals and extracted data."""
        snippet = html[:200000]

        # Unavailable
        unavailable_signals = [
            "This content isn't available",
            "this page isn't available",
            "The link you followed may be broken",
            "this group is no longer available",
        ]
        for sig in unavailable_signals:
            if sig.lower() in snippet.lower():
                group.group_type = "Unavailable Group"
                return

        # Archived
        if '"is_archived":true' in snippet or '"ARCHIVED"' in snippet or 'This group has been archived' in snippet:
            group.group_type = "Archived Group"
            return

        # Determine from extracted privacy field
        if group.privacy == "Private":
            if group.visibility == "Hidden" or '"SECRET"' in snippet:
                group.group_type = "Hidden Group"
            else:
                group.group_type = "Private Group"
            return

        if group.privacy == "Public":
            group.group_type = "Public Group"
            return

        # Fallback detection from HTML
        if '"SECRET"' in snippet:
            group.group_type = "Hidden Group"
            group.privacy = "Private"
            group.visibility = "Hidden"
        elif '"CLOSED"' in snippet or '"PRIVATE"' in snippet:
            group.group_type = "Private Group"
            group.privacy = "Private"
        elif '"OPEN"' in snippet or '"PUBLIC"' in snippet:
            group.group_type = "Public Group"
            group.privacy = "Public"
        else:
            group.group_type = "Public Group"  # default

    def _extract_about(self, html: str, group: GroupData):
        # Description (may be more complete on about page)
        m = re.search(r'"description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]{10,})"', html)
        if m:
            desc = m.group(1).encode().decode('unicode_escape', errors='ignore')
            if len(desc) > len(group.description):
                group.description = desc

        # Activity
        m = re.search(r'"text"\s*:\s*"([\d,.]+)\s+(?:new\s+)?posts?\s+(?:today|a day|per day)"', html, re.IGNORECASE)
        if m:
            group.posts_per_day = float(m.group(1).replace(",", ""))
        m = re.search(r'"text"\s*:\s*"([\d,.]+)\s+(?:new\s+)?posts?\s+(?:this month|a month|per month|last month)"', html, re.IGNORECASE)
        if m:
            group.posts_per_month = float(m.group(1).replace(",", ""))

        # Rules
        rules = re.findall(r'"rule_title_text"\s*:\s*"([^"]+)"', html)
        if rules:
            group.rules = rules
        else:
            rules = re.findall(r'"title"\s*:\s*"([^"]+)"[^}]*"description"\s*:\s*"([^"]*)"[^}]*"__typename"\s*:\s*"GroupRule"', html)
            for title, desc in rules:
                group.rules.append(f"{title}: {desc}" if desc else title)

        # Topics
        topics = re.findall(r'"text"\s*:\s*"([^"]+)"[^}]*"__typename"\s*:\s*"GroupTopicItem"', html)
        if topics:
            group.topics = topics

        # Location
        m = re.search(r'"text"\s*:\s*"([^"]+)"[^}]*"(?:location|city)"', html, re.IGNORECASE)
        if m:
            group.location = m.group(1)

        # History / About text
        m = re.search(r'"group_about_description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"', html)
        if m:
            group.history = m.group(1).encode().decode('unicode_escape', errors='ignore')

        # Join mode
        if '"CAN_JOIN"' in html or '"join_action_type":"JOIN"' in html:
            group.join_mode = "Open"
        elif '"REQUEST_TO_JOIN"' in html or '"join_action_type":"REQUEST"' in html:
            group.join_mode = "Approval Required"

    def _extract_members(self, html: str, group: GroupData):
        # Extract admins
        admin_section = re.findall(
            r'"role"\s*:\s*"ADMIN"[^}]*"name"\s*:\s*"([^"]+)"[^}]*"url"\s*:\s*"([^"]*)"[^}]*"id"\s*:\s*"(\d+)"',
            html
        )
        for name, url, uid in admin_section:
            group.admins.append(GroupMember(name=name, user_id=uid, profile_url=url.replace("\\/", "/"), role="Admin"))

        # Simpler admin patterns
        if not group.admins:
            admins = re.findall(r'"text"\s*:\s*"Admin[s]?"[^}]*"name"\s*:\s*"([^"]+)"', html)
            for name in admins:
                group.admins.append(GroupMember(name=name, role="Admin"))

        # Moderators
        mod_section = re.findall(
            r'"role"\s*:\s*"MODERATOR"[^}]*"name"\s*:\s*"([^"]+)"[^}]*"url"\s*:\s*"([^"]*)"[^}]*"id"\s*:\s*"(\d+)"',
            html
        )
        for name, url, uid in mod_section:
            group.moderators.append(GroupMember(name=name, user_id=uid, profile_url=url.replace("\\/", "/"), role="Moderator"))

        # General members
        member_entries = re.findall(
            r'"__typename"\s*:\s*"(?:User|GroupMember)"[^}]*"name"\s*:\s*"([^"]+)"[^}]*"id"\s*:\s*"(\d+)"',
            html
        )
        seen_ids = {a.user_id for a in group.admins} | {m.user_id for m in group.moderators}
        for name, uid in member_entries:
            if uid not in seen_ids and uid != self.engine.cookies.get("c_user", ""):
                seen_ids.add(uid)
                group.members.append(GroupMember(name=name, user_id=uid, role="Member"))
                if self.max_members > 0 and len(group.members) >= self.max_members:
                    break

    @staticmethod
    def _parse_count(text: str) -> int:
        text = text.replace(",", "").strip()
        mult = 1
        if text.upper().endswith("K"): mult, text = 1000, text[:-1]
        elif text.upper().endswith("M"): mult, text = 1000000, text[:-1]
        elif text.upper().endswith("B"): mult, text = 1000000000, text[:-1]
        try: return int(float(text) * mult)
        except ValueError: return 0
