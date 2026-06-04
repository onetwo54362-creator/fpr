"""Apify actor entry point — memory-safe for infinite URLs at 128MB RAM."""

from __future__ import annotations

import asyncio
import gc
import logging

from apify import Actor

from .constants import TargetType
from .graphql_engine import GraphQLEngine
from .group_scraper import GroupScraper
from .models import GroupData, PageData, ProfileData
from .page_scraper import PageScraper
from .profile_scraper import ProfileScraper
from .proxy_manager import ProxyManager
from .rate_limiter import RateLimiter
from .response_parser import parse_target_url

try:
    from .excel_exporter import create_excel
except ImportError:
    create_excel = None

log = logging.getLogger(__name__)


async def main():
    async with Actor:
        actor_input = await Actor.get_input() or {}

        # Parse cookies
        raw_cookies = actor_input.get('cookies', '')
        if isinstance(raw_cookies, str):
            cookies = {}
            for part in raw_cookies.split(';'):
                if '=' in part:
                    k, v = part.strip().split('=', 1)
                    cookies[k.strip()] = v.strip()
        else:
            cookies = raw_cookies

        # Parse URLs — support both field names and formats
        urls = []
        # Primary: bulk textarea (targetUrls) — one URL per line
        bulk_text = actor_input.get('targetUrls', '').strip() if isinstance(actor_input.get('targetUrls', ''), str) else ''
        if bulk_text:
            for line in bulk_text.splitlines():
                line = line.strip()
                if line and line.startswith('http'):
                    urls.append(line)
        # Fallback: single URL field
        single_url = actor_input.get('targetUrl', '').strip() if isinstance(actor_input.get('targetUrl', ''), str) else ''
        if single_url and single_url not in urls and single_url != 'https://www.facebook.com/':
            urls.append(single_url)
        # Fallback: 'urls' field (list or comma-separated string)
        if not urls:
            raw_urls = actor_input.get('urls', [])
            if isinstance(raw_urls, str):
                urls = [u.strip() for u in raw_urls.split(',') if u.strip()]
            elif isinstance(raw_urls, list):
                for u in raw_urls:
                    if isinstance(u, dict):
                        urls.append(u.get('url', ''))
                    else:
                        urls.append(str(u))
                urls = [u.strip() for u in urls if u.strip()]
        # Deduplicate
        seen = set()
        unique = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique.append(u)
        urls = unique

        if not urls:
            log.error('No URLs provided')
            return

        fb_dtsg = actor_input.get('fb_dtsg', '')
        scrape_about = actor_input.get('scrapeAbout', True)
        excel_export = actor_input.get('excelExport', False)

        proxy_config = actor_input.get('proxyConfiguration') or actor_input.get('proxy', {})
        proxy_url = None
        if proxy_config:
            proxy_group = proxy_config.get('apifyProxyGroups', ['RESIDENTIAL'])
            proxy_country = proxy_config.get('apifyProxyCountry', 'US')
            grp = proxy_group[0] if proxy_group else 'RESIDENTIAL'
            proxy_url = f"http://groups-{grp},country-{proxy_country}:@proxy.apify.com:8000"

        proxy_manager = ProxyManager(proxy_url=proxy_url)
        rate_limiter = RateLimiter(
            min_delay=actor_input.get('minCooldownSeconds', actor_input.get('minDelay', 2.0)),
            max_delay=actor_input.get('maxCooldownSeconds', actor_input.get('maxDelay', 5.0)),
        )

        engine = GraphQLEngine(cookies, fb_dtsg, proxy_manager)
        if not fb_dtsg:
            await engine.auto_fetch_fb_dtsg()

        profile_scraper = ProfileScraper(engine, rate_limiter, scrape_about=scrape_about)
        group_scraper = GroupScraper(engine, rate_limiter, scrape_about=scrape_about)
        page_scraper = PageScraper(engine, rate_limiter, scrape_about=scrape_about)

        # =====================================================================
        # Process each URL — NO accumulation, push to dataset and forget
        # =====================================================================
        dataset = await Actor.open_dataset()
        counts = {'profiles': 0, 'groups': 0, 'pages': 0, 'errors': 0}

        for idx, url in enumerate(urls, 1):
            await Actor.set_status_message(f"Processing {idx}/{len(urls)}: {url[:60]}...")
            log.info(f"\n{'='*60}")
            log.info(f"\U0001f4cc [{idx}/{len(urls)}] Processing: {url}")
            log.info(f"{'='*60}")

            try:
                target_type, entity_id, clean_url = parse_target_url(url)
                log.info(f"  Type: {target_type}, ID/Username: {entity_id}")

                # Resolve unknown types (returns compact data, NOT full HTML)
                initial_data = None
                if target_type == TargetType.UNKNOWN:
                    log.info(f"  \U0001f50d Resolving entity type for: {clean_url}")
                    resolved = await engine.resolve_entity(clean_url)
                    if resolved['type'] != TargetType.UNKNOWN:
                        target_type = resolved['type']
                    if resolved.get('id'):
                        entity_id = resolved['id']
                    initial_data = resolved
                    await rate_limiter.on_request_complete()
                    log.info(f"  \u2192 Resolved: type={target_type}, id={entity_id}")

                # Route to scraper, push to dataset, then FORGET the result
                if target_type == TargetType.PROFILE:
                    result = await profile_scraper.scrape(
                        url=clean_url, username=entity_id,
                        user_id=entity_id if entity_id.isdigit() else '',
                        initial_data=initial_data,
                    )
                    await dataset.push_data(result.to_dataset_dict())
                    log.info(f"  \u2705 Profile: {result.name}")
                    counts['profiles'] += 1

                elif target_type == TargetType.GROUP:
                    result = await group_scraper.scrape(
                        url=clean_url, group_id=entity_id,
                        initial_data=initial_data,
                    )
                    await dataset.push_data(result.to_dataset_dict())
                    log.info(f"  \u2705 Group: {result.name}")
                    counts['groups'] += 1

                elif target_type == TargetType.PAGE:
                    result = await page_scraper.scrape(
                        url=clean_url,
                        page_id=entity_id if entity_id.isdigit() else '',
                        username=entity_id if not entity_id.isdigit() else '',
                        initial_data=initial_data,
                    )
                    await dataset.push_data(result.to_dataset_dict())
                    log.info(f"  \u2705 Page: {result.name}")
                    counts['pages'] += 1

                else:
                    log.warning(f"  \u26a0\ufe0f Could not determine type \u2014 treating as profile")
                    result = await profile_scraper.scrape(
                        url=clean_url, username=entity_id,
                        initial_data=initial_data,
                    )
                    await dataset.push_data(result.to_dataset_dict())
                    counts['profiles'] += 1

            except Exception as e:
                log.error(f"  \u274c Error processing {url}: {e}", exc_info=True)
                await dataset.push_data({
                    'entity_type': 'error', 'url': url, 'error': str(e),
                })
                counts['errors'] += 1

            # Force garbage collection after each URL — critical for 128MB RAM
            initial_data = None
            result = None
            gc.collect()

            # Delay between URLs
            if idx < len(urls):
                await rate_limiter.page_delay()

        # =====================================================================
        # Summary
        # =====================================================================
        total = counts['profiles'] + counts['groups'] + counts['pages']
        summary = f"Done: {total} entities ({counts['profiles']} profiles, {counts['groups']} groups, {counts['pages']} pages)"
        if counts['errors']:
            summary += f", {counts['errors']} errors"
        await Actor.set_status_message(summary)
        log.info(f"\n{'='*60}")
        log.info(f"\U0001f3c1 {summary}")
        log.info(f"\U0001f4ca Engine stats: {engine.get_stats()}")
        log.info(f"\u23f1\ufe0f  Rate limiter: {rate_limiter.get_stats()}")
        log.info(f"\U0001f512 Proxy: {proxy_manager.get_stats()}")
        log.info(f"{'='*60}")

        await engine.close()


asyncio.run(main())
