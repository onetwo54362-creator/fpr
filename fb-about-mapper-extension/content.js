/**
 * FB About Page Data Mapper — Content Script v2.0
 *
 * UPGRADED: Now intercepts real GraphQL network requests instead of
 * DOM-scraping. Captures doc_id, variables, and response JSON from
 * every /api/graphql/ call Facebook makes when loading about pages.
 *
 * This gives us the exact queries needed for memory-efficient scraping.
 */

(function () {
  'use strict';

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

  // =========================================================================
  // GRAPHQL INTERCEPTOR — Captures real API calls
  // =========================================================================

  // Store captured GraphQL calls
  window.__FB_GRAPHQL_LOG = [];

  // Also capture fb_dtsg from any request
  window.__FB_DTSG_CAPTURED = '';

  // Intercept fetch() — Facebook uses fetch for GraphQL
  const originalFetch = window.fetch;
  window.fetch = async function (...args) {
    const [url, options] = args;
    const urlStr = typeof url === 'string' ? url : url?.url || '';

    // Only intercept GraphQL calls
    if (urlStr.includes('/api/graphql')) {
      try {
        const body = options?.body;
        let parsed = {};

        if (typeof body === 'string') {
          parsed = Object.fromEntries(new URLSearchParams(body));
        } else if (body instanceof URLSearchParams) {
          parsed = Object.fromEntries(body);
        } else if (body instanceof FormData) {
          parsed = {};
          for (const [k, v] of body.entries()) parsed[k] = v;
        }

        const docId = parsed.doc_id || '';
        const friendlyName = parsed.fb_api_req_friendly_name || '';
        const fbDtsg = parsed.fb_dtsg || '';
        let variables = {};

        if (fbDtsg && !window.__FB_DTSG_CAPTURED) {
          window.__FB_DTSG_CAPTURED = fbDtsg;
          console.log('[FB Mapper] 🔑 Captured fb_dtsg:', fbDtsg.substring(0, 20) + '...');
        }

        try {
          variables = JSON.parse(parsed.variables || '{}');
        } catch (e) { /* ignore */ }

        // Make the actual request
        const response = await originalFetch.apply(this, args);
        const clone = response.clone();

        // Try to read and parse the response
        try {
          const responseText = await clone.text();
          let responseData = null;

          // Facebook prepends "for (;;);" to responses
          const cleaned = responseText.replace(/^for \(;;\);/, '').trim();
          // May have multiple JSON lines
          const lines = cleaned.split('\n').filter(l => l.trim().startsWith('{'));
          if (lines.length > 0) {
            try {
              responseData = JSON.parse(lines[0]);
            } catch (e) {
              // Try parsing all lines
              responseData = lines.map(l => {
                try { return JSON.parse(l); } catch (_) { return null; }
              }).filter(Boolean);
            }
          }

          // Extract profile_fields from response
          const profileFields = [];
          const extractFields = (obj) => {
            if (!obj || typeof obj !== 'object') return;
            if (Array.isArray(obj)) { obj.forEach(extractFields); return; }
            if (obj.profile_fields && obj.profile_fields.nodes) {
              profileFields.push(...obj.profile_fields.nodes);
            }
            if (obj.field_type) {
              profileFields.push(obj);
            }
            Object.values(obj).forEach(v => {
              if (v && typeof v === 'object') extractFields(v);
            });
          };
          if (responseData) extractFields(responseData);

          const entry = {
            timestamp: new Date().toISOString(),
            doc_id: docId,
            friendly_name: friendlyName,
            variables: variables,
            fb_dtsg: fbDtsg ? fbDtsg.substring(0, 30) + '...' : '',
            response_size_bytes: responseText.length,
            profile_fields_found: profileFields.length,
            profile_fields: profileFields.slice(0, 50), // Cap at 50
            response_data: responseData,
            response_preview: responseText.substring(0, 500),
          };

          window.__FB_GRAPHQL_LOG.push(entry);
          console.log(
            `[FB Mapper] 📡 GraphQL: ${friendlyName || docId} → ${responseText.length} bytes, ${profileFields.length} fields`
          );
        } catch (e) {
          window.__FB_GRAPHQL_LOG.push({
            timestamp: new Date().toISOString(),
            doc_id: docId,
            friendly_name: friendlyName,
            variables: variables,
            error: e.message,
          });
        }

        return response;
      } catch (e) {
        // If anything fails, still make the original request
        return originalFetch.apply(this, args);
      }
    }

    return originalFetch.apply(this, args);
  };

  // Also intercept XMLHttpRequest (backup)
  const origXHROpen = XMLHttpRequest.prototype.open;
  const origXHRSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__fbMapperUrl = url;
    this.__fbMapperMethod = method;
    return origXHROpen.call(this, method, url, ...rest);
  };

  XMLHttpRequest.prototype.send = function (body) {
    if (this.__fbMapperUrl && this.__fbMapperUrl.includes('/api/graphql')) {
      let parsed = {};
      if (typeof body === 'string') {
        try { parsed = Object.fromEntries(new URLSearchParams(body)); } catch (e) {}
      }

      const docId = parsed.doc_id || '';
      const friendlyName = parsed.fb_api_req_friendly_name || '';
      const fbDtsg = parsed.fb_dtsg || '';

      if (fbDtsg && !window.__FB_DTSG_CAPTURED) {
        window.__FB_DTSG_CAPTURED = fbDtsg;
      }

      let variables = {};
      try { variables = JSON.parse(parsed.variables || '{}'); } catch (e) {}

      this.addEventListener('load', () => {
        try {
          const responseText = this.responseText || '';
          const cleaned = responseText.replace(/^for \(;;\);/, '').trim();
          const lines = cleaned.split('\n').filter(l => l.trim().startsWith('{'));
          let responseData = null;
          if (lines.length > 0) {
            try { responseData = JSON.parse(lines[0]); } catch (e) {}
          }

          const profileFields = [];
          const extractFields = (obj) => {
            if (!obj || typeof obj !== 'object') return;
            if (Array.isArray(obj)) { obj.forEach(extractFields); return; }
            if (obj.profile_fields && obj.profile_fields.nodes) {
              profileFields.push(...obj.profile_fields.nodes);
            }
            if (obj.field_type) profileFields.push(obj);
            Object.values(obj).forEach(v => {
              if (v && typeof v === 'object') extractFields(v);
            });
          };
          if (responseData) extractFields(responseData);

          window.__FB_GRAPHQL_LOG.push({
            timestamp: new Date().toISOString(),
            doc_id: docId,
            friendly_name: friendlyName,
            variables: variables,
            response_size_bytes: responseText.length,
            profile_fields_found: profileFields.length,
            profile_fields: profileFields.slice(0, 50),
            response_data: responseData,
            response_preview: responseText.substring(0, 500),
            via: 'xhr',
          });
        } catch (e) { /* ignore */ }
      });
    }
    return origXHRSend.call(this, body);
  };

  // =========================================================================
  // DOM SCANNING (simplified — kept for basic info)
  // =========================================================================

  function scanCurrentPage() {
    return {
      url: window.location.href,
      timestamp: new Date().toISOString(),
      entityType: detectEntityType(),
      entityName: extractEntityName(),
      entityId: extractEntityId(),
      currentSection: detectCurrentSection(),
      domData: scanDomLabels(),
      graphqlCalls: window.__FB_GRAPHQL_LOG.length,
      fbDtsg: window.__FB_DTSG_CAPTURED || '',
    };
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
    const ogTitle = document.querySelector('meta[property="og:title"]');
    if (ogTitle) {
      let name = ogTitle.getAttribute('content') || '';
      name = name.replace(/ \| Facebook$/, '').replace(/ - Facebook$/, '').trim();
      if (name && name !== 'Facebook') return name;
    }
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

  // Simplified DOM scan — just labels and values, no heavy selectors
  function scanDomLabels() {
    const data = { headers: [], fieldPairs: [] };
    const mainContent = document.querySelector('[role="main"]');
    if (!mainContent) return data;

    const BOILERPLATE = new Set([
      'See more', 'See less', 'Like', 'Comment', 'Share', 'Send',
      'Log In', 'Sign Up', 'Edit', 'Add', 'Save', 'Cancel', 'Close',
      'Facebook', 'Meta', 'Help', 'About', 'More',
    ]);

    // Find headers (bold, 16px+)
    const headers = [];
    mainContent.querySelectorAll('span').forEach(span => {
      const text = span.textContent.trim();
      if (!text || text.length > 100 || text.length < 2) return;
      if (BOILERPLATE.has(text)) return;
      const s = window.getComputedStyle(span);
      if (parseInt(s.fontWeight) >= 600 && parseFloat(s.fontSize) >= 16) {
        if (!span.closest('a') && !span.closest('[role="button"]')) {
          headers.push({ text, el: span });
        }
      }
    });

    data.headers = headers.map(h => h.text);

    // Find values near each header
    headers.forEach(h => {
      const container = h.el.parentElement?.parentElement?.parentElement;
      if (!container) return;
      const values = [];
      const seen = new Set();
      container.querySelectorAll('span, div, a').forEach(el => {
        if (el === h.el || el.contains(h.el) || h.el.contains(el)) return;
        const text = el.textContent.trim();
        if (!text || text.length < 2 || text.length > 500 || BOILERPLATE.has(text) || seen.has(text)) return;
        if (text === h.text) return;
        const s = window.getComputedStyle(el);
        const isSubtitle = parseFloat(s.fontSize) <= 13;
        const childText = Array.from(el.children).map(c => c.textContent.trim()).join('');
        if (childText === text && el.children.length > 0) return;
        seen.add(text);
        values.push({ text: text.substring(0, 500), isSubtitle, tag: el.tagName });
      });
      if (values.length > 0) {
        data.fieldPairs.push({ label: h.text, values });
      }
    });

    return data;
  }

  // =========================================================================
  // HIGHLIGHT DATA ELEMENTS
  // =========================================================================

  function highlightDataElements() {
    document.querySelectorAll('.fb-mapper-highlight').forEach(el => el.remove());
    const mainContent = document.querySelector('[role="main"]');
    if (!mainContent) return 0;
    let count = 0;
    mainContent.querySelectorAll('span').forEach(span => {
      const text = span.textContent.trim();
      if (!text || text.length < 2 || text.length > 300) return;
      const s = window.getComputedStyle(span);
      const fw = parseInt(s.fontWeight);
      const fs = parseFloat(s.fontSize);
      let color, label;
      if (fw >= 600 && fs >= 16) { color = 'rgba(99, 102, 241, 0.3)'; label = 'HEADER'; }
      else if (fs <= 13) { color = 'rgba(251, 191, 36, 0.25)'; label = 'LABEL'; }
      else if (fs >= 14) { color = 'rgba(52, 211, 153, 0.2)'; label = 'VALUE'; }
      else return;
      if (span.children.length <= 1) {
        span.style.outline = `2px solid ${color.replace(/0\.\d+\)/, '0.8)')}`;
        span.style.backgroundColor = color;
        const badge = document.createElement('span');
        badge.className = 'fb-mapper-highlight';
        badge.style.cssText = `position:absolute;top:-16px;left:0;font-size:9px;padding:1px 4px;background:${color.replace(/0\.\d+\)/, '0.9)')};color:white;border-radius:3px;font-weight:700;z-index:10000;pointer-events:none;font-family:monospace;`;
        badge.textContent = label;
        span.style.position = 'relative';
        span.appendChild(badge);
        count++;
      }
    });
    return count;
  }

  // =========================================================================
  // MESSAGE HANDLER
  // =========================================================================

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    try {
      switch (msg.action) {
        case 'scan': {
          const result = scanCurrentPage();
          sendResponse({ success: true, data: result });
          break;
        }
        case 'getGraphQLLog': {
          // Return ALL captured GraphQL calls
          sendResponse({
            success: true,
            log: window.__FB_GRAPHQL_LOG,
            fbDtsg: window.__FB_DTSG_CAPTURED || '',
            totalCalls: window.__FB_GRAPHQL_LOG.length,
          });
          break;
        }
        case 'clearGraphQLLog': {
          window.__FB_GRAPHQL_LOG = [];
          sendResponse({ success: true });
          break;
        }
        case 'highlight': {
          const count = highlightDataElements();
          sendResponse({ success: true, count });
          break;
        }
        case 'removeHighlights': {
          document.querySelectorAll('.fb-mapper-highlight').forEach(el => el.remove());
          document.querySelector('[role="main"]')?.querySelectorAll('span').forEach(span => {
            span.style.outline = '';
            span.style.backgroundColor = '';
          });
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
            fbDtsg: window.__FB_DTSG_CAPTURED || '',
            graphqlCalls: window.__FB_GRAPHQL_LOG.length,
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
            const username = url.match(/facebook\.com\/([^/?]+)/)?.[1];
            newUrl = `https://www.facebook.com/${username}/${sectionKey}`;
          }
          if (newUrl) {
            window.location.href = newUrl;
            sendResponse({ success: true, navigatedTo: newUrl });
          } else {
            sendResponse({ success: false, error: 'Could not determine URL' });
          }
          break;
        }
        default:
          sendResponse({ success: false, error: 'Unknown action' });
      }
    } catch (err) {
      sendResponse({ success: false, error: err.message });
    }
    return true;
  });

  console.log('[FB Mapper v2] Content script loaded — GraphQL interceptor active ✓');
})();
