/**
 * FB About Page Data Mapper — Content Script
 * 
 * Scans the Facebook about page DOM and maps every data element:
 * - Identifies section headers, field labels, and values
 * - Records CSS selectors, data attributes, aria labels
 * - Captures the text content and any associated metadata
 * - Analyzes the Facebook JSON embedded in <script> tags
 */

(function () {
  'use strict';

  // Prevent double-injection
  if (window.__FB_MAPPER_INJECTED) return;
  window.__FB_MAPPER_INJECTED = true;

  // =========================================================================
  // CONFIGURATION
  // =========================================================================

  const ABOUT_SECTIONS = [
    { key: 'directory_intro', label: 'Intro' },
    { key: 'directory_category', label: 'Category' },
    { key: 'directory_personal_details', label: 'Personal Details' },
    { key: 'directory_basic_info', label: 'Details' },
    { key: 'directory_links', label: 'Links' },
    { key: 'directory_specialties', label: 'Services' },
    { key: 'directory_offers', label: 'Offers' },
    { key: 'directory_work', label: 'Work' },
    { key: 'directory_education', label: 'Education' },
    { key: 'directory_activites', label: 'Hobbies' },
    { key: 'directory_interests', label: 'Interests' },
    { key: 'directory_travel', label: 'Travel' },
    { key: 'directory_contact_info', label: 'Contact Info' },
    { key: 'directory_privacy_and_legal_info', label: 'Privacy & Legal' },
    { key: 'directory_names', label: 'Names' },
    { key: 'directory_communities', label: 'Communities' },
    { key: 'about_family_and_relationships', label: 'Family & Relationships' },
    { key: 'about_places', label: 'Places Lived' },
  ];

  // Facebook-internal noise to filter out
  const NOISE_PATTERNS = [
    /^WAWeb/i, /^Comet/i, /^Relay/i, /^__require/i,
    /^undefined$/, /^null$/, /^true$/, /^false$/,
    /^\d+$/, /^[a-f0-9]{32,}$/i,
  ];

  const BOILERPLATE_TEXTS = new Set([
    'See more', 'See less', 'Like', 'Comment', 'Share', 'Send',
    'Write a comment', 'Log In', 'Sign Up', 'Create new account',
    'Forgot password?', 'Accessibility', 'Report', 'Block',
    'Privacy Policy', 'Terms of Service', 'Cookie Policy',
    'Edit', 'Add', 'Save', 'Cancel', 'Close', 'More',
    'Facebook', 'Meta', 'Help', 'About',
  ]);

  // =========================================================================
  // DOM SCANNING ENGINE
  // =========================================================================

  function scanCurrentPage() {
    const result = {
      url: window.location.href,
      timestamp: new Date().toISOString(),
      entityType: detectEntityType(),
      entityName: extractEntityName(),
      entityId: extractEntityId(),
      currentSection: detectCurrentSection(),
      domData: scanDomForData(),
      scriptData: scanScriptsForData(),
      sidebarSections: scanSidebarSections(),
      rawHtmlSnippets: {},
    };

    // Also capture the main content area HTML for reference
    const mainContent = document.querySelector('[role="main"]');
    if (mainContent) {
      result.rawHtmlSnippets.mainContentLength = mainContent.innerHTML.length;
      result.rawHtmlSnippets.mainContentPreview = mainContent.innerHTML.substring(0, 2000);
    }

    return result;
  }

  function detectEntityType() {
    const url = window.location.href;
    if (url.includes('/groups/')) return 'group';
    const html = document.documentElement.innerHTML.substring(0, 100000);
    if (html.includes('"__typename":"Page"') || html.includes('"pageID"')) return 'page';
    if (html.includes('"__typename":"User"') || html.includes('"userID"')) return 'profile';
    if (html.includes('"__typename":"Group"') || html.includes('"groupID"')) return 'group';
    return 'unknown';
  }

  function extractEntityName() {
    // og:title
    const ogTitle = document.querySelector('meta[property="og:title"]');
    if (ogTitle) {
      let name = ogTitle.getAttribute('content') || '';
      name = name.replace(/ \| Facebook$/, '').replace(/ - Facebook$/, '').trim();
      if (name && name !== 'Facebook') return name;
    }
    // <title>
    let title = document.title || '';
    title = title.replace(/ \| Facebook$/, '').replace(/ - Facebook$/, '').trim();
    if (title && title !== 'Facebook') return title;
    return '';
  }

  function extractEntityId() {
    const url = window.location.href;
    let m = url.match(/profile\.php\?id=(\d+)/);
    if (m) return m[1];
    m = url.match(/\/groups\/(\d+)/);
    if (m) return m[1];

    const html = document.documentElement.innerHTML.substring(0, 200000);
    for (const pattern of [/"userID"\s*:\s*"(\d+)"/, /"pageID"\s*:\s*"(\d+)"/, /"groupID"\s*:\s*"(\d+)"/]) {
      m = html.match(pattern);
      if (m) return m[1];
    }
    return '';
  }

  function detectCurrentSection() {
    const url = window.location.href;
    for (const s of ABOUT_SECTIONS) {
      if (url.includes(s.key)) return s;
    }
    if (url.includes('/about')) return { key: 'about', label: 'About (Overview)' };
    return { key: 'unknown', label: 'Unknown' };
  }

  // =========================================================================
  // DOM DATA EXTRACTION — The Core
  // =========================================================================

  function scanDomForData() {
    const data = {
      headers: [],      // Section headers like "Location", "Birthday", etc.
      labelValuePairs: [], // Label+value combos
      standaloneValues: [], // Values without clear labels
      links: [],         // URLs found
      images: [],        // Image URLs
      listItems: [],     // List-type data (family members, etc.)
    };

    const mainContent = document.querySelector('[role="main"]');
    const scanRoot = mainContent || document.body;

    // ── 1. Find Section Headers ──
    // Facebook uses <span> elements with specific font weights for headers
    const allSpans = scanRoot.querySelectorAll('span');
    const headerCandidates = new Map(); // text -> element

    allSpans.forEach(span => {
      const text = span.textContent.trim();
      if (!text || text.length > 100 || text.length < 2) return;
      if (BOILERPLATE_TEXTS.has(text)) return;

      const styles = window.getComputedStyle(span);
      const fontSize = parseFloat(styles.fontSize);
      const fontWeight = parseInt(styles.fontWeight);

      // Headers are typically bold (>=600) and larger (>=16px)
      if (fontWeight >= 600 && fontSize >= 16 && !isNoise(text)) {
        // Check if it's a section header (not inside a link/button)
        const isInLink = span.closest('a') !== null;
        const isInButton = span.closest('[role="button"]') !== null;
        if (!isInLink && !isInButton) {
          headerCandidates.set(text, {
            text,
            fontSize,
            fontWeight,
            selector: getCssSelector(span),
            tagPath: getTagPath(span),
            role: span.getAttribute('role') || '',
            ariaLabel: span.getAttribute('aria-label') || '',
            dataAttributes: getDataAttributes(span),
            parentDataAttributes: getDataAttributes(span.parentElement),
          });
        }
      }
    });

    data.headers = Array.from(headerCandidates.values());

    // ── 2. Find Label-Value Pairs ──
    // Facebook structures about data in containers with a label span and value span
    // Pattern: a container div with a header/label, followed by value text

    data.headers.forEach(header => {
      const headerEl = scanRoot.querySelector(header.selector);
      if (!headerEl) return;

      // Walk up to find the container
      let container = headerEl.parentElement;
      for (let i = 0; i < 5 && container; i++) {
        // Look for siblings or children that contain value text
        const values = extractValuesNearHeader(container, headerEl, header.text);
        if (values.length > 0) {
          data.labelValuePairs.push({
            label: header.text,
            values,
            headerSelector: header.selector,
            containerSelector: getCssSelector(container),
            containerTag: container.tagName,
            containerClasses: Array.from(container.classList).join(' '),
          });
          break;
        }
        container = container.parentElement;
      }
    });

    // ── 3. Find Standalone Values (text with subtitle patterns) ──
    // Facebook often has primary text + subtitle (e.g., "Manila, Philippines" + "Current city")
    const allDivs = scanRoot.querySelectorAll('div, span');
    const seenTexts = new Set();

    allDivs.forEach(el => {
      if (el.children.length > 0) return; // Only leaf nodes
      const text = el.textContent.trim();
      if (!text || text.length < 3 || text.length > 300) return;
      if (BOILERPLATE_TEXTS.has(text) || isNoise(text) || seenTexts.has(text)) return;

      const styles = window.getComputedStyle(el);
      const fontSize = parseFloat(styles.fontSize);
      const fontWeight = parseInt(styles.fontWeight);
      const color = styles.color;

      // Subtitle text (smaller, lighter color — typically the field label)
      const isSubtitle = fontSize <= 13 || (color && isGrayColor(color));
      // Primary text (normal or bold, darker)
      const isPrimary = fontSize >= 14 && fontWeight >= 400;

      if (isSubtitle || isPrimary) {
        const parentText = el.parentElement?.textContent?.trim() || '';
        const siblingTexts = getSiblingTexts(el);

        seenTexts.add(text);
        data.standaloneValues.push({
          text,
          isSubtitle,
          isPrimary,
          fontSize,
          fontWeight,
          color,
          selector: getCssSelector(el),
          tag: el.tagName,
          parentText: parentText.substring(0, 200),
          siblingTexts,
          ariaLabel: el.getAttribute('aria-label') || '',
          role: el.getAttribute('role') || '',
        });
      }
    });

    // ── 4. Links ──
    scanRoot.querySelectorAll('a[href]').forEach(a => {
      const href = a.getAttribute('href') || '';
      const text = a.textContent.trim();
      if (href && !href.startsWith('#') && text && !BOILERPLATE_TEXTS.has(text)) {
        data.links.push({
          text: text.substring(0, 200),
          href,
          selector: getCssSelector(a),
          ariaLabel: a.getAttribute('aria-label') || '',
        });
      }
    });

    // ── 5. Images ──
    scanRoot.querySelectorAll('img[src]').forEach(img => {
      const src = img.getAttribute('src') || '';
      const alt = img.getAttribute('alt') || '';
      if (src && !src.includes('emoji') && !src.includes('static')) {
        data.images.push({
          src,
          alt,
          selector: getCssSelector(img),
          width: img.naturalWidth,
          height: img.naturalHeight,
        });
      }
    });

    return data;
  }

  function extractValuesNearHeader(container, headerEl, headerText) {
    const values = [];
    const allText = container.querySelectorAll('span, div, a');
    let foundHeader = false;

    allText.forEach(el => {
      if (el === headerEl || el.contains(headerEl) || headerEl.contains(el)) {
        foundHeader = true;
        return;
      }
      if (!foundHeader) return;

      const text = el.textContent.trim();
      if (!text || text.length < 2 || text.length > 500) return;
      if (BOILERPLATE_TEXTS.has(text) || isNoise(text)) return;
      if (text === headerText) return;

      // Skip if it's another header
      const styles = window.getComputedStyle(el);
      const fontSize = parseFloat(styles.fontSize);
      const fontWeight = parseInt(styles.fontWeight);
      if (fontWeight >= 600 && fontSize >= 16) return;

      // Only leaf-like elements
      const childText = Array.from(el.children).map(c => c.textContent.trim()).join('');
      if (childText === text && el.children.length > 0) return; // Skip parent wrappers

      const isSubtitle = fontSize <= 13 || isGrayColor(styles.color);

      values.push({
        text: text.substring(0, 500),
        isSubtitle,
        fontSize,
        fontWeight,
        color: styles.color,
        selector: getCssSelector(el),
        tag: el.tagName,
        href: el.getAttribute('href') || '',
      });
    });

    // Deduplicate
    const seen = new Set();
    return values.filter(v => {
      if (seen.has(v.text)) return false;
      seen.add(v.text);
      return true;
    });
  }

  // =========================================================================
  // SCRIPT DATA EXTRACTION — Embedded JSON
  // =========================================================================

  function scanScriptsForData() {
    const result = {
      totalScripts: 0,
      dataScripts: 0,
      foundKeys: {},
      sampleData: {},
    };

    // Interesting keys that contain about page data
    const interestingKeys = [
      'name', 'bio_text', 'description', 'category_name', 'category_type',
      'birthday', 'gender', 'current_city', 'hometown', 'employer',
      'school', 'concentration', 'degree', 'position',
      'relationship_status', 'significant_other', 'family_members',
      'phone', 'email', 'website', 'social_links',
      'languages', 'religious_views', 'political_views',
      'favorite_quotes', 'intro_card', 'profile_intro',
      'interested_in', 'friend_count', 'friends_count', 'follower_count',
      'member_count', 'privacy', 'join_mode', 'creation_time',
      'page_likers', 'checkins', 'overall_star_rating',
      'hours', 'price_range', 'mission', 'company_overview',
      'address', 'latitude', 'longitude', 'founded', 'impressum',
      'vanity', 'username', 'userID', 'pageID', 'groupID',
      'is_verified', 'isVerified', 'profile_picture', 'coverPhoto',
      'is_profile_locked', 'profile_locked', 'is_locked',
      'places_lived', 'work', 'education',
      'about_collection_sections', 'profile_tab_sections',
      'about_app_sections', 'timeline_context_items',
      'all_collections', 'section_type', 'section_header',
      'collection_items', 'text_content', 'field_type',
      'profile_fields', 'profile_about_fields',
    ];

    const scripts = document.querySelectorAll('script[type="application/json"], script:not([src])');
    result.totalScripts = scripts.length;

    scripts.forEach((script, idx) => {
      const content = script.textContent || '';
      if (content.length < 50) return;

      let hasData = false;
      for (const key of interestingKeys) {
        // Check for "key" or "key": patterns
        const regex = new RegExp(`"${key}"\\s*:`, 'i');
        if (regex.test(content)) {
          hasData = true;
          if (!result.foundKeys[key]) {
            result.foundKeys[key] = [];
          }

          // Extract a snippet around the key
          const keyIdx = content.indexOf(`"${key}"`);
          if (keyIdx >= 0) {
            const snippet = content.substring(keyIdx, Math.min(keyIdx + 300, content.length));
            result.foundKeys[key].push({
              scriptIndex: idx,
              scriptType: script.getAttribute('type') || 'inline',
              snippetLength: content.length,
              valueSnippet: snippet.substring(0, 300),
            });

            // Try to extract the actual value
            try {
              const valueMatch = snippet.match(new RegExp(`"${key}"\\s*:\\s*("([^"]*)"|(\\d+\\.?\\d*)|(true|false|null)|\\{|\\[)`));
              if (valueMatch) {
                if (valueMatch[2] !== undefined) {
                  result.sampleData[key] = valueMatch[2]; // string value
                } else if (valueMatch[3] !== undefined) {
                  result.sampleData[key] = parseFloat(valueMatch[3]); // number
                } else if (valueMatch[4] !== undefined) {
                  result.sampleData[key] = valueMatch[4]; // bool/null
                } else {
                  result.sampleData[key] = '[object/array]';
                }
              }
            } catch (e) { /* ignore */ }
          }
        }
      }
      if (hasData) result.dataScripts++;
    });

    return result;
  }

  // =========================================================================
  // SIDEBAR SECTION SCANNING
  // =========================================================================

  function scanSidebarSections() {
    const sections = [];
    // Facebook sidebar links for about sections
    const links = document.querySelectorAll('a[href*="directory_"], a[href*="about_"], a[href*="/about"]');
    links.forEach(a => {
      const href = a.getAttribute('href') || '';
      const text = a.textContent.trim();
      if (text && text.length < 50) {
        sections.push({
          text,
          href,
          selector: getCssSelector(a),
          isActive: a.closest('[aria-current]') !== null ||
                    window.getComputedStyle(a).backgroundColor !== 'rgba(0, 0, 0, 0)',
        });
      }
    });
    return sections;
  }

  // =========================================================================
  // HIGHLIGHT DATA ELEMENTS ON PAGE
  // =========================================================================

  function highlightDataElements() {
    // Remove existing highlights
    document.querySelectorAll('.fb-mapper-highlight').forEach(el => el.remove());

    const mainContent = document.querySelector('[role="main"]');
    if (!mainContent) return 0;

    let count = 0;
    const allSpans = mainContent.querySelectorAll('span');

    allSpans.forEach(span => {
      const text = span.textContent.trim();
      if (!text || text.length < 2 || text.length > 300) return;
      if (BOILERPLATE_TEXTS.has(text) || isNoise(text)) return;

      const styles = window.getComputedStyle(span);
      const fontSize = parseFloat(styles.fontSize);
      const fontWeight = parseInt(styles.fontWeight);

      let color, label;
      if (fontWeight >= 600 && fontSize >= 16) {
        color = 'rgba(99, 102, 241, 0.3)'; // Purple for headers
        label = 'HEADER';
      } else if (fontSize <= 13 || isGrayColor(styles.color)) {
        color = 'rgba(251, 191, 36, 0.25)'; // Yellow for subtitles/labels
        label = 'LABEL';
      } else if (fontSize >= 14) {
        color = 'rgba(52, 211, 153, 0.2)'; // Green for values
        label = 'VALUE';
      } else {
        return;
      }

      // Only highlight leaf-like spans
      if (span.children.length === 0 || span.children.length === 1) {
        span.style.outline = `2px solid ${color.replace('0.2', '0.8').replace('0.25', '0.8').replace('0.3', '0.8')}`;
        span.style.backgroundColor = color;
        span.style.position = 'relative';

        const badge = document.createElement('span');
        badge.className = 'fb-mapper-highlight';
        badge.style.cssText = `
          position: absolute; top: -16px; left: 0; font-size: 9px; padding: 1px 4px;
          background: ${color.replace('0.2', '0.9').replace('0.25', '0.9').replace('0.3', '0.9')};
          color: white; border-radius: 3px; font-weight: 700; z-index: 10000;
          pointer-events: none; font-family: monospace;
        `;
        badge.textContent = label;
        span.appendChild(badge);
        count++;
      }
    });

    return count;
  }

  function removeHighlights() {
    document.querySelectorAll('.fb-mapper-highlight').forEach(el => el.remove());
    const mainContent = document.querySelector('[role="main"]');
    if (mainContent) {
      mainContent.querySelectorAll('span').forEach(span => {
        span.style.outline = '';
        span.style.backgroundColor = '';
      });
    }
  }

  // =========================================================================
  // UTILITY FUNCTIONS
  // =========================================================================

  function getCssSelector(el) {
    if (!el || !el.tagName) return '';
    const parts = [];
    let current = el;
    for (let i = 0; i < 6 && current && current !== document.body; i++) {
      let selector = current.tagName.toLowerCase();
      if (current.id) {
        selector += `#${current.id}`;
        parts.unshift(selector);
        break;
      }
      if (current.getAttribute('data-testid')) {
        selector += `[data-testid="${current.getAttribute('data-testid')}"]`;
        parts.unshift(selector);
        break;
      }
      if (current.getAttribute('role')) {
        selector += `[role="${current.getAttribute('role')}"]`;
      }
      // Add nth-child for uniqueness
      const parent = current.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === current.tagName);
        if (siblings.length > 1) {
          const idx = siblings.indexOf(current) + 1;
          selector += `:nth-child(${idx})`;
        }
      }
      parts.unshift(selector);
      current = current.parentElement;
    }
    return parts.join(' > ');
  }

  function getTagPath(el) {
    const parts = [];
    let current = el;
    for (let i = 0; i < 8 && current && current !== document.body; i++) {
      parts.unshift(current.tagName.toLowerCase());
      current = current.parentElement;
    }
    return parts.join(' > ');
  }

  function getDataAttributes(el) {
    if (!el || !el.attributes) return {};
    const attrs = {};
    Array.from(el.attributes).forEach(attr => {
      if (attr.name.startsWith('data-') || attr.name === 'role' || attr.name === 'aria-label') {
        attrs[attr.name] = attr.value.substring(0, 100);
      }
    });
    return attrs;
  }

  function getSiblingTexts(el) {
    const parent = el.parentElement;
    if (!parent) return [];
    const texts = [];
    parent.childNodes.forEach(node => {
      if (node === el) return;
      const text = (node.textContent || '').trim();
      if (text && text.length > 1 && text.length < 200 && !BOILERPLATE_TEXTS.has(text)) {
        texts.push(text);
      }
    });
    return texts.slice(0, 5);
  }

  function isNoise(text) {
    return NOISE_PATTERNS.some(p => p.test(text));
  }

  function isGrayColor(color) {
    const m = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if (!m) return false;
    const [r, g, b] = [parseInt(m[1]), parseInt(m[2]), parseInt(m[3])];
    // Gray = all channels similar, and not too bright (not white text)
    return Math.abs(r - g) < 20 && Math.abs(g - b) < 20 && r < 200 && r > 80;
  }

  // =========================================================================
  // MESSAGE HANDLER — Communication with popup
  // =========================================================================

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    try {
      switch (msg.action) {
        case 'scan': {
          const result = scanCurrentPage();
          sendResponse({ success: true, data: result });
          break;
        }
        case 'highlight': {
          const count = highlightDataElements();
          sendResponse({ success: true, count });
          break;
        }
        case 'removeHighlights': {
          removeHighlights();
          sendResponse({ success: true });
          break;
        }
        case 'getPageInfo': {
          sendResponse({
            success: true,
            url: window.location.href,
            isFacebook: window.location.hostname.includes('facebook.com'),
            isAboutPage: window.location.href.includes('about') || window.location.href.includes('directory_'),
            entityType: detectEntityType(),
            entityName: extractEntityName(),
          });
          break;
        }
        case 'navigateToSection': {
          const { sectionKey } = msg;
          const entityId = extractEntityId();
          const url = window.location.href;
          let newUrl;

          if (url.includes('profile.php?id=') || entityId) {
            const id = entityId || url.match(/id=(\d+)/)?.[1];
            newUrl = `https://www.facebook.com/profile.php?id=${id}&sk=${sectionKey}`;
          } else if (url.includes('/groups/')) {
            const groupId = url.match(/\/groups\/([^/?]+)/)?.[1];
            newUrl = `https://www.facebook.com/groups/${groupId}/${sectionKey === 'about' ? 'about' : sectionKey}`;
          } else {
            // Username-based
            const username = url.match(/facebook\.com\/([^/?]+)/)?.[1];
            newUrl = `https://www.facebook.com/${username}/${sectionKey}`;
          }

          if (newUrl) {
            window.location.href = newUrl;
            sendResponse({ success: true, navigatedTo: newUrl });
          } else {
            sendResponse({ success: false, error: 'Could not determine navigation URL' });
          }
          break;
        }
        default:
          sendResponse({ success: false, error: 'Unknown action' });
      }
    } catch (err) {
      sendResponse({ success: false, error: err.message });
    }
    return true; // Keep channel open for async response
  });

  console.log('[FB About Mapper] Content script loaded ✓');
})();
