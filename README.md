# Facebook Profile, Group & Page Details Scraper

Comprehensive Apify actor that scrapes detailed metadata from Facebook personal profiles, groups, and pages using authenticated HTTP requests (no browser needed).

## Features

### Profile Scraping (50+ fields)
- Basic info: name, username, user ID, profile picture, cover photo, verification status
- Bio, intro, favorite quotes
- Work history (company, position, dates)
- Education (school, degree, concentration, year)
- Places lived (current city, hometown, all places)
- Relationships & family members
- Contact info (phone, email, websites, social links)
- Personal details (birthday, gender, languages, religion, politics)
- Life events
- Social stats (friends, followers, following counts)

### Group Scraping (30+ fields)
- Basic info: name, group ID, description, cover photo
- Settings: privacy, visibility, join mode
- Stats: member count, posts per day/month, creation date
- People: admins, moderators, member list (optional)
- Content: rules, topics
- Location & history

### Page Scraping (40+ fields)
- Basic info: name, page ID, username, category, verification
- About: description, mission, company overview, founded, products, impressum
- Contact: phone, email, website, Instagram, Twitter, WhatsApp
- Location: full address, coordinates, service area
- Business: hours, price range
- Engagement: likes, followers, check-ins, rating, reviews

## Input

| Field | Type | Description |
|-------|------|-------------|
| `targetUrls` | string | Facebook URLs, one per line |
| `cookies` | string | Facebook session cookies (must have `c_user` and `xs`) |
| `fbDtsg` | string | CSRF token (auto-fetched if blank) |
| `scrapeAbout` | boolean | Scrape detailed About sections (default: true) |
| `scrapeMembers` | boolean | Scrape group member list (default: false) |
| `maxMembers` | integer | Max members to scrape (default: 100, 0 = unlimited) |
| `proxyUrl` | string | HTTP proxy URL |
| `excelExport` | boolean | Generate Excel file (default: true) |

## Cookie Formats Supported
1. **JSON array** (Cookie Editor extension): `[{"name": "c_user", "value": "123"}, ...]`
2. **JSON object**: `{"c_user": "123", "xs": "abc"}`
3. **Netscape format** (tab-separated)
4. **Header string**: `c_user=123; xs=abc;`

## Anti-Detection
- Random batch cooldowns with jitter
- Randomized user agents
- Block detection (HTTP 403/429/503, checkpoint keywords)
- Configurable request pacing

## Output
- **Dataset**: JSON objects per entity with all extracted fields
- **Key-Value Store**: Formatted Excel file (`facebook-details.xlsx`) with separate sheets for profiles, groups, and pages
