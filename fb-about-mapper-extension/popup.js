/**
 * FB About Mapper — Popup Script
 * Controls scanning, export, and auto-navigation through about sections.
 */

(function () {
  'use strict';

  // Elements
  const pageUrl = document.getElementById('pageUrl');
  const statusBar = document.getElementById('status');
  const statsPanel = document.getElementById('statsPanel');
  const statSections = document.getElementById('statSections');
  const statFields = document.getElementById('statFields');
  const statValues = document.getElementById('statValues');
  const progressBar = document.getElementById('progressBar');
  const progressFill = document.getElementById('progressFill');
  const sectionList = document.getElementById('sectionList');
  const logEl = document.getElementById('log');
  const btnScan = document.getElementById('btnScan');
  const btnScanAll = document.getElementById('btnScanAll');
  const btnExport = document.getElementById('btnExport');
  const btnDownload = document.getElementById('btnDownload');
  const btnToggleLog = document.getElementById('btnToggleLog');
  const btnHighlight = document.getElementById('btnHighlight');

  let allResults = {};
  let highlightActive = false;

  // ── Init ──
  checkCurrentPage();

  // ── Event Listeners ──
  btnScan.addEventListener('click', scanCurrentPage);
  btnScanAll.addEventListener('click', scanAllSections);
  btnExport.addEventListener('click', copyToClipboard);
  btnDownload.addEventListener('click', downloadJson);
  btnToggleLog.addEventListener('click', () => logEl.classList.toggle('visible'));
  btnHighlight.addEventListener('click', toggleHighlight);

  // =========================================================================
  // PAGE CHECK
  // =========================================================================

  async function checkCurrentPage() {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (!tab) {
        setStatus('No active tab', 'error');
        return;
      }

      const response = await chrome.tabs.sendMessage(tab.id, { action: 'getPageInfo' });
      if (response.success) {
        if (!response.isFacebook) {
          setStatus('Not a Facebook page', 'error');
          pageUrl.textContent = tab.url;
          btnScan.disabled = true;
          return;
        }

        pageUrl.textContent = response.entityName || tab.url.substring(0, 60);
        statusBar.className = response.isAboutPage ? 'status-bar success' : 'status-bar';

        if (response.isAboutPage) {
          addLog(`📍 On about page: ${response.entityType} — ${response.entityName}`, 'success');
        } else {
          addLog(`📍 On Facebook: ${response.entityType} — ${response.entityName}`, 'info');
        }

        btnScanAll.style.display = 'block';
      }
    } catch (err) {
      setStatus('Navigate to a Facebook profile/page/group first', 'error');
      addLog(`❌ ${err.message}`, 'error');
      btnScan.disabled = true;
    }
  }

  // =========================================================================
  // SCAN CURRENT PAGE
  // =========================================================================

  async function scanCurrentPage() {
    btnScan.disabled = true;
    btnScan.innerHTML = '<span>⏳</span> Scanning...';
    addLog('🔍 Scanning current page DOM...', 'info');

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const response = await chrome.tabs.sendMessage(tab.id, { action: 'scan' });

      if (response.success) {
        const data = response.data;
        const sectionKey = data.currentSection.key;
        allResults[sectionKey] = data;

        // Update stats
        const headerCount = data.domData.headers.length;
        const fieldCount = data.domData.labelValuePairs.length;
        const valueCount = data.domData.standaloneValues.length;
        const scriptKeys = Object.keys(data.scriptData.foundKeys).length;

        statsPanel.style.display = 'grid';
        statSections.textContent = Object.keys(allResults).length;
        statFields.textContent = fieldCount + scriptKeys;
        statValues.textContent = valueCount;

        addLog(`✅ Found: ${headerCount} headers, ${fieldCount} field pairs, ${valueCount} standalone values`, 'success');
        addLog(`📦 Script data: ${data.scriptData.dataScripts} data scripts, ${scriptKeys} interesting keys`, 'info');

        // Show section details
        updateSectionList(data);

        // Show export buttons
        btnExport.style.display = 'block';
        btnDownload.style.display = 'block';
        btnHighlight.style.display = 'block';

        // Log sample data from scripts
        if (Object.keys(data.scriptData.sampleData).length > 0) {
          addLog('📋 Sample script data:', 'info');
          for (const [key, val] of Object.entries(data.scriptData.sampleData).slice(0, 10)) {
            const displayVal = typeof val === 'string' ? val.substring(0, 60) : val;
            addLog(`   ${key}: ${displayVal}`, 'info');
          }
        }

        // Log DOM headers
        if (data.domData.headers.length > 0) {
          addLog('📌 DOM Headers found:', 'info');
          data.domData.headers.forEach(h => addLog(`   • ${h.text}`, 'info'));
        }
      }
    } catch (err) {
      addLog(`❌ Scan error: ${err.message}`, 'error');
    }

    btnScan.disabled = false;
    btnScan.innerHTML = '<span>⚡</span> Scan This About Page';
  }

  // =========================================================================
  // SCAN ALL SECTIONS (Auto-Navigate)
  // =========================================================================

  async function scanAllSections() {
    const sections = [
      'directory_intro', 'directory_category', 'directory_personal_details',
      'directory_basic_info', 'directory_links', 'directory_specialties',
      'directory_offers', 'directory_work', 'directory_education',
      'directory_activites', 'directory_interests', 'directory_travel',
      'directory_contact_info', 'directory_privacy_and_legal_info',
      'directory_names', 'directory_communities',
      'about_family_and_relationships', 'about_places',
    ];

    btnScanAll.disabled = true;
    btnScanAll.innerHTML = '<span>⏳</span> Scanning all sections...';
    progressBar.style.display = 'block';
    addLog('🔄 Starting full scan of all about sections...', 'info');

    for (let i = 0; i < sections.length; i++) {
      const section = sections[i];
      const progress = ((i + 1) / sections.length * 100).toFixed(0);
      progressFill.style.width = `${progress}%`;
      addLog(`📄 [${i + 1}/${sections.length}] Navigating to ${section}...`, 'info');

      try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

        // Navigate
        await chrome.tabs.sendMessage(tab.id, {
          action: 'navigateToSection',
          sectionKey: section,
        });

        // Wait for page load
        await waitForPageLoad(tab.id, 3000);

        // Re-inject content script (new page)
        try {
          await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            files: ['content.js'],
          });
        } catch (e) { /* May already be injected */ }

        await sleep(1500);

        // Scan
        try {
          const response = await chrome.tabs.sendMessage(tab.id, { action: 'scan' });
          if (response.success) {
            allResults[section] = response.data;
            const fieldCount = response.data.domData.labelValuePairs.length;
            const valueCount = response.data.domData.standaloneValues.length;
            const scriptKeys = Object.keys(response.data.scriptData.foundKeys).length;

            addLog(`  ✅ ${section}: ${fieldCount} fields, ${valueCount} values, ${scriptKeys} script keys`, 'success');
            statSections.textContent = Object.keys(allResults).length;
            statFields.textContent = Object.values(allResults).reduce((sum, r) =>
              sum + r.domData.labelValuePairs.length + Object.keys(r.scriptData.foundKeys).length, 0);
          }
        } catch (e) {
          addLog(`  ⚠️ ${section}: scan failed (page may not exist for this entity)`, 'warn');
        }
      } catch (err) {
        addLog(`  ⚠️ ${section}: ${err.message}`, 'warn');
      }
    }

    progressFill.style.width = '100%';
    statsPanel.style.display = 'grid';
    btnExport.style.display = 'block';
    btnDownload.style.display = 'block';
    btnHighlight.style.display = 'block';
    btnScanAll.disabled = false;
    btnScanAll.innerHTML = '<span>🔄</span> Scan ALL About Sections (Auto-Navigate)';

    addLog(`🏁 Full scan complete! ${Object.keys(allResults).length} sections scanned.`, 'success');

    // Generate summary
    generateSummary();
  }

  // =========================================================================
  // SUMMARY GENERATION
  // =========================================================================

  function generateSummary() {
    const summary = {
      _meta: {
        tool: 'FB About Page Data Mapper',
        scannedAt: new Date().toISOString(),
        sectionsScanned: Object.keys(allResults).length,
        entityType: Object.values(allResults)[0]?.entityType || 'unknown',
        entityName: Object.values(allResults)[0]?.entityName || '',
        entityId: Object.values(allResults)[0]?.entityId || '',
      },
      sections: {},
      allScriptKeys: {},
      dataMap: {},
    };

    // Merge all results
    for (const [section, data] of Object.entries(allResults)) {
      if (section === '_summary') continue;
      
      summary.sections[section] = {
        url: data.url,
        headers: data.domData.headers.map(h => h.text),
        fieldPairs: data.domData.labelValuePairs.map(p => ({
          label: p.label,
          values: p.values.map(v => ({
            text: v.text,
            isSubtitle: v.isSubtitle,
            tag: v.tag,
          })),
        })),
        standaloneValueCount: data.domData.standaloneValues.length,
        linksCount: data.domData.links.length,
        scriptKeysFound: Object.keys(data.scriptData.foundKeys),
        scriptSampleData: data.scriptData.sampleData,
      };

      // Merge script keys
      for (const [key, locations] of Object.entries(data.scriptData.foundKeys)) {
        if (!summary.allScriptKeys[key]) {
          summary.allScriptKeys[key] = { sections: [], sampleValue: data.scriptData.sampleData[key] };
        }
        summary.allScriptKeys[key].sections.push(section);
      }
    }

    // Build the data map: label -> where it was found + how to extract it
    for (const [section, data] of Object.entries(allResults)) {
      if (section === '_summary') continue;
      
      for (const pair of data.domData.labelValuePairs) {
        const key = pair.label.toLowerCase().replace(/\s+/g, '_');
        summary.dataMap[key] = {
          label: pair.label,
          section,
          extractionMethod: 'DOM label-value pair',
          headerSelector: pair.headerSelector,
          containerSelector: pair.containerSelector,
          values: pair.values.map(v => v.text),
          subtitles: pair.values.filter(v => v.isSubtitle).map(v => v.text),
        };
      }
    }

    allResults._summary = summary;
    addLog('📊 Summary generated — ready to export!', 'success');
  }

  // =========================================================================
  // UI HELPERS
  // =========================================================================

  function updateSectionList(data) {
    sectionList.style.display = 'block';
    sectionList.innerHTML = '';

    // Headers found
    data.domData.headers.forEach(h => {
      const item = document.createElement('div');
      item.className = 'section-item';
      item.innerHTML = `
        <span class="dot found"></span>
        <span class="name">${h.text}</span>
        <span class="count">${h.fontSize}px</span>
      `;
      sectionList.appendChild(item);
    });

    // Label-value pairs
    data.domData.labelValuePairs.forEach(p => {
      const item = document.createElement('div');
      item.className = 'section-item';
      const valPreview = p.values.map(v => v.text).join(', ').substring(0, 40);
      item.innerHTML = `
        <span class="dot found"></span>
        <span class="name">${p.label}: ${valPreview}</span>
        <span class="count">${p.values.length}</span>
      `;
      sectionList.appendChild(item);
    });
  }

  function setStatus(text, type = '') {
    statusBar.className = `status-bar ${type}`;
    pageUrl.textContent = text;
  }

  function addLog(text, type = '') {
    const entry = document.createElement('div');
    entry.className = `entry ${type}`;
    entry.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
    logEl.appendChild(entry);
    logEl.scrollTop = logEl.scrollHeight;
  }

  async function copyToClipboard() {
    try {
      if (!allResults._summary) generateSummary();
      const json = JSON.stringify(allResults, null, 2);
      
      // Creating a textarea is a more robust fallback for copying in extensions
      const el = document.createElement('textarea');
      el.value = json;
      document.body.appendChild(el);
      el.select();
      document.execCommand('copy');
      document.body.removeChild(el);

      addLog(`📋 Copied ${(json.length / 1024).toFixed(1)} KB to clipboard`, 'success');
      btnExport.innerHTML = '<span>✅</span> Copied!';
      setTimeout(() => btnExport.innerHTML = '<span>📋</span> Copy JSON Map to Clipboard', 2000);
    } catch (err) {
      addLog(`❌ Copy failed: ${err.message}`, 'error');
    }
  }

  function downloadJson() {
    try {
      if (!allResults._summary) generateSummary();
      const json = JSON.stringify(allResults, null, 2);
      const entityName = Object.values(allResults)[0]?.entityName?.replace(/[^a-zA-Z0-9]/g, '_') || 'fb';
      const filename = `fb-about-map_${entityName}_${Date.now()}.json`;
    
    // Create base64 data URI to avoid popup object URL lifecycle issues
    const dataUrl = 'data:application/json;base64,' + btoa(unescape(encodeURIComponent(json)));
    
    chrome.downloads.download({
      url: dataUrl,
      filename: filename,
      saveAs: true
    }, (downloadId) => {
      if (chrome.runtime.lastError) {
        addLog(`❌ Download error: ${chrome.runtime.lastError.message}`, 'error');
      } else {
        addLog(`💾 Downloaded JSON map`, 'success');
      }
    });
    } catch (err) {
      addLog(`❌ Download failed: ${err.message}`, 'error');
    }
  }

  async function toggleHighlight() {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (highlightActive) {
      await chrome.tabs.sendMessage(tab.id, { action: 'removeHighlights' });
      highlightActive = false;
      btnHighlight.innerHTML = '<span>🎨</span> Highlight Data Elements on Page';
    } else {
      const response = await chrome.tabs.sendMessage(tab.id, { action: 'highlight' });
      highlightActive = true;
      btnHighlight.innerHTML = `<span>🎨</span> Remove Highlights (${response.count} elements)`;
      addLog(`🎨 Highlighted ${response.count} data elements`, 'success');
    }
  }

  // =========================================================================
  // NAVIGATION HELPERS
  // =========================================================================

  function waitForPageLoad(tabId, timeout = 3000) {
    return new Promise((resolve) => {
      const listener = (id, changeInfo) => {
        if (id === tabId && changeInfo.status === 'complete') {
          chrome.tabs.onUpdated.removeListener(listener);
          resolve();
        }
      };
      chrome.tabs.onUpdated.addListener(listener);
      setTimeout(() => {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }, timeout);
    });
  }

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
})();
