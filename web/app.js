let symbolsData = {};
  let lotRulesData = { default_multiplier: 1.0, min_lot: 0.01, rules: {} };
  let profilesData = { active_profile: '', presets: {} };
  let accountsData = { active_master_id: '', active_slave_id: '', master_accounts: [], slave_accounts: [] };
  let rawViewStates = { symbols: false, 'lot-rules': false };

  function showToast(msg, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = (type === 'success' ? '✅ ' : '❌ ') + msg;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      toast.style.transition = 'all 0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }, 3000);
  }

  function switchTab(name) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    event.currentTarget.classList.add('active');
    document.getElementById(`pane-${name}`).classList.add('active');
  }

  async function fetchJSON(url, options = {}) {
    try {
      const res = await fetch(url, options);
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || `HTTP ${res.status}`);
      }
      return await res.json();
    } catch (e) {
      showToast(e.message, 'error');
      throw e;
    }
  }

  // === VPS LOAD & STATUS & LOGS ===
  function toggleVpsDrawer() {
    const drawer = document.getElementById('vps-drawer');
    const chevron = document.getElementById('vps-chevron');
    if (!drawer) return;
    const isOpen = drawer.classList.toggle('open');
    if (chevron) chevron.innerText = isOpen ? '▴' : '▾';
  }

  function vpsLevelClass(pct) {
    if (pct >= 85) return 'crit';
    if (pct >= 70) return 'warn';
    return '';
  }

  function renderVpsMetrics(vps) {
    if (!vps) return;
    const cpuPct = Number(vps.cpu_percent || 0);
    const ramPct = Number(vps.ram_percent || 0);
    const swapPct = Number(vps.swap_percent || 0);
    const diskPct = Number(vps.disk_percent || 0);

    // 1-line summary
    document.getElementById('vps-cpu-short').innerText = `${cpuPct.toFixed(0)}%`;
    document.getElementById('vps-ram-short').innerText = `${ramPct.toFixed(0)}%`;
    document.getElementById('vps-disk-short').innerText = `${diskPct.toFixed(0)}%`;
    document.getElementById('vps-uptime-short').innerText = vps.uptime_str || '--';

    const ramGbInline = document.getElementById('vps-ram-gb-inline');
    if (ramGbInline) {
      ramGbInline.innerText = vps.ram_total_gb > 0 ? `(${vps.ram_used_gb}/${vps.ram_total_gb}G)` : '';
    }

    document.getElementById('vps-cpu-dot').className = `vps-dot ${vpsLevelClass(cpuPct)}`.trim();
    document.getElementById('vps-ram-dot').className = `vps-dot ${vpsLevelClass(ramPct)}`.trim();
    document.getElementById('vps-disk-dot').className = `vps-dot ${vpsLevelClass(diskPct)}`.trim();

    // Detail drawer
    document.getElementById('vps-cpu-detail').innerText = `${cpuPct.toFixed(1)}%`;
    const cpuBar = document.getElementById('vps-cpu-bar');
    cpuBar.style.width = `${Math.min(100, cpuPct)}%`;
    cpuBar.className = `vps-bar-fill ${vpsLevelClass(cpuPct)}`.trim();
    document.getElementById('vps-load-avg').innerText = `Load: ${vps.load_1m ?? 0} / ${vps.load_5m ?? 0} (${vps.cpu_cores || 1}C) · Up ${vps.uptime_str || '--'}`;

    document.getElementById('vps-ram-detail').innerText = `${ramPct.toFixed(1)}%`;
    const ramBar = document.getElementById('vps-ram-bar');
    ramBar.style.width = `${Math.min(100, ramPct)}%`;
    ramBar.className = `vps-bar-fill ${vpsLevelClass(ramPct)}`.trim();
    document.getElementById('vps-ram-sub').innerText = vps.ram_total_gb > 0 ? `${vps.ram_used_gb} / ${vps.ram_total_gb} GB` : 'N/A';

    document.getElementById('vps-swap-detail').innerText = vps.swap_total_gb > 0 ? `${swapPct.toFixed(1)}%` : 'Off';
    const swapBar = document.getElementById('vps-swap-bar');
    swapBar.style.width = `${Math.min(100, swapPct)}%`;
    swapBar.className = `vps-bar-fill ${vpsLevelClass(swapPct)}`.trim();
    document.getElementById('vps-swap-sub').innerText = vps.swap_total_gb > 0 ? `${vps.swap_used_gb} / ${vps.swap_total_gb} GB` : '0 / 0 GB';

    document.getElementById('vps-disk-detail').innerText = `${diskPct.toFixed(1)}%`;
    const diskBar = document.getElementById('vps-disk-bar');
    diskBar.style.width = `${Math.min(100, diskPct)}%`;
    diskBar.className = `vps-bar-fill ${vpsLevelClass(diskPct)}`.trim();
    document.getElementById('vps-disk-sub').innerText = `${vps.disk_used_gb} / ${vps.disk_total_gb} GB`;
  }

  async function refreshStatus() {
    try {
      const data = await fetchJSON('/api/status');
      renderVpsMetrics(data.vps);
      
      const masterLbl = document.getElementById('master-label');
      const slaveLbl = document.getElementById('slave-label');
      if (masterLbl) masterLbl.innerText = data.active_master_name || 'Master';
      if (slaveLbl) slaveLbl.innerText = data.active_slave_name || 'Slave';

      // Master Badge
      const vDot = document.getElementById('vantage-dot');
      const vText = document.getElementById('vantage-text');
      const vBadge = document.getElementById('vantage-badge');
      if (data.vantage && data.vantage.connected) {
        vDot.className = 'dot online';
        vText.innerHTML = `Online <span class="badge-ip">(${escapeHtml(data.vantage.addr)})</span>`;
        vBadge.title = `Master EA: ${data.vantage.addr}`;
      } else {
        vDot.className = 'dot offline';
        vText.innerText = 'Offline';
        vBadge.title = 'Master EA: Offline';
      }

      // Slave Badge
      const fDot = document.getElementById('ftmo-dot');
      const fText = document.getElementById('ftmo-text');
      const fBadge = document.getElementById('ftmo-badge');
      if (data.ftmo && data.ftmo.connected) {
        fDot.className = 'dot online';
        fText.innerHTML = `Online <span class="badge-ip">(${escapeHtml(data.ftmo.addr)})</span>`;
        fBadge.title = `Slave EA: ${data.ftmo.addr}`;
      } else {
        fDot.className = 'dot offline';
        fText.innerText = 'Offline';
        fBadge.title = 'Slave EA: Offline';
      }

      // Render Logs
      const logBox = document.getElementById('log-box');
      if (data.recent_logs && data.recent_logs.length > 0) {
        const autoScroll = document.getElementById('auto-scroll-log').checked;
        logBox.innerHTML = data.recent_logs.map(log => `
          <div class="log-line">
            <span class="log-time">[${log.time}]</span>
            <span class="log-cat ${log.category}">${log.category}</span>
            <span>${escapeHtml(log.msg)}</span>
          </div>
        `).join('');
        if (autoScroll) {
          logBox.scrollTop = logBox.scrollHeight;
        }
      } else {
        logBox.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 2rem;">No logs recorded yet.</div>';
      }
    } catch (e) {
      console.error("Status poll failed:", e);
    }
  }

  // === SYMBOL MAPPING ===
  async function loadSymbols() {
    try {
      symbolsData = await fetchJSON('/api/symbols');
      renderSymbolTable();
    } catch (e) {
      console.error("Failed to load symbols:", e);
    }
  }

  function renderSymbolTable() {
    const tbody = document.getElementById('symbol-table-body');
    const filter = (document.getElementById('symbol-filter').value || '').trim().toLowerCase();
    const keys = Object.keys(symbolsData).sort();
    
    let html = '';
    let count = 0;
    for (const src of keys) {
      const tgt = symbolsData[src];
      if (filter && !src.toLowerCase().includes(filter) && !tgt.toLowerCase().includes(filter)) {
        continue;
      }
      count++;
      html += `
        <tr>
          <td>
            <div class="mobile-card-top">
              <div>
                <div class="mobile-only-label">Source Symbol (Vantage)</div>
                <span class="symbol-pill">${escapeHtml(src)}</span>
              </div>
              <button class="btn btn-danger btn-sm mobile-only-delete" onclick="deleteSymbol('${escapeJs(src)}')" title="Delete mapping">🗑️</button>
            </div>
          </td>
          <td class="arrow-icon">➔</td>
          <td>
            <div class="mobile-only-label">Target Symbol (FTMO)</div>
            <input type="text" value="${escapeHtml(tgt)}" onchange="updateSymbol('${escapeJs(src)}', this.value)" style="width: 100%; max-width: 250px;">
          </td>
          <td class="desktop-only-delete" style="text-align: right;">
            <button class="btn btn-danger btn-sm" onclick="deleteSymbol('${escapeJs(src)}')" title="Delete mapping">🗑️</button>
          </td>
        </tr>
      `;
    }
    if (count === 0) {
      html = `<tr><td colspan="4" style="text-align: center; color: var(--text-muted); padding: 2rem;">No symbol mappings found.</td></tr>`;
    }
    tbody.innerHTML = html;
    document.getElementById('symbol-count').innerText = `Total: ${Object.keys(symbolsData).length} mappings`;
    document.getElementById('symbols-json-text').value = JSON.stringify(symbolsData, null, 2);
  }

  function filterSymbols() {
    renderSymbolTable();
  }

  function updateSymbol(src, newTgt) {
    if (!newTgt.trim()) {
      showToast("Target symbol cannot be empty", "error");
      renderSymbolTable();
      return;
    }
    symbolsData[src] = newTgt.trim();
    document.getElementById('symbols-json-text').value = JSON.stringify(symbolsData, null, 2);
  }

  function deleteSymbol(src) {
    if (confirm(`Remove mapping for ${src}?`)) {
      delete symbolsData[src];
      renderSymbolTable();
    }
  }

  function openAddSymbolModal() {
    document.getElementById('modal-src-symbol').value = '';
    document.getElementById('modal-tgt-symbol').value = '';
    const modal = document.getElementById('add-symbol-modal');
    modal.style.display = 'flex';
  }

  function closeAddSymbolModal() {
    document.getElementById('add-symbol-modal').style.display = 'none';
  }

  function confirmAddSymbol() {
    const src = document.getElementById('modal-src-symbol').value.trim();
    const tgt = document.getElementById('modal-tgt-symbol').value.trim();
    if (!src || !tgt) {
      showToast("Please enter both source and target symbol", "error");
      return;
    }
    symbolsData[src] = tgt;
    closeAddSymbolModal();
    renderSymbolTable();
    showToast(`Added mapping: ${src} ➔ ${tgt}`);
  }

  async function saveSymbols() {
    if (rawViewStates.symbols) {
      try {
        symbolsData = JSON.parse(document.getElementById('symbols-json-text').value);
      } catch (e) {
        showToast("Invalid JSON syntax: " + e.message, "error");
        return;
      }
    }
    try {
      await fetchJSON('/api/symbols', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(symbolsData)
      });
      showToast("Symbol mappings saved and hot-reloaded into memory!");
      renderSymbolTable();
      refreshStatus();
    } catch (e) {
      // error toast handled in fetchJSON
    }
  }

  // === LOT RULES ===
  async function loadLotRules() {
    try {
      lotRulesData = await fetchJSON('/api/lot-rules');
      renderLotRules();
    } catch (e) {
      console.error("Failed to load lot rules:", e);
    }
  }

  function renderLotRules() {
    document.getElementById('global-multiplier').value = lotRulesData.default_multiplier ?? 1.0;
    document.getElementById('global-min-lot').value = lotRulesData.min_lot ?? 0.01;

    const container = document.getElementById('rules-container');
    const rules = lotRulesData.rules || {};
    const symbols = Object.keys(rules).sort();

    if (symbols.length === 0) {
      container.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 2rem;">No custom per-symbol rules configured. All symbols use global multiplier.</div>';
      return;
    }

    let html = '';
    for (const sym of symbols) {
      const r = rules[sym];
      let ruleType = 'multiplier';
      if ('fixed_lot' in r) ruleType = 'fixed_lot';
      else if ('lot_map' in r) ruleType = 'lot_map';

      html += `
        <div class="rule-card" id="rule-card-${escapeHtml(sym)}">
          <div class="rule-card-header">
            <span class="symbol-pill" style="font-size: 1rem;">${escapeHtml(sym)}</span>
            <div style="display: flex; gap: 0.5rem; align-items: center;">
              <select onchange="changeRuleType('${escapeJs(sym)}', this.value)">
                <option value="fixed_lot" ${ruleType === 'fixed_lot' ? 'selected' : ''}>Fixed Lot Size</option>
                <option value="multiplier" ${ruleType === 'multiplier' ? 'selected' : ''}>Multiplier</option>
                <option value="lot_map" ${ruleType === 'lot_map' ? 'selected' : ''}>Lot Map (Ladder)</option>
              </select>
              <button class="btn btn-danger btn-sm" onclick="deleteRuleCard('${escapeJs(sym)}')">🗑️ Remove</button>
            </div>
          </div>
          <div id="rule-body-${escapeHtml(sym)}">
            ${renderRuleBody(sym, r, ruleType)}
          </div>
        </div>
      `;
    }
    container.innerHTML = html;
    document.getElementById('lot-json-text').value = JSON.stringify(lotRulesData, null, 2);
  }

  function renderRuleBody(sym, r, ruleType) {
    if (ruleType === 'fixed_lot') {
      return `
        <div class="input-group" style="max-width: 250px;">
          <label class="input-label">Fixed Lot Size</label>
          <input type="number" step="0.01" min="0.01" inputmode="decimal" value="${r.fixed_lot ?? 1.0}" onchange="updateRuleVal('${escapeJs(sym)}', 'fixed_lot', parseFloat(this.value))">
        </div>
      `;
    } else if (ruleType === 'multiplier') {
      return `
        <div class="input-group" style="max-width: 250px;">
          <label class="input-label">Symbol Multiplier</label>
          <input type="number" step="0.01" min="0.01" inputmode="decimal" value="${r.multiplier ?? 1.0}" onchange="updateRuleVal('${escapeJs(sym)}', 'multiplier', parseFloat(this.value))">
        </div>
      `;
    } else if (ruleType === 'lot_map') {
      const map = r.lot_map || {};
      const keys = Object.keys(map).sort((a,b) => parseFloat(a) - parseFloat(b));
      let rows = keys.map(k => `
        <div class="ladder-row">
          <input type="number" step="0.01" inputmode="decimal" value="${k}" style="width: 100px;" onchange="updateLadderKey('${escapeJs(sym)}', '${escapeJs(k)}', this.value)">
          <span class="arrow-icon">➔</span>
          <input type="number" step="0.01" inputmode="decimal" value="${map[k]}" style="width: 100px;" onchange="updateLadderVal('${escapeJs(sym)}', '${escapeJs(k)}', parseFloat(this.value))">
          <button class="btn btn-danger btn-sm" onclick="deleteLadderTier('${escapeJs(sym)}', '${escapeJs(k)}')">✕</button>
        </div>
      `).join('');

      return `
        <div style="margin-bottom: 0.75rem;">
          <div style="font-size: 0.85rem; font-weight: 600; color: var(--text-muted); margin-bottom: 0.5rem;">Ladder Map (Source Lot ➔ Target Lot)</div>
          ${rows}
          <button class="btn btn-secondary btn-sm" onclick="addLadderTier('${escapeJs(sym)}')" style="margin-top: 0.5rem;">➕ Add Tier</button>
        </div>
        <div class="input-group" style="max-width: 250px; margin-top: 0.5rem;">
          <label class="input-label">Fallback Multiplier (when lot not matched)</label>
          <input type="number" step="0.01" min="0.01" inputmode="decimal" value="${r.default_multiplier ?? 1.0}" onchange="updateRuleVal('${escapeJs(sym)}', 'default_multiplier', parseFloat(this.value))">
        </div>
      `;
    }
  }

  function changeRuleType(sym, newType) {
    const rules = lotRulesData.rules;
    if (newType === 'fixed_lot') {
      rules[sym] = { fixed_lot: 1.0 };
    } else if (newType === 'multiplier') {
      rules[sym] = { multiplier: 1.0 };
    } else if (newType === 'lot_map') {
      rules[sym] = { lot_map: { "0.1": 0.2, "0.5": 1.0 }, default_multiplier: 1.0 };
    }
    renderLotRules();
  }

  function updateRuleVal(sym, key, val) {
    if (!lotRulesData.rules[sym]) lotRulesData.rules[sym] = {};
    lotRulesData.rules[sym][key] = val;
    document.getElementById('lot-json-text').value = JSON.stringify(lotRulesData, null, 2);
  }

  function updateLadderKey(sym, oldKey, newKey) {
    const map = lotRulesData.rules[sym].lot_map;
    const val = map[oldKey];
    delete map[oldKey];
    map[newKey] = val;
    renderLotRules();
  }

  function updateLadderVal(sym, key, val) {
    lotRulesData.rules[sym].lot_map[key] = val;
    document.getElementById('lot-json-text').value = JSON.stringify(lotRulesData, null, 2);
  }

  function addLadderTier(sym) {
    if (!lotRulesData.rules[sym].lot_map) lotRulesData.rules[sym].lot_map = {};
    lotRulesData.rules[sym].lot_map["0.1"] = 0.1;
    renderLotRules();
  }

  function deleteLadderTier(sym, key) {
    delete lotRulesData.rules[sym].lot_map[key];
    renderLotRules();
  }

  function addNewRuleCard() {
    const sym = prompt("Enter symbol for new rule (Vantage source e.g. XAUUSD247, XAUUSD.tv, or FTMO target e.g. US100.cash):");
    if (!sym || !sym.trim()) return;
    const cleanSym = sym.trim();
    if (lotRulesData.rules[cleanSym]) {
      showToast(`Rule for ${cleanSym} already exists!`, 'error');
      return;
    }
    lotRulesData.rules[cleanSym] = { multiplier: 1.0 };
    renderLotRules();
    showToast(`Added rule card for ${cleanSym}`);
  }

  function deleteRuleCard(sym) {
    if (confirm(`Delete rule for ${sym}?`)) {
      delete lotRulesData.rules[sym];
      renderLotRules();
    }
  }

  async function saveLotRules() {
    if (rawViewStates['lot-rules']) {
      try {
        lotRulesData = JSON.parse(document.getElementById('lot-json-text').value);
      } catch (e) {
        showToast("Invalid JSON syntax: " + e.message, "error");
        return;
      }
    } else {
      lotRulesData.default_multiplier = parseFloat(document.getElementById('global-multiplier').value) || 1.0;
      lotRulesData.min_lot = parseFloat(document.getElementById('global-min-lot').value) || 0.01;
    }
    try {
      await fetchJSON('/api/lot-rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(lotRulesData)
      });
      showToast("Lot sizing rules saved and hot-reloaded into memory!");
      renderLotRules();
      refreshStatus();
    } catch (e) {
      // handled in fetchJSON
    }
  }

  // === RAW TOGGLE ===
  function toggleRawView(tab) {
    rawViewStates[tab] = !rawViewStates[tab];
    const isRaw = rawViewStates[tab];
    if (tab === 'symbols') {
      document.getElementById('symbols-visual-view').style.display = isRaw ? 'none' : 'block';
      document.getElementById('symbols-raw-view').style.display = isRaw ? 'block' : 'none';
      document.getElementById('symbols-raw-toggle-text').innerText = isRaw ? 'Visual View' : 'Raw JSON';
      if (isRaw) {
        document.getElementById('symbols-json-text').value = JSON.stringify(symbolsData, null, 2);
      } else {
        try {
          symbolsData = JSON.parse(document.getElementById('symbols-json-text').value);
          renderSymbolTable();
        } catch (e) {
          showToast("Invalid JSON in raw editor", "error");
        }
      }
    } else if (tab === 'lot-rules') {
      document.getElementById('lot-visual-view').style.display = isRaw ? 'none' : 'block';
      document.getElementById('lot-raw-view').style.display = isRaw ? 'block' : 'none';
      document.getElementById('lot-raw-toggle-text').innerText = isRaw ? 'Visual View' : 'Raw JSON';
      if (isRaw) {
        lotRulesData.default_multiplier = parseFloat(document.getElementById('global-multiplier').value) || 1.0;
        lotRulesData.min_lot = parseFloat(document.getElementById('global-min-lot').value) || 0.01;
        document.getElementById('lot-json-text').value = JSON.stringify(lotRulesData, null, 2);
      } else {
        try {
          lotRulesData = JSON.parse(document.getElementById('lot-json-text').value);
          renderLotRules();
        } catch (e) {
          showToast("Invalid JSON in raw editor", "error");
        }
      }
    }
  }

  // === FORCE RELOAD ===
  async function forceReloadConfigs() {
    try {
      await fetchJSON('/api/reload', { method: 'POST' });
      showToast("Configs successfully reloaded from disk!");
      await loadSymbols();
      await loadLotRules();
      await refreshStatus();
    } catch (e) {
      // handled
    }
  }

  // === CHANGE PASSWORD ===
  async function changePassword(e) {
    e.preventDefault();
    const user = document.getElementById('sec-username').value.trim();
    const p1 = document.getElementById('sec-password').value;
    const p2 = document.getElementById('sec-password-confirm').value;
    if (p1 !== p2) {
      showToast("Passwords do not match!", "error");
      return;
    }
    try {
      await fetchJSON('/api/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: user, password: p1 })
      });
      showToast("Password updated! Please remember your new login credentials.");
      document.getElementById('sec-password').value = '';
      document.getElementById('sec-password-confirm').value = '';
    } catch (e) {
      // handled
    }
  }

  // === PROFILES (PRESETS) ===
  async function loadProfiles() {
    try {
      profilesData = await fetchJSON('/api/profiles');
      renderProfileSelect();
    } catch (e) {
      console.error("Failed to load profiles:", e);
    }
  }

  function renderProfileSelect() {
    const sel = document.getElementById('profile-select');
    const modalSel = document.getElementById('acc-modal-profile');
    const presets = profilesData.presets || {};
    const names = Object.keys(presets);
    const active = profilesData.active_profile || '';

    if (sel) {
      sel.innerHTML = names.map(n => `
        <option value="${escapeHtml(n)}" ${n === active ? 'selected' : ''}>
          ${escapeHtml(n)}${n === active ? ' (Active)' : ''}
        </option>
      `).join('');
    }
    if (modalSel) {
      modalSel.innerHTML = `<option value="">(None - Keep current)</option>` +
        names.map(n => `<option value="${escapeHtml(n)}">${escapeHtml(n)}</option>`).join('');
    }
  }

  async function activateSelectedProfile() {
    const sel = document.getElementById('profile-select');
    const name = sel ? sel.value : '';
    if (!name) return;
    try {
      const res = await fetchJSON('/api/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'activate', name })
      });
      profilesData = res.profiles || profilesData;
      renderProfileSelect();
      await loadSymbols();
      await loadLotRules();
      await refreshStatus();
      showToast(`Activated profile: ${name}`);
    } catch (e) {
      // handled
    }
  }

  async function saveCurrentAsProfile() {
    const sel = document.getElementById('profile-select');
    const defaultName = sel && sel.value ? sel.value : 'Vantage -> FTMO';
    const name = prompt("Save current Symbol Mapping & Lot Rules into Profile Preset:", defaultName);
    if (!name || !name.trim()) return;
    const cleanName = name.trim();

    if (!rawViewStates['lot-rules']) {
      lotRulesData.default_multiplier = parseFloat(document.getElementById('global-multiplier').value) || 1.0;
      lotRulesData.min_lot = parseFloat(document.getElementById('global-min-lot').value) || 0.01;
    }

    try {
      const res = await fetchJSON('/api/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'save',
          name: cleanName,
          symbol_map: symbolsData,
          lot_rules: lotRulesData
        })
      });
      profilesData = res.profiles || profilesData;
      renderProfileSelect();
      showToast(`Saved current settings to profile: ${cleanName}`);
    } catch (e) {
      // handled
    }
  }

  async function deleteSelectedProfile() {
    const sel = document.getElementById('profile-select');
    const name = sel ? sel.value : '';
    if (!name) return;
    if (!confirm(`Delete profile preset '${name}'?`)) return;
    try {
      const res = await fetchJSON('/api/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delete', name })
      });
      profilesData = res.profiles || profilesData;
      renderProfileSelect();
      showToast(`Deleted profile: ${name}`);
    } catch (e) {
      // handled
    }
  }

  // === ACCOUNTS (MASTER & SLAVE SWITCHER) ===
  async function loadAccounts() {
    try {
      accountsData = await fetchJSON('/api/accounts');
      renderAccounts();
    } catch (e) {
      console.error("Failed to load accounts:", e);
    }
  }

  function renderAccountRoleList(role, list, activeId, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!list || list.length === 0) {
      container.innerHTML = `<div style="color: var(--text-muted); text-align: center; padding: 1.5rem;">No ${role} accounts saved yet. Click "➕ Add" above to create one.</div>`;
      return;
    }

    container.innerHTML = list.map(acc => {
      const isActive = acc.id === activeId;
      return `
        <div class="rule-card" style="border-color: ${isActive ? 'var(--success)' : 'var(--card-border)'};">
          <div class="rule-card-header">
            <div style="display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;">
              <span class="symbol-pill" style="font-size: 0.95rem;">${escapeHtml(acc.name)}</span>
              ${acc.broker ? `<span class="badge" style="font-size: 0.72rem; padding: 0.15rem 0.5rem;">${escapeHtml(acc.broker)}</span>` : ''}
              ${isActive ? `<span class="badge" style="background: rgba(16, 185, 129, 0.15); color: var(--success); border-color: var(--success); font-size: 0.72rem; padding: 0.15rem 0.5rem;">🟢 ACTIVE</span>` : ''}
            </div>
            <div style="display: flex; gap: 0.4rem;">
              <button class="btn btn-primary btn-sm" onclick="activateAccount('${role}', '${escapeJs(acc.id)}', '${escapeJs(acc.name)}')">⚡ Switch</button>
              <button class="btn btn-secondary btn-sm" onclick="openAccountModal('${role}', '${escapeJs(acc.id)}')" title="Edit">✏️</button>
              <button class="btn btn-danger btn-sm" onclick="deleteAccount('${role}', '${escapeJs(acc.id)}', '${escapeJs(acc.name)}')" title="Delete">🗑️</button>
            </div>
          </div>
          <div style="font-size: 0.82rem; color: var(--text-muted); display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 0.4rem;">
            <div><strong>Server:</strong> <span style="font-family: var(--font-mono); color: var(--text-main);">${escapeHtml(acc.server)}</span></div>
            <div><strong>Login:</strong> <span style="font-family: var(--font-mono); color: var(--text-main);">${escapeHtml(acc.login)}</span></div>
            <div><strong>Symbol:</strong> <span style="font-family: var(--font-mono);">${escapeHtml(acc.startup_symbol || 'XAUUSD')}</span></div>
            ${acc.linked_profile ? `<div><strong>Profile:</strong> <span style="color: var(--accent);">${escapeHtml(acc.linked_profile)}</span></div>` : ''}
          </div>
        </div>
      `;
    }).join('');
  }

  function renderAccounts() {
    renderAccountRoleList('master', accountsData.master_accounts || [], accountsData.active_master_id, 'master-accounts-list');
    renderAccountRoleList('slave', accountsData.slave_accounts || [], accountsData.active_slave_id, 'slave-accounts-list');
  }

  function openAccountModal(role, accId = '') {
    renderProfileSelect();
    document.getElementById('acc-modal-role').value = role;
    document.getElementById('acc-modal-id').value = accId;
    const list = role === 'master' ? (accountsData.master_accounts || []) : (accountsData.slave_accounts || []);
    const existing = accId ? list.find(a => a.id === accId) : null;

    document.getElementById('account-modal-title').innerText = existing
      ? `Edit ${role === 'master' ? 'Master' : 'Slave'} Account`
      : `Add ${role === 'master' ? 'Master (Sender)' : 'Slave (Receiver)'} Account`;

    document.getElementById('acc-modal-name').value = existing ? existing.name : '';
    document.getElementById('acc-modal-broker').value = existing ? (existing.broker || '') : (role === 'master' ? 'Vantage' : 'FTMO');
    document.getElementById('acc-modal-server').value = existing ? existing.server : '';
    document.getElementById('acc-modal-login').value = existing ? existing.login : '';
    document.getElementById('acc-modal-password').value = '';
    document.getElementById('acc-modal-symbol').value = existing ? (existing.startup_symbol || 'XAUUSD') : 'XAUUSD';
    document.getElementById('acc-modal-profile').value = existing ? (existing.linked_profile || '') : '';

    document.getElementById('account-modal').style.display = 'flex';
  }

  function closeAccountModal() {
    document.getElementById('account-modal').style.display = 'none';
  }

  async function saveAccountModal() {
    const role = document.getElementById('acc-modal-role').value;
    const account = {
      id: document.getElementById('acc-modal-id').value,
      name: document.getElementById('acc-modal-name').value.trim(),
      broker: document.getElementById('acc-modal-broker').value.trim(),
      server: document.getElementById('acc-modal-server').value.trim(),
      login: document.getElementById('acc-modal-login').value.trim(),
      password: document.getElementById('acc-modal-password').value,
      startup_symbol: document.getElementById('acc-modal-symbol').value.trim() || 'XAUUSD',
      linked_profile: document.getElementById('acc-modal-profile').value
    };

    if (!account.name || !account.server || !account.login) {
      showToast("Please fill in Display Name, MT5 Server, and Login", "error");
      return;
    }

    try {
      const res = await fetchJSON('/api/accounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'save', role, account })
      });
      accountsData = res.accounts || accountsData;
      closeAccountModal();
      renderAccounts();
      showToast(`Saved ${role} account: ${account.name}`);
    } catch (e) {
      // handled
    }
  }

  async function deleteAccount(role, id, name) {
    if (!confirm(`Delete ${role} account '${name}'?`)) return;
    try {
      const res = await fetchJSON('/api/accounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'delete', role, id })
      });
      accountsData = res.accounts || accountsData;
      renderAccounts();
      showToast(`Deleted account: ${name}`);
    } catch (e) {
      // handled
    }
  }

  async function activateAccount(role, id, name) {
    const containerName = role === 'master' ? 'mt5_vantage' : 'mt5_ftmo';
    if (!confirm(`Switch active ${role.toUpperCase()} account to '${name}'?\nThis will update mt5-server.ini and restart container '${containerName}'.`)) {
      return;
    }
    try {
      showToast(`Switching ${role} account to ${name}...`);
      const res = await fetchJSON('/api/accounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'activate', role, id, restart_container: true })
      });
      accountsData = res.accounts || accountsData;
      if (res.profiles) {
        profilesData = res.profiles;
        renderProfileSelect();
      }
      renderAccounts();
      await loadSymbols();
      await loadLotRules();
      await refreshStatus();
      showToast(`Activated '${name}' (${res.docker_msg})`);
    } catch (e) {
      // handled
    }
  }

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, s => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[s]));
  }
  function escapeJs(str) {
    return String(str).replace(/['"\\]/g, '\\$&');
  }

  // === INITIALIZE ===
  window.addEventListener('DOMContentLoaded', () => {
    loadProfiles();
    loadAccounts();
    loadSymbols();
    loadLotRules();
    refreshStatus();
    setInterval(() => {
      if (!document.hidden) {
        refreshStatus();
      }
    }, 2500);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) {
        refreshStatus();
      }
    });
  });
