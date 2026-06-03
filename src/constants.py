"""Constants and configuration for the Facebook Profile/Group/Page Scraper."""

import random

# =============================================================================
# GraphQL Endpoint
# =============================================================================
GRAPHQL_URL = "https://www.facebook.com/api/graphql/"

# =============================================================================
# GraphQL Document IDs
# =============================================================================
DOC_IDS = {
    "PROFILE_TIMELINE": "25430544756617998",
    "GROUP_FEED": "25716860671307636",
    "GROUP_MEMBERS": "25478249255782006",
    "PAGE_TIMELINE": "25430544756617998",
}

FRIENDLY_NAMES = {
    "PROFILE_TIMELINE": "ProfileCometTimelineFeedRefetchQuery",
    "GROUP_FEED": "GroupsCometFeedRegularStoriesPaginationQuery",
    "GROUP_MEMBERS": "GroupsCometMembersPageNewMembersSectionRefetchQuery",
    "PAGE_TIMELINE": "ProfileCometTimelineFeedRefetchQuery",
}

# =============================================================================
# Target Types
# =============================================================================
class TargetType:
    PAGE = "page"
    GROUP = "group"
    PROFILE = "profile"
    UNKNOWN = "unknown"

# =============================================================================
# About Page Section Paths
# =============================================================================
PROFILE_ABOUT_SECTIONS = [
    "about",
    "about_overview",
    "about_work_and_education",
    "about_places",
    "about_contact_and_basic_info",
    "about_family_and_relationships",
    "about_details",
    "about_life_events",
]

# =============================================================================
# User Agent Pool
# =============================================================================
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]


def get_random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def get_base_headers(user_agent: str | None = None, friendly_name: str | None = None) -> dict:
    ua = user_agent or get_random_user_agent()
    headers = {
        "user-agent": ua,
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://www.facebook.com",
        "referer": "https://www.facebook.com/",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
    }
    if friendly_name:
        headers["x-fb-friendly-name"] = friendly_name
    return headers


def get_document_headers(user_agent: str | None = None) -> dict:
    ua = user_agent or get_random_user_agent()
    return {
        "user-agent": ua,
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
    }


# =============================================================================
# Block Detection
# =============================================================================
BLOCK_STATUS_CODES = {403, 429, 503}
BLOCK_KEYWORDS = [
    "checkpoint", "login_required", "you must log in",
    "blocked", "temporarily blocked", "account has been disabled",
]
PROXY_ERROR_KEYWORDS = [
    "proxy", "407", "tunnel", "connection refused",
    "cannot connect to proxy", "eof occurred",
]

# =============================================================================
# Response / Config
# =============================================================================
FB_RESPONSE_PREFIX = "for (;;);"
EXCEL_MAX_ROWS_PER_SHEET = 1_048_575
DEFAULT_REQUEST_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_BASE_DELAY = 2
