"""Facebook group details scraper.

Scrapes group metadata from the main page and /about page.
Does NOT scrape members — only details.
"""

from __future__ import annotations
import logging
import re
from .graphql_engine import GraphQLEngine
from .rate_limiter import RateLimiter
from .models import GroupData

log = logging.getLogger(__name__)

INVALID_NAMES = {
    "WAWebOpusRecorderWorkerBundle", "WebWizRecorderWorkerBundle",
    "WAWebWorkerBundle", "CometMediaViewerPhoto", "CometFeed",
    "RelayModern", "CometSinglePostRoute", "ProfileCometTimelineRoute",
    "", "Facebook", "undefined", "null",
}


class GroupScraper:
    def __init__(self, engine: GraphQLEngine, rate_limiter: RateLimiter, scrape_about: bool = True, **kwargs):
        self.engine = engine
        self.rate_limiter = rate_limiter
        self.scrape_about = scrape_about

    async def scrape(self, url: str, group_id: str, initial_html: str = "") -> GroupData:
        log.info(f"👥 Scraping group: {url}")
        group = GroupData(group_id=group_id, group_url=url)

        # Main page HTML
        html = initial_html or await self.engine.fetch_page_html(url)
        if html:
            self._extract_from_html(html, group)
            self._classify_group(html, group)
            await self.rate_limiter.on_request_complete()

        # About page — the main source of group details
        if self.scrape_about:
            about_url = f"https://www.facebook.com/groups/{group_id}/about"
            try:
                about_html = await self.engine.fetch_page_html(about_url)
                if about_html:
                    self._extract_about(about_html, group)
                    await self.rate_limiter.on_request_complete()
            except Exception as e:
                log.warning(f"  ⚠️ Failed to scrape about page: {e}")

        log.info(f"✅ Group scraped: {group.name} [{group.group_type}] ({group.member_count} members)")
        return group

    def _extract_from_html(self, html: str, group: GroupData):
        """Extract group data from the main page HTML."""
        # ── Name ──
        self._extract_name(html, group)

        # ── Group ID ──
        if not group.group_id:
            m = re.search(r'"groupID"\s*:\s*"(\d+)"', html)
            if m:
                group.group_id = m.group(1)

        # ── Privacy ──
        if '"PUBLIC"' in html or '"OPEN"' in html:
            group.privacy = "Public"
        elif '"SECRET"' in html:
            group.privacy = "Private"
            group.visibility = "Hidden"
        elif '"CLOSED"' in html or '"PRIVATE"' in html:
            group.privacy = "Private"

        # ── Member count ──
        for p in [r'"member_count"\s*:\s*(\d+)',
                  r'"group_members"\s*:\s*\{[^}]*"count"\s*:\s*(\d+)',
                  r'"text"\s*:\s*"([\d,.KMB]+)\s+(?:total\s+)?members?"']:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                group.member_count = _parse_count(m.group(1))
                if group.member_count > 0:
                    break

        # ── Cover photo ──
        m = re.search(r'"cover_photo"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
        if m:
            group.cover_photo_url = m.group(1).replace("\\/", "/")
        if not group.cover_photo_url:
            m = re.search(r'"coverPhoto"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"', html)
            if m:
                group.cover_photo_url = m.group(1).replace("\\/", "/")

        # ── Description (basic, may be overridden by about page) ──
        m = re.search(r'"description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]{10,})"', html)
        if m:
            desc = _unescape(m.group(1))
            if desc and not _is_privacy_text(desc):
                group.description = desc

        # ── Created ──
        m = re.search(r'"creation_time"\s*:\s*(\d+)', html)
        if m:
            from datetime import datetime, timezone
            try:
                dt = datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)
                group.created_at = dt.strftime("%Y-%m-%d")
            except Exception:
                pass

    def _extract_name(self, html: str, group: GroupData):
        """Extract real group name — filter out FB internal modules."""
        candidates = []

        # <title> tag
        m = re.search(r'<title[^>]*>([^<]+)</title>', html)
        if m:
            name = m.group(1).strip()
            for suffix in [" | Facebook", " - Facebook", " — Facebook", " · Facebook",
                          " | Facebook Group", " - Facebook Group"]:
                name = name.replace(suffix, "")
            if name:
                candidates.append(name.strip())

        # og:title
        m = re.search(r'<meta\s+(?:property|name)="og:title"\s+content="([^"]+)"', html)
        if not m:
            m = re.search(r'content="([^"]+)"\s+(?:property|name)="og:title"', html)
        if m:
            name = m.group(1).replace(" | Facebook", "").replace(" - Facebook", "").strip()
            if name:
                candidates.append(name)

        # __typename Group context
        m = re.search(r'"__typename"\s*:\s*"Group"[^}]*"name"\s*:\s*"([^"]{2,100})"', html)
        if m:
            candidates.append(m.group(1))

        # Reverse pattern
        m = re.search(r'"name"\s*:\s*"([^"]{2,100})"[^}]*"__typename"\s*:\s*"Group"', html)
        if m:
            candidates.append(m.group(1))

        for name in candidates:
            name = _unescape(name)
            if name and name not in INVALID_NAMES and not name.startswith("WAWeb") and len(name) < 100:
                group.name = name
                return

    def _classify_group(self, html: str, group: GroupData):
        """Classify group type."""
        snippet = html[:200000]

        unavail = ["This content isn't available", "this page isn't available",
                   "The link you followed may be broken", "this group is no longer available"]
        for sig in unavail:
            if sig.lower() in snippet.lower():
                group.group_type = "Unavailable Group"
                return

        if '"is_archived":true' in snippet or 'This group has been archived' in snippet:
            group.group_type = "Archived Group"
            return

        if group.privacy == "Private":
            if group.visibility == "Hidden" or '"SECRET"' in snippet:
                group.group_type = "Hidden Group"
            else:
                group.group_type = "Private Group"
            return

        if group.privacy == "Public":
            group.group_type = "Public Group"
            return

        # Fallback
        if '"SECRET"' in snippet:
            group.group_type = "Hidden Group"
            group.privacy = "Private"
            group.visibility = "Hidden"
        elif '"CLOSED"' in snippet or '"PRIVATE"' in snippet:
            group.group_type = "Private Group"
            group.privacy = "Private"
        else:
            group.group_type = "Public Group"
            group.privacy = "Public"

    def _extract_about(self, html: str, group: GroupData):
        """Extract comprehensive details from the /about page."""
        # ── Description (prefer longer version from about page) ──
        m = re.search(r'"description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]{10,})"', html)
        if m:
            desc = _unescape(m.group(1))
            if desc and not _is_privacy_text(desc) and len(desc) > len(group.description):
                group.description = desc

        # Also look for group_about_description
        m = re.search(r'"group_about_description"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"', html)
        if m:
            about_desc = _unescape(m.group(1))
            if about_desc and not _is_privacy_text(about_desc):
                if len(about_desc) > len(group.description):
                    group.description = about_desc

        # ── Activity / Posts per day/month ──
        for p in [r'"text"\s*:\s*"([\d,.]+)\s+(?:new\s+)?posts?\s+(?:today|a day|per day)"',
                  r'"text"\s*:\s*"([\d,.]+)\s+posts?\s+in\s+the\s+last\s+day"']:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                group.posts_per_day = float(m.group(1).replace(",", ""))
                break
        for p in [r'"text"\s*:\s*"([\d,.]+)\s+(?:new\s+)?posts?\s+(?:this month|a month|per month|last month)"',
                  r'"text"\s*:\s*"([\d,.]+)\s+posts?\s+in\s+the\s+last\s+month"']:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                group.posts_per_month = float(m.group(1).replace(",", ""))
                break

        # ── Member count (may be more accurate on about page) ──
        for p in [r'"member_count"\s*:\s*(\d+)',
                  r'"text"\s*:\s*"([\d,.KMB]+)\s+(?:total\s+)?members?"']:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                count = _parse_count(m.group(1))
                if count > group.member_count:
                    group.member_count = count
                break

        # ── Rules ──
        rules = re.findall(r'"rule_title_text"\s*:\s*"([^"]+)"', html)
        if rules:
            group.rules = [_unescape(r) for r in rules]
        else:
            # Try GroupRule typed objects
            for m in re.finditer(r'"title"\s*:\s*"([^"]+)"[^}]*"__typename"\s*:\s*"GroupRule"', html):
                rule = _unescape(m.group(1))
                if rule not in group.rules:
                    group.rules.append(rule)

        # ── Rule descriptions (append to rule title) ──
        if not group.rules:
            rule_pairs = re.findall(
                r'"rule_title_text"\s*:\s*"([^"]+)"[^}]*"rule_description_text"\s*:\s*"([^"]*)"', html
            )
            for title, desc in rule_pairs:
                full_rule = _unescape(title)
                if desc:
                    full_rule += f": {_unescape(desc)}"
                group.rules.append(full_rule)

        # ── Topics ──
        topics = re.findall(r'"text"\s*:\s*"([^"]+)"[^}]*"__typename"\s*:\s*"GroupTopicItem"', html)
        if topics:
            group.topics = [_unescape(t) for t in topics]

        # ── Location ──
        for p in [r'"text"\s*:\s*"([^"]+)"[^}]*"(?:location|city)"',
                  r'"location"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"',
                  r'"city"\s*:\s*"([^"]+)"']:
            m = re.search(p, html, re.IGNORECASE)
            if m:
                loc = _unescape(m.group(1))
                if loc and not _is_privacy_text(loc):
                    group.location = loc
                    break

        # ── Join mode ──
        if '"CAN_JOIN"' in html or '"join_action_type":"JOIN"' in html:
            group.join_mode = "Open"
        elif '"REQUEST_TO_JOIN"' in html or '"join_action_type":"REQUEST"' in html:
            group.join_mode = "Approval Required"

        # ── History / additional about text ──
        m = re.search(r'"history"\s*:\s*\{[^}]*"text"\s*:\s*"([^"]+)"', html)
        if m:
            group.history = _unescape(m.group(1))

        # ── Admins (names only from about page) ──
        # We extract admin names that appear on the about page
        admins_text = re.findall(r'"role"\s*:\s*"ADMIN"[^}]*"name"\s*:\s*"([^"]+)"', html)
        from .models import GroupMember
        for name in admins_text:
            name = _unescape(name)
            if name and name not in INVALID_NAMES and not any(a.name == name for a in group.admins):
                group.admins.append(GroupMember(name=name, role="Admin"))

        # ── Moderators ──
        mods_text = re.findall(r'"role"\s*:\s*"MODERATOR"[^}]*"name"\s*:\s*"([^"]+)"', html)
        for name in mods_text:
            name = _unescape(name)
            if name and name not in INVALID_NAMES and not any(m.name == name for m in group.moderators):
                group.moderators.append(GroupMember(name=name, role="Moderator"))

        # ── Visibility ──
        if not group.visibility:
            if '"VISIBLE"' in html:
                group.visibility = "Visible"
            elif '"HIDDEN"' in html or '"SECRET"' in html:
                group.visibility = "Hidden"

        # ── Additional text fields that might contain about info ──
        # Look for all substantial text blocks on the about page
        all_texts = re.findall(r'"text"\s*:\s*"([^"]{20,500})"', html)
        for t in all_texts:
            t = _unescape(t)
            if _is_privacy_text(t) or _is_boilerplate(t):
                continue
            # If we still don't have a description and this looks like one
            if not group.description and len(t) > 30:
                group.description = t
            elif not group.history and len(t) > 50 and t != group.description:
                group.history = t


# =============================================================================
# Utility functions
# =============================================================================

def _is_privacy_text(text: str) -> bool:
    """Check if text is just a Facebook privacy description, not real content."""
    privacy_phrases = [
        "Anyone can see who", "Only members can see",
        "anyone can find this group", "Only members can find",
        "Anyone can see who's in the group and what they post",
        "Only members can see who",
    ]
    return any(p.lower() in text.lower() for p in privacy_phrases)


def _is_boilerplate(text: str) -> bool:
    boilerplate = ["See more", "See less", "Like", "Comment", "Share",
                   "Log in", "Sign up", "Privacy Policy", "Terms of Service",
                   "WAWeb", "RelayModern", "CometFeed", "Anyone can see",
                   "Only members", "Visible", "Public"]
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
