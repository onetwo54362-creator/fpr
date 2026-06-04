/**
 * FB About Mapper — Popup Script v2.0
 *
 * Controls the extension popup. Communicates with content script to:
 * 1. Retrieve captured GraphQL calls (doc_id, variables, response data)
 * 2. Auto-navigate through all about sections
 * 3. Export the captured data as JSON
 */

(function () {
  'use strict';

  // State
  let allScanData = {};
  let allGraphQLCalls = [];
  let capturedDtsg = '';
  let logLines = [];
  let highlightOn = false;
  let isScanning = false;

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
  // UI Helpers
  // =========================================================================

  function $(id) { return document.getElementById(id); }

  function addLog(msg) {
    const time = new Date().toLocaleTimeString();
    logLines.push(`[${time}] ${msg}`);
    const logArea = $('logArea');
    logArea.textContent = logLines.join('\n');
    logArea.scrollTop = logArea.scrollHeight;
  }

  function setStatus(msg) {
    $('status').textContent = msg;
  }

  function updateStats() {
    const totalFields = allGraphQLCalls.reduce((sum, c) => sum + (c.profile_fields_found || 0), 0);
    $('graphqlCount').textContent = allGraphQLCalls.length;
    $('fieldCount').textContent = totalFields;

    // Show fb_dtsg if captured
    if (capturedDtsg) {
      $('dtsgSection').style.display = 'block';
      $('dtsgValue').textContent = capturedDtsg;
    }

    // Show GraphQL call summary
    if (allGraphQLCalls.length > 0) {
      $('graphqlSection').style.display = 'block';
      const summary = $('graphqlSummary');
      summary.innerHTML = allGraphQLCalls.map(call => `
        <div class="call">
          <div class="name">${call.friendly_name || 'Unknown'}</div>
          <div class="doc-id">doc_id: ${call.doc_id || 'N/A'}</div>
          <div class="fields">${call.profile_fields_found || 0} profile fields, ${call.response_size_bytes || 0} bytes</div>
        </div>
      `).join('');
    }
  }

  // =========================================================================
  // Core: Send message to content script
  // =========================================================================

  async function sendToContent(action, data = {}) {
    return new Promise((resolve, reject) => {
      chrome.tabs.query({ active: true, currentWindow: true }, tabs => {
        if (!tabs[0]) return reject(new Error('No active tab'));
        chrome.tabs.sendMessage(tabs[0].id, { action, ...data }, response => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
          } else {
            resolve(response);
          }
        });
      });
    });
  }

  // =========================================================================
  // Scan current page
  // =========================================================================

  async function scanCurrentPage() {
    try {
      setStatus('Scanning current page...');

      // Get page scan (DOM data)
      const scanResult = await sendToContent('scan');
      if (scanResult?.success) {
        const section = scanResult.data.currentSection?.key || 'unknown';
        allScanData[section] = scanResult.data;
        $('entityName').textContent = scanResult.data.entityName || '—';
        addLog(`📍 Scanned: ${section} — ${scanResult.data.entityName}`);
      }

      // Get GraphQL log
      const gqlResult = await sendToContent('getGraphQLLog');
      if (gqlResult?.success) {
        allGraphQLCalls = gqlResult.log || [];
        capturedDtsg = gqlResult.fbDtsg || capturedDtsg;
        addLog(`📡 ${allGraphQLCalls.length} GraphQL calls captured`);
        if (capturedDtsg) {
          addLog(`🔑 fb_dtsg: ${capturedDtsg.substring(0, 30)}...`);
        }
      }

      updateStats();
      setStatus(`Scanned — ${allGraphQLCalls.length} GraphQL calls captured`);
    } catch (err) {
      addLog(`❌ Scan error: ${err.message}`);
      setStatus(`Error: ${err.message}`);
    }
  }

  // =========================================================================
  // Scan ALL sections (auto-navigate)
  // =========================================================================

  async function scanAllSections() {
    if (isScanning) return;
    isScanning = true;
    $('btnScanAll').textContent = '⏳ Scanning...';
    $('btnScanAll').disabled = true;

    addLog('🔄 Starting full scan of all about sections...');

    // First clear the GraphQL log
    try { await sendToContent('clearGraphQLLog'); } catch (e) {}
    allGraphQLCalls = [];

    for (let i = 0; i < ABOUT_SECTIONS.length; i++) {
      const section = ABOUT_SECTIONS[i];
      addLog(`📄 [${i + 1}/${ABOUT_SECTIONS.length}] Navigating to ${section.key}...`);
      setStatus(`Scanning ${i + 1}/${ABOUT_SECTIONS.length}: ${section.label}...`);

      try {
        // Navigate to the section
        await sendToContent('navigateToSection', { sectionKey: section.key });

        // Wait for page to load + GraphQL calls to complete
        await sleep(4000);

        // Now scan the page
        const scanResult = await sendToContent('scan');
        if (scanResult?.success) {
          allScanData[section.key] = scanResult.data;
        }

        // Get updated GraphQL log
        const gqlResult = await sendToContent('getGraphQLLog');
        if (gqlResult?.success) {
          allGraphQLCalls = gqlResult.log || [];
          capturedDtsg = gqlResult.fbDtsg || capturedDtsg;
        }

        const sectionFields = allGraphQLCalls.reduce((s, c) => s + (c.profile_fields_found || 0), 0);
        addLog(`✅ ${section.key}: ${allGraphQLCalls.length} calls, ${sectionFields} total fields`);

        updateStats();
      } catch (err) {
        addLog(`⚠️ Failed ${section.key}: ${err.message}`);
      }
    }

    addLog(`🏁 Full scan complete! ${allGraphQLCalls.length} GraphQL calls, ${Object.keys(allScanData).length} sections`);
    setStatus(`Done — ${allGraphQLCalls.length} GraphQL calls captured`);
    $('btnScanAll').textContent = '🔄 Scan ALL About Sections (Auto-Navigate)';
    $('btnScanAll').disabled = false;
    isScanning = false;
  }

  // =========================================================================
  // Export
  // =========================================================================

  function buildExportData() {
    // Build a clean export with GraphQL data as the primary source
    const exportData = {
      _meta: {
        version: '2.0',
        exportedAt: new Date().toISOString(),
        fbDtsg: capturedDtsg || '',
        totalGraphQLCalls: allGraphQLCalls.length,
      },
      // The key data: exact GraphQL queries Facebook uses for about pages
      graphql_queries: allGraphQLCalls.map(call => ({
        doc_id: call.doc_id,
        friendly_name: call.friendly_name,
        variables: call.variables,
        response_size_bytes: call.response_size_bytes,
        profile_fields_found: call.profile_fields_found,
        profile_fields: call.profile_fields,
        response_data: call.response_data,
      })),
      // DOM data as supplementary
      dom_sections: {},
    };

    // Add simplified DOM data
    for (const [key, data] of Object.entries(allScanData)) {
      exportData.dom_sections[key] = {
        url: data.url,
        entityType: data.entityType,
        entityName: data.entityName,
        entityId: data.entityId,
        headers: data.domData?.headers || [],
        fieldPairs: data.domData?.fieldPairs || [],
      };
    }

    return exportData;
  }

  function downloadJSON() {
    const data = buildExportData();
    const json = JSON.stringify(data, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);

    const entityName = allScanData[Object.keys(allScanData)[0]]?.entityName || 'facebook';
    const safeName = entityName.replace(/[^a-zA-Z0-9]/g, '_').substring(0, 30);
    const filename = `fb-graphql-map_${safeName}_${Date.now()}.json`;

    chrome.downloads.download({ url, filename, saveAs: true }, () => {
      URL.revokeObjectURL(url);
      addLog(`💾 Downloaded: ${filename} (${(json.length / 1024).toFixed(1)} KB)`);
    });
  }

  async function copyJSON() {
    const data = buildExportData();
    const json = JSON.stringify(data, null, 2);
    try {
      await navigator.clipboard.writeText(json);
      addLog(`📋 Copied ${(json.length / 1024).toFixed(1)} KB to clipboard`);
      setStatus('Copied to clipboard!');
    } catch (err) {
      // Fallback
      const ta = document.createElement('textarea');
      ta.value = json;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      addLog(`📋 Copied to clipboard (fallback)`);
      setStatus('Copied to clipboard!');
    }
  }

  // =========================================================================
  // Init
  // =========================================================================

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  // Wire up buttons
  $('btnScan').addEventListener('click', scanCurrentPage);
  $('btnScanAll').addEventListener('click', scanAllSections);
  $('btnDownload').addEventListener('click', downloadJSON);
  $('btnCopy').addEventListener('click', copyJSON);

  $('btnHighlight').addEventListener('click', async () => {
    highlightOn = !highlightOn;
    if (highlightOn) {
      const result = await sendToContent('highlight');
      addLog(`🎨 Highlighted ${result?.count || 0} elements`);
    } else {
      await sendToContent('removeHighlights');
      addLog('🎨 Highlights removed');
    }
  });

  $('btnClearLog').addEventListener('click', async () => {
    try {
      await sendToContent('clearGraphQLLog');
      allGraphQLCalls = [];
      updateStats();
      addLog('🗑️ GraphQL log cleared');
    } catch (e) {
      addLog('⚠️ Could not clear log');
    }
  });

  $('btnToggleLog').addEventListener('click', () => {
    $('logArea').classList.toggle('visible');
  });

  // Auto-scan on popup open
  (async () => {
    try {
      const info = await sendToContent('getPageInfo');
      if (info?.success) {
        $('entityName').textContent = info.entityName || '—';
        capturedDtsg = info.fbDtsg || '';
        $('graphqlCount').textContent = info.graphqlCalls || 0;
        if (capturedDtsg) {
          $('dtsgSection').style.display = 'block';
          $('dtsgValue').textContent = capturedDtsg;
        }
        if (info.isFacebook) {
          setStatus(`On Facebook — ${info.entityType} page`);
        } else {
          setStatus('Navigate to a Facebook page first');
        }
      }
    } catch (e) {
      setStatus('Navigate to a Facebook page first');
    }
  })();
})();
