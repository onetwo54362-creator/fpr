"""Main entry point for the Facebook Profile/Group/Page Scraper Apify Actor."""

from __future__ import annotations

import asyncio
import logging

from apify import Actor

from .constants import TargetType
from .response_parser import parse_cookies, parse_target_url
from .proxy_manager import ProxyManager
from .rate_limiter import RateLimiter
from .graphql_engine import GraphQLEngine
from .profile_scraper import ProfileScraper
from .group_scraper import GroupScraper
from .page_scraper import PageScraper
from .excel_exporter import create_excel
from .models import ProfileData, GroupData, PageData

log = logging.getLogger(__name__)


async def main():
    async with Actor:
        actor_input = await Actor.get_input() or {}

        # =====================================================================
        # Parse input
        # =====================================================================
        target_urls_raw = actor_input.get("targetUrls", "")
        cookie_input = actor_input.get("cookies", "")
        fb_dtsg_input = actor_input.get("fbDtsg", "")
        scrape_about = actor_input.get("scrapeAbout", True)
        min_batch = actor_input.get("minBatchSize", 2)
        max_batch = actor_input.get("maxBatchSize", 5)
        min_cooldown = actor_input.get("minCooldownSeconds", 3)
        max_cooldown = actor_input.get("maxCooldownSeconds", 10)
        proxy_url = actor_input.get("proxyUrl", "")
        excel_export = actor_input.get("excelExport", True)

        # =====================================================================
        # Validate cookies
        # =====================================================================
        if not cookie_input:
            log.error("❌ No cookies provided. Please provide Facebook session cookies.")
            await Actor.set_status_message("Error: No cookies provided")
            return

        cookies = parse_cookies(cookie_input)
        if "c_user" not in cookies or "xs" not in cookies:
            log.error("❌ Cookies must contain 'c_user' and 'xs'. Got keys: " + ", ".join(cookies.keys()))
            await Actor.set_status_message("Error: Missing c_user or xs in cookies")
            return

        log.info(f"🍪 Cookies parsed: {len(cookies)} cookies (user: {cookies.get('c_user', '?')})")

        # =====================================================================
        # Parse URLs
        # =====================================================================
        urls = [u.strip() for u in target_urls_raw.strip().split("\n") if u.strip()]
        if not urls:
            log.error("❌ No target URLs provided.")
            await Actor.set_status_message("Error: No URLs provided")
            return

        log.info(f"🎯 Processing {len(urls)} target URL(s)")

        # =====================================================================
        # Initialize components
        # =====================================================================
        proxy_manager = ProxyManager(proxy_url=proxy_url if proxy_url else None)
        rate_limiter = RateLimiter(
            min_batch=min_batch, max_batch=max_batch,
            min_delay=min_cooldown, max_delay=max_cooldown,
        )
        engine = GraphQLEngine(
            cookies=cookies,
            fb_dtsg=fb_dtsg_input or "",
            proxy_manager=proxy_manager,
        )

        # Auto-fetch fb_dtsg if not provided
        if not fb_dtsg_input:
            token = await engine.auto_fetch_fb_dtsg()
            if not token:
                log.error("❌ Could not obtain fb_dtsg token. Please provide it manually.")
                await Actor.set_status_message("Error: Could not get fb_dtsg")
                await engine.close()
                return

        # Initialize scrapers
        profile_scraper = ProfileScraper(engine, rate_limiter, scrape_about=scrape_about)
        group_scraper = GroupScraper(engine, rate_limiter, scrape_about=scrape_about)
        page_scraper = PageScraper(engine, rate_limiter, scrape_about=scrape_about)

        # =====================================================================
        # Process each URL
        # =====================================================================
        all_profiles: list[ProfileData] = []
        all_groups: list[GroupData] = []
        all_pages: list[PageData] = []
        dataset = await Actor.open_dataset()

        for idx, url in enumerate(urls, 1):
            await Actor.set_status_message(f"Processing {idx}/{len(urls)}: {url[:60]}...")
            log.info(f"\n{'='*60}")
            log.info(f"📌 [{idx}/{len(urls)}] Processing: {url}")
            log.info(f"{'='*60}")

            try:
                # Parse URL to determine type
                target_type, entity_id, clean_url = parse_target_url(url)
                log.info(f"  Type: {target_type}, ID/Username: {entity_id}")

                # If type is unknown, resolve it by fetching the page
                html_content = None
                if target_type == TargetType.UNKNOWN:
                    log.info(f"  🔍 Resolving entity type for: {clean_url}")
                    resolved_type, resolved_id, html_content = await engine.resolve_entity_type_and_id(clean_url)
                    if resolved_type != TargetType.UNKNOWN:
                        target_type = resolved_type
                    if resolved_id:
                        entity_id = resolved_id
                    await rate_limiter.on_request_complete()
                    log.info(f"  → Resolved: type={target_type}, id={entity_id}")

                # Route to appropriate scraper
                if target_type == TargetType.PROFILE:
                    profile = await profile_scraper.scrape(
                        url=clean_url, username=entity_id, user_id=entity_id if entity_id.isdigit() else "",
                        initial_html=html_content or "",
                    )
                    await dataset.push_data(profile.to_dataset_dict())
                    all_profiles.append(profile)
                    log.info(f"  ✅ Profile: {profile.name}")

                elif target_type == TargetType.GROUP:
                    group = await group_scraper.scrape(
                        url=clean_url, group_id=entity_id,
                        initial_html=html_content or "",
                    )
                    await dataset.push_data(group.to_dataset_dict())
                    all_groups.append(group)
                    log.info(f"  ✅ Group: {group.name}")

                elif target_type == TargetType.PAGE:
                    page = await page_scraper.scrape(
                        url=clean_url, page_id=entity_id if entity_id.isdigit() else "",
                        username=entity_id if not entity_id.isdigit() else "",
                        initial_html=html_content or "",
                    )
                    await dataset.push_data(page.to_dataset_dict())
                    all_pages.append(page)
                    log.info(f"  ✅ Page: {page.name}")

                else:
                    # Last resort: treat as profile
                    log.warning(f"  ⚠️ Could not determine type — treating as profile")
                    profile = await profile_scraper.scrape(
                        url=clean_url, username=entity_id,
                        initial_html=html_content or "",
                    )
                    await dataset.push_data(profile.to_dataset_dict())
                    all_profiles.append(profile)

            except Exception as e:
                log.error(f"  ❌ Error processing {url}: {e}", exc_info=True)
                await dataset.push_data({
                    "entity_type": "error",
                    "url": url,
                    "error": str(e),
                })

            # Delay between URLs
            if idx < len(urls):
                await rate_limiter.page_delay()

        # =====================================================================
        # Excel export
        # =====================================================================
        if excel_export and (all_profiles or all_groups or all_pages):
            try:
                log.info("📊 Generating Excel export...")
                excel_bytes = create_excel(all_profiles, all_groups, all_pages)
                kvs = await Actor.open_key_value_store()
                await kvs.set_value(
                    "facebook-details.xlsx",
                    excel_bytes,
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                log.info("✅ Excel file saved to Key-Value Store as 'facebook-details.xlsx'")
            except Exception as e:
                log.error(f"❌ Excel export failed: {e}")

        # =====================================================================
        # Summary
        # =====================================================================
        total = len(all_profiles) + len(all_groups) + len(all_pages)
        summary = f"Done: {total} entities ({len(all_profiles)} profiles, {len(all_groups)} groups, {len(all_pages)} pages)"
        await Actor.set_status_message(summary)
        log.info(f"\n{'='*60}")
        log.info(f"🏁 {summary}")
        log.info(f"📊 Engine stats: {engine.get_stats()}")
        log.info(f"⏱️  Rate limiter: {rate_limiter.get_stats()}")
        log.info(f"🔒 Proxy: {proxy_manager.get_stats()}")
        log.info(f"{'='*60}")

        await engine.close()


asyncio.run(main())
