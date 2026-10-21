'use strict';

const API_BASE = '/api';

const state = {
  instances: [],
  charts: {},
  lastRequest: {},
  lastResult: {},
  saveTab: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function pick(obj, names, fallback) {
  for (const n of names) {
    if (obj && obj[n] !== undefined && obj[n] !== null) return obj[n];
  }
  return fallback;
}

const fmtNum = (n, digits = 1) => {
  if (n === null || n === undefined || n === '') return '—';
  const v = Number(n);
  if (!isFinite(v)) return '—';
  return v.toLocaleString(undefined, { maximumFractionDigits: digits });
};

const fmtMoney = (n) => {
  const v = Number(n);
  if (!isFinite(v)) return '—';
  return '$' + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

function normInstance(inst) {
  return {
    id: pick(inst, ['id', 'instance_id'], ''),
    name: pick(inst, ['instance_type', 'name', 'instanceType'], 'unknown'),
    provider: pick(inst, ['provider', 'cloud'], 'unknown').toLowerCase(),
    family: pick(inst, ['family', 'instance_family'], ''),
    vcpu: Number(pick(inst, ['vcpu', 'vcpus', 'cpu'], 0)),
    ramGb: (inst.ram_mb !== undefined || inst.memory_mb !== undefined)
      ? Number(pick(inst, ['ram_mb', 'memory_mb'], 0)) / 1024
      : Number(pick(inst, ['ram_gb', 'memory_gb'], 0)),
    storageGb: (inst.storage_gb == null && inst.disk_gb == null) ? null : Number(pick(inst, ['storage_gb', 'disk_gb'], 0)),
    networkMbps: Number(pick(inst, ['network_gbps'], 0)) * 1000,
    hourlyCost: Number(pick(inst, ['hourly_cost_usd', 'hourly_cost', 'cost_hourly', 'price_hourly'], 0)),
    monthlyCost: Number(pick(inst, ['monthly_cost_usd', 'monthly_cost', 'cost_monthly', 'price_monthly'], 0)) || Number(pick(inst, ['hourly_cost_usd', 'hourly_cost'], 0)) * 730,
    priceSource: pick(inst, ['price_source', 'priceSource'], 'snapshot:instances.json'),
    priceFetchedAt: pick(inst, ['price_fetched_at', 'priceFetchedAt'], null),
  };
}

async function api(path, body, method) {
  const opts = (body === undefined || body === null)
    ? { method: method || 'GET', headers: { Accept: 'application/json' } }
    : { method: method || 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body) };
  let res;
  try {
    res = await fetch(API_BASE + path, opts);
  } catch (e) {
    throw new Error('Cannot reach API server at ' + API_BASE + ' — is the backend running?');
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { /* non-JSON */ }
  if (!res.ok) {
    const msg = pick(data, ['detail', 'message', 'error'], 'Request failed with status ' + res.status);
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
}

function setConnection(ok, text) {
  $('#connDot').classList.toggle('connected', !!ok);
  $('#connText').textContent = text;
}

function showError(id, msg) {
  const el = $('#' + id);
  el.textContent = msg;
  el.classList.add('visible');
}

function clearError(id) { $('#' + id).classList.remove('visible'); }

function showResults(id) { $('#' + id).classList.remove('hidden'); }

function validateNumber(id, { min = null, max = null, required = true } = {}) {
  const el = $('#' + id);
  const raw = el.value.trim();
  if (raw === '') {
    if (required) { el.classList.add('invalid'); return null; }
    el.classList.remove('invalid');
    return null;
  }
  const v = Number(raw);
  const bad = !isFinite(v) || (min !== null && v < min) || (max !== null && v > max);
  el.classList.toggle('invalid', bad);
  return bad ? null : v;
}

function destroyChart(id) {
  if (state.charts[id]) {
    state.charts[id].destroy();
    delete state.charts[id];
  }
}

const CHART_COLORS = ['#3b82f6', '#22c55e', '#f59e0b', '#8b5cf6', '#ef4444', '#06b6d4'];

const DEMO_UTILIZATION = 'CPU: 12, 18, 25, 30, 22\nMemory: 40, 55, 60, 48, 52\nNetwork: 5, 8, 12, 9, 7';

const PRESETS = {
  overprovisioned: {
    instanceId: 'aws-c5-2xlarge',
    data: 'CPU: 10, 15, 18, 12, 14, 16, 20, 11\nMemory: 30, 32, 35, 33, 31, 34, 35, 32\nNetwork: 5, 8, 12, 7, 9, 10, 6, 8',
    label: 'Over-provisioned Web App (Downsize candidate)',
  },
  memorydb: {
    instanceId: 'aws-r5-large',
    data: 'CPU: 22, 25, 28, 20, 24, 26, 23, 27\nMemory: 82, 85, 89, 87, 88, 91, 86, 90\nNetwork: 15, 20, 25, 18, 22, 19, 21, 24',
    label: 'Memory-bound DB (RAM bottleneck)',
  },
  spiky: {
    instanceId: 'gcp-n2-standard-4',
    data: 'CPU: 15, 88, 22, 92, 35, 85, 18, 90\nMemory: 55, 68, 60, 72, 58, 70, 56, 75\nNetwork: 10, 45, 15, 60, 20, 50, 12, 55',
    label: 'Spiky Batch Worker (Burst workload)',
  },
  idle: {
    instanceId: 'azure-D4s_v5',
    data: 'CPU: 1, 2, 3, 1, 2, 1, 2, 3\nMemory: 4, 5, 4, 5, 4, 5, 4, 4\nNetwork: 0.1, 0.2, 0.1, 0.3, 0.1, 0.2, 0.1, 0.1',
    label: 'Dormant Instance (Terminate or migrate)',
  },
};

function chartDefaults() {
  return {
    color: '#8b9bb8',
    borderColor: '#1e2a45',
    font: { family: getComputedStyle(document.body).fontFamily },
  };
}

function makeChart(id, config) {
  destroyChart(id);
  const canvas = document.getElementById(id);
  if (!canvas) return;
  state.charts[id] = new Chart(canvas, config);
}

function barChartConfig(labels, datasets, yTitle) {
  const d = chartDefaults();
  return {
    type: 'bar',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: d.color, boxWidth: 12 } },
        tooltip: {
          callbacks: {
            label: (ctx) => {
              const v = ctx.parsed.y;
              const ds = ctx.dataset.label || '';
              return ds + ': ' + (ds.toLowerCase().includes('cost') ? fmtMoney(v) : fmtNum(v));
            },
          },
        },
      },
      scales: {
        x: { ticks: { color: d.color }, grid: { color: 'rgba(30,42,69,0.5)' } },
        y: {
          beginAtZero: true,
          title: { display: !!yTitle, text: yTitle, color: d.color },
          ticks: { color: d.color },
          grid: { color: 'rgba(30,42,69,0.5)' },
        },
      },
    },
  };
}

function pieChartConfig(labels, values) {
  const total = values.reduce((s, v) => s + v, 0) || 1;
  return {
    type: 'doughnut',
    cutout: '55%',
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: CHART_COLORS,
        borderColor: '#292524',
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'right', labels: { color: '#8b9bb8', boxWidth: 12, padding: 14 } },
        tooltip: {
          callbacks: {
            label: (ctx) => ' ' + ctx.label + ': ' + fmtMoney(ctx.parsed) +
              ' (' + (ctx.parsed / total * 100).toFixed(1) + '%)',
          },
        },
      },
    },
  };
}

function renderInstanceOptions(select, { grouped = false } = {}) {
  select.innerHTML = '';
  if (!state.instances.length) {
    const o = document.createElement('option');
    o.textContent = 'No instances loaded';
    o.value = '';
    select.appendChild(o);
    return;
  }
  if (grouped) {
    const byProvider = {};
    for (const inst of state.instances) {
      (byProvider[inst.provider] = byProvider[inst.provider] || []).push(inst);
    }
    for (const provider of Object.keys(byProvider).sort()) {
      const og = document.createElement('optgroup');
      og.label = provider.toUpperCase();
      for (const inst of byProvider[provider]) {
        const o = document.createElement('option');
        o.value = inst.id;
        o.textContent = inst.name + ' (' + inst.vcpu + ' vCPU, ' + fmtNum(inst.ramGb, 0) + ' GB)';
        og.appendChild(o);
      }
      select.appendChild(og);
    }
  } else {
    for (const inst of state.instances) {
      const o = document.createElement('option');
      o.value = inst.id;
      o.textContent = inst.name + ' — ' + inst.provider.toUpperCase();
      select.appendChild(o);
    }
  }
}

function selectedIds(select) {
  return Array.from(select.selectedOptions).map((o) => o.value).filter(Boolean);
}

function providerDot(provider) {
  const cls = ['aws', 'azure', 'gcp'].includes(provider) ? provider : 'gray';
  return '<span class="provider-dot ' + cls + '"></span>';
}

function renderInstanceRow(r, { includeProvider = false, includeFamily = false } = {}) {
  const isLive = r.priceSource && r.priceSource.startsWith('live:');
  const liveBadge = isLive ? '<span class="live-badge" title="' + escapeHtml(r.priceSource) + '">Live</span>' : '';
  const familyBadge = includeFamily && r.family ? ' <span class="badge gray">' + escapeHtml(r.family) + '</span>' : '';
  const providerCell = includeProvider ? '<td>' + providerDot(r.provider) + ' ' + r.provider.toUpperCase() + '</td>' : '';
  return '<tr><td>' + r.name + familyBadge + liveBadge + '</td>' +
    providerCell +
    '<td>' + r.vcpu + '</td><td class="num">' + fmtNum(r.ramGb) + '</td>' +
    '<td class="num">' + fmtNum(r.storageGb, 0) + '</td><td class="num">' + fmtNum(r.networkMbps, 0) + '</td>' +
    '<td class="num">' + fmtMoney(r.hourlyCost) + '</td><td class="num">' + fmtMoney(r.monthlyCost) + '</td></tr>';
}

/* ---------- Loading / skeleton / feedback ---------- */

function setLoading(btn, loading) {
  if (!btn) return;
  btn.classList.toggle('loading', loading);
  btn.disabled = loading;
}

function skeletonHtml(cards = 4) {
  let html = '<div class="panel skeleton-panel"><div class="sk sk-title"></div><div class="sk-grid">';
  for (let i = 0; i < cards; i++) html += '<div class="sk sk-card"></div>';
  html += '</div><div class="sk sk-line"></div><div class="sk sk-line"></div><div class="sk sk-line short"></div></div>';
  return html;
}

function showSkeleton(resultsId, emptyId, cards) {
  hideEmpty(emptyId);
  const el = $('#' + resultsId);
  el.innerHTML = skeletonHtml(cards);
  showResults(resultsId);
}

function hideEmpty(emptyId) { $('#' + emptyId).classList.add('hidden'); }
function showEmpty(emptyId) { $('#' + emptyId).classList.remove('hidden'); }

function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('visible');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('visible'), 2200);
}

function downloadCsv(filename, rows) {
  const esc = (v) => {
    const s = String(v === null || v === undefined ? '' : v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const csv = rows.map((r) => r.map(esc).join(',')).join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function resultsActionsHtml(tab, extra) {
  return '<div class="results-actions">' +
    (extra || '') +
    '<button type="button" class="btn btn-secondary btn-sm save-scenario">Save Scenario</button>' +
    '</div>';
}

function wireResultsActions(containerId, tab, extraBtns) {
  const container = $('#' + containerId);
  const saveBtn = container.querySelector('.save-scenario');
  if (saveBtn) saveBtn.addEventListener('click', () => openSaveModal(tab));
  if (extraBtns) extraBtns(container);
}

/* ---------- Tab navigation ---------- */

function switchTab(name) {
  $$('.tab-btn').forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  $$('.tab-panel').forEach((p) => p.classList.toggle('active', p.id === 'tab-' + name));
  if (name === 'saved') loadSavedScenarios();
}

function initTabs() {
  $$('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
}

/* ---------- Tab 1: Calculator ---------- */

async function onCalculate(e) {
  e.preventDefault();
  clearError('calcError');

  const fields = [
    ['concurrentUsers', { min: 0 }],
    ['cpuPerUser', { min: 0, max: 100 }],
    ['memoryPerUser', { min: 0 }],
    ['storageGb', { min: 0 }],
    ['bandwidthMbps', { min: 0 }],
    ['growthFactor', { min: 0 }],
    ['headroomPercent', { min: 0, max: 100 }],
  ];
  const vals = {};
  let valid = true;
  for (const [id, opts] of fields) {
    const v = validateNumber(id, opts);
    if (v === null) { valid = false; continue; }
    vals[id] = v;
  }
  if (!valid) { showError('calcError', 'Please fix the highlighted fields.'); return; }

  const body = {
    workload_type: $('#workloadType').value,
    concurrent_users: vals.concurrentUsers,
    cpu_per_user: vals.cpuPerUser,
    memory_per_user_mb: vals.memoryPerUser,
    storage_gb: vals.storageGb,
    bandwidth_mbps: vals.bandwidthMbps,
    growth_factor: vals.growthFactor,
    headroom_percent: vals.headroomPercent,
  };

  const submitBtn = e.target.querySelector('button[type="submit"]');
  setLoading(submitBtn, true);
  showSkeleton('calcResults', 'calcEmpty', 4);

  let data;
  try {
    data = await api('/calculate', body);
  } catch (err) {
    setLoading(submitBtn, false);
    showError('calcError', err.message);
    return;
  }
  setLoading(submitBtn, false);

  state.lastRequest.calculator = Object.assign({ _tab: 'calculator' }, body);
  state.lastResult.calculator = data;

  const req = data.resources || data.required_resources || {};
  const recs = data.recommendations || {};
  const providers = ['aws', 'azure', 'gcp'].filter((p) => Array.isArray(recs[p]) && recs[p].length);
  const allProviders = providers.length ? providers : Object.keys(recs);

  let html = resultsActionsHtml('calculator') +
    '<div class="panel"><h2 class="panel-title">Required Resources</h2><div class="stat-cards">';
  const cards = [
    ['vCPUs', fmtNum(pick(req, ['vcpu', 'vcpus', 'required_vcpu'], 0)), ''],
    ['RAM', fmtNum(pick(req, ['ram_gb', 'memory_gb', 'required_ram_gb'], 0)), ' GB'],
    ['Storage', fmtNum(pick(req, ['storage_gb', 'required_storage_gb'], 0)), ' GB'],
    ['Bandwidth', fmtNum(pick(req, ['bandwidth_mbps', 'required_bandwidth_mbps'], 0)), ' Mbps'],
  ];
  for (const [label, value, unit] of cards) {
    html += '<div class="stat-card"><div class="stat-label">' + label + '</div>' +
      '<div class="stat-value">' + value + '<span class="stat-unit">' + unit + '</span></div></div>';
  }
  html += '</div></div>';

  for (const provider of allProviders) {
    const rows = recs[provider].map(normInstance);
    html += '<div class="panel provider-section"><h2 class="panel-title">Recommended — ' + provider.toUpperCase() + '</h2>';
    html += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">' +
      providerDot(provider) + '<span class="provider-heading" style="margin:0">' + rows.length + ' option(s)</span></div>';
    html += '<div class="table-scroll"><table class="data-table"><thead><tr><th>Instance</th><th>vCPU</th><th class="num">RAM (GB)</th>' +
      '<th class="num">Storage (GB)</th><th class="num">Network (Mbps)</th><th class="num">Hourly</th><th class="num">Monthly</th></tr></thead><tbody>';
    for (const r of rows) {
      html += renderInstanceRow(r, { includeFamily: true });
    }
    html += '</tbody></table></div></div>';
  }

  html += '<div class="panel"><h2 class="panel-title">Provider Cost Comparison</h2>' +
    '<div class="chart-scroll"><div class="chart-wrap"><canvas id="calcChart"></canvas></div></div></div>';

  $('#calcResults').innerHTML = html;
  showResults('calcResults');
  wireResultsActions('calcResults', 'calculator');

  const labels = allProviders.map((p) => p.toUpperCase());
  const tops = allProviders.map((p) => normInstance(recs[p][0]));
  const hourly = tops.map((r) => r.hourlyCost);
  const monthly = tops.map((r) => r.monthlyCost);
  makeChart('calcChart', barChartConfig(labels, [
    { label: 'Hourly Cost', data: hourly, backgroundColor: '#3b82f6' },
    { label: 'Monthly Cost', data: monthly, backgroundColor: '#22c55e' },
  ], 'Cost'));
}

/* ---------- Tab 2: Compare ---------- */

async function onCompare(e) {
  e.preventDefault();
  clearError('compareError');
  const ids = selectedIds($('#compareSelect'));
  if (ids.length < 2) { showError('compareError', 'Select at least 2 instances to compare.'); return; }

  const submitBtn = e.target.querySelector('button[type="submit"]');
  setLoading(submitBtn, true);
  showSkeleton('compareResults', 'compareEmpty', 4);

  let data;
  try {
    data = await api('/compare', { instance_ids: ids });
  } catch (err) {
    setLoading(submitBtn, false);
    showError('compareError', err.message);
    return;
  }
  setLoading(submitBtn, false);

  const rows = Array.isArray(data) ? data
    : (Array.isArray(data.instances) ? data.instances
    : (Array.isArray(data.results) ? data.results
    : (Array.isArray(data.comparison) ? data.comparison : [])));
  if (!rows.length) { showError('compareError', 'No comparison data returned.'); return; }
  const norm = rows.map(normInstance);

  state.lastRequest.compare = { _tab: 'compare', instance_ids: ids };
  state.lastResult.compare = data;

  const csvRows = [['Instance', 'Provider', 'vCPU', 'RAM (GB)', 'Storage (GB)', 'Network (Mbps)', 'Hourly', 'Monthly']];
  let html = resultsActionsHtml('compare',
    '<button type="button" class="btn btn-secondary btn-sm export-csv">Export CSV</button>') +
    '<div class="panel"><h2 class="panel-title">Comparison</h2><div class="table-scroll">' +
    '<table class="data-table"><thead><tr><th>Instance</th><th>Provider</th><th>vCPU</th><th class="num">RAM (GB)</th>' +
    '<th class="num">Storage (GB)</th><th class="num">Network (Mbps)</th><th class="num">Hourly</th><th class="num">Monthly</th></tr></thead><tbody>';
  for (const r of norm) {
    html += renderInstanceRow(r, { includeProvider: true });
    csvRows.push([r.name, r.provider.toUpperCase(), r.vcpu, fmtNum(r.ramGb), fmtNum(r.storageGb, 0), fmtNum(r.networkMbps, 0), r.hourlyCost.toFixed(4), r.monthlyCost.toFixed(2)]);
  }
  html += '</tbody></table></div></div>';
  html += '<div class="panel"><h2 class="panel-title">Cost Comparison by Provider</h2>' +
    '<div class="chart-scroll"><div class="chart-wrap"><canvas id="compareChart"></canvas></div></div></div>';

  $('#compareResults').innerHTML = html;
  showResults('compareResults');
  wireResultsActions('compareResults', 'compare', (container) => {
    container.querySelector('.export-csv').addEventListener('click', () => {
      downloadCsv('instance-comparison.csv', csvRows);
    });
  });

  const byProvider = {};
  for (const r of norm) (byProvider[r.provider] = byProvider[r.provider] || []).push(r);
  const providerNames = Object.keys(byProvider).sort();
  makeChart('compareChart', barChartConfig(providerNames.map((p) => p.toUpperCase()), [
    { label: 'Hourly Cost (total)', data: providerNames.map((p) => byProvider[p].reduce((s, r) => s + r.hourlyCost, 0)), backgroundColor: '#3b82f6' },
    { label: 'Monthly Cost (total)', data: providerNames.map((p) => byProvider[p].reduce((s, r) => s + r.monthlyCost, 0)), backgroundColor: '#f59e0b' },
  ], 'Cost'));
}

/* ---------- Tab 3: Cost Estimator ---------- */

async function onCost(e) {
  e.preventDefault();
  clearError('costError');
  const ids = selectedIds($('#costSelect'));
  if (!ids.length) { showError('costError', 'Select at least one instance.'); return; }
  const hours = validateNumber('hoursPerMonth', { min: 0, max: 744 });
  const storage = validateNumber('costStorageGb', { min: 0 });
  const egress = validateNumber('egressGb', { min: 0 });
  if (hours === null || storage === null || egress === null) {
    showError('costError', 'Please fix the highlighted fields.');
    return;
  }

  const submitBtn = e.target.querySelector('button[type="submit"]');
  setLoading(submitBtn, true);
  showSkeleton('costResults', 'costEmpty', 4);

  let data;
  try {
    data = await api('/cost', {
      instance_ids: ids,
      pricing_model: $('#pricingModel').value,
      hours_per_month: hours,
      storage_gb: storage,
      data_egress_gb: egress,
    });
  } catch (err) {
    setLoading(submitBtn, false);
    showError('costError', err.message);
    return;
  }
  setLoading(submitBtn, false);

  state.lastRequest.cost = {
    _tab: 'cost',
    instance_ids: ids,
    pricing_model: $('#pricingModel').value,
    hours_per_month: hours,
    storage_gb: storage,
    data_egress_gb: egress,
  };
  state.lastResult.cost = data;

  const breakdown = Array.isArray(data.instances) ? data.instances
    : (data.breakdown || data.costs || data.line_items || []);
  const totalMonthly = Number(pick(data, ['grand_monthly_total', 'total_monthly', 'monthly_total'], 0));
  const totalYearly = Number(pick(data, ['grand_yearly_total', 'total_yearly', 'yearly_total'], totalMonthly * 12));

  const csvRows = [['Instance', 'Compute (mo)', 'Storage (mo)', 'Egress (mo)', 'Monthly Total']];
  let html = resultsActionsHtml('cost',
    '<button type="button" class="btn btn-secondary btn-sm export-csv">Export CSV</button>') +
    '<div class="panel"><h2 class="panel-title">Cost Breakdown — ' +
    $('#pricingModel').value.replace(/_/g, ' ').toUpperCase() + '</h2>';
  html += '<div class="table-scroll"><table class="data-table"><thead><tr><th>Instance</th><th class="num">Compute (mo)</th>' +
    '<th class="num">Storage (mo)</th><th class="num">Egress (mo)</th><th class="num">Monthly Total</th></tr></thead><tbody>';
  const chartValues = [];
  for (const item of breakdown) {
    const inst = normInstance(item);
    const compute = Number(pick(item, ['compute_monthly'], 0));
    const storageC = Number(pick(item, ['storage_monthly'], 0));
    const egress = Number(pick(item, ['egress_monthly'], 0));
    const monthly = Number(pick(item, ['monthly_total', 'monthly_cost', 'cost'], compute + storageC + egress));
    const isLive = inst.priceSource && inst.priceSource.startsWith('live:');
    const liveBadge = isLive ? '<span class="live-badge" title="' + escapeHtml(inst.priceSource) + '">Live</span>' : '';
    html += '<tr><td>' + (pick(item, ['instance_type', 'name'], inst.name)) + liveBadge + '</td>' +
      '<td class="num">' + fmtMoney(compute) + '</td>' +
      '<td class="num">' + fmtMoney(storageC) + '</td>' +
      '<td class="num">' + fmtMoney(egress) + '</td>' +
      '<td class="num">' + fmtMoney(monthly) + '</td></tr>';
    chartValues.push(monthly);
    csvRows.push([pick(item, ['instance_type', 'name'], inst.name), compute.toFixed(2), storageC.toFixed(2), egress.toFixed(2), monthly.toFixed(2)]);
  }
  html += '</tbody></table></div>';
  html += '<div class="totals-row">' +
    '<div><div class="stat-label">Total Monthly</div><div class="stat-value">' + fmtMoney(totalMonthly) + '</div></div>' +
    '<div><div class="stat-label">Total Yearly</div><div class="stat-value">' + fmtMoney(totalYearly) + '</div></div>' +
    '</div></div>';

  const chartLabels = breakdown.map((item) => pick(item, ['instance_type', 'name'], ''));
  html += '<div class="panel"><h2 class="panel-title">Cost Distribution</h2>' +
    '<div class="chart-scroll"><div class="chart-wrap"><canvas id="costChart"></canvas></div></div></div>';

  $('#costResults').innerHTML = html;
  showResults('costResults');
  wireResultsActions('costResults', 'cost', (container) => {
    container.querySelector('.export-csv').addEventListener('click', () => {
      downloadCsv('cost-estimate.csv', csvRows);
    });
  });

  makeChart('costChart', pieChartConfig(chartLabels, chartValues.map((v) => Math.max(v, 0.0001))));
}

/* ---------- Tab 4: Fleet Planner ---------- */

function fleetRowHtml() {
  return '<div class="fleet-row">' +
    '<div class="form-field"><label>Instance Type</label><select class="fleet-instance" required></select></div>' +
    '<div class="form-field"><label>Count</label><input type="number" class="fleet-count" min="1" step="1" value="1" required></div>' +
    '<button type="button" class="btn btn-danger fleet-remove" title="Remove">&times;</button>' +
    '</div>';
}

function addFleetRow() {
  const div = document.createElement('div');
  div.innerHTML = fleetRowHtml();
  const row = div.firstElementChild;
  renderInstanceOptions(row.querySelector('.fleet-instance'));
  row.querySelector('.fleet-remove').addEventListener('click', () => {
    if ($('#fleetRows').children.length > 1) row.remove();
  });
  $('#fleetRows').appendChild(row);
}

async function onFleet(e) {
  e.preventDefault();
  clearError('fleetError');
  const rows = $$('#fleetRows .fleet-row');
  const vms = [];
  let valid = true;
  for (const row of rows) {
    const id = row.querySelector('.fleet-instance').value;
    const countRaw = row.querySelector('.fleet-count').value.trim();
    const count = Number(countRaw);
    if (!id || !isFinite(count) || count < 1) { valid = false; continue; }
    vms.push({ instance_id: id, count: Math.floor(count) });
  }
  if (!valid) { showError('fleetError', 'Every fleet row needs an instance and a count of at least 1.'); return; }

  const submitBtn = e.target.querySelector('button[type="submit"]');
  setLoading(submitBtn, true);
  showSkeleton('fleetResults', 'fleetEmpty', 5);

  let data;
  try {
    data = await api('/fleet', { vms });
  } catch (err) {
    setLoading(submitBtn, false);
    showError('fleetError', err.message);
    return;
  }
  setLoading(submitBtn, false);

  state.lastRequest.fleet = { _tab: 'fleet', vms };
  state.lastResult.fleet = data;

  const totals = data.totals || data.fleet_totals || data;
  const totalVcpu = Number(pick(totals, ['total_vcpu', 'vcpu'], 0));
  const totalRam = Number(pick(totals, ['total_memory_gb', 'total_ram_gb', 'ram_gb', 'memory_gb'], 0));
  const totalCost = Number(pick(totals, ['total_monthly_cost_usd', 'total_monthly_cost', 'monthly_cost'], 0));

  let html = resultsActionsHtml('fleet') +
    '<div class="panel"><h2 class="panel-title">Fleet Totals</h2><div class="stat-cards">';
  const cards = [
    ['Total vCPUs', fmtNum(totalVcpu, 0), ''],
    ['Total RAM', fmtNum(totalRam), ' GB'],
    ['Total Monthly Cost', fmtMoney(totalCost), ''],
  ];
  for (const [label, value, unit] of cards) {
    html += '<div class="stat-card"><div class="stat-label">' + label + '</div>' +
      '<div class="stat-value">' + value + (unit ? '<span class="stat-unit">' + unit + '</span>' : '') + '</div></div>';
  }
  html += '</div></div>';
  html += '<div class="panel"><h2 class="panel-title">Resource Allocation by Instance</h2>' +
    '<div class="chart-scroll"><div class="chart-wrap tall"><canvas id="fleetChart"></canvas></div></div></div>';

  $('#fleetResults').innerHTML = html;
  showResults('fleetResults');
  wireResultsActions('fleetResults', 'fleet');

  const breakdown = data.breakdown || data.vms || [];
  const labels = [];
  const vcpuData = [];
  const ramData = [];
  for (const item of breakdown) {
    const inst = normInstance(item);
    const count = Number(pick(item, ['count'], 1));
    labels.push(inst.name + (count > 1 ? ' ×' + count : ''));
    vcpuData.push(inst.vcpu * count);
    ramData.push(inst.ramGb * count);
  }
  if (!labels.length) {
    const byId = Object.fromEntries(state.instances.map((i) => [i.id, i]));
    for (const vm of vms) {
      const inst = byId[vm.instance_id];
      if (!inst) continue;
      labels.push(inst.name + (vm.count > 1 ? ' ×' + vm.count : ''));
      vcpuData.push(inst.vcpu * vm.count);
      ramData.push(inst.ramGb * vm.count);
    }
  }
  if (labels.length) {
    makeChart('fleetChart', barChartConfig(labels, [
      { label: 'vCPU', data: vcpuData, backgroundColor: '#3b82f6' },
      { label: 'RAM (GB)', data: ramData, backgroundColor: '#8b5cf6' },
    ], 'Resources'));
  }
}

/* ---------- Tab 5: Right-Size ---------- */

function parseUtilization(text) {
  const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
  const result = { cpu: [], memory: [], network: [] };
  const keys = ['cpu', 'memory', 'network'];
  lines.forEach((line, i) => {
    const keyMatch = line.match(/^\s*(CPU|Memory|Network)\s*:\s*(.*)$/i);
    const raw = keyMatch ? keyMatch[2] : line;
    const key = keyMatch ? keyMatch[1].toLowerCase() : keys[i];
    const nums = raw.split(',').map((s) => Number(s.trim())).filter((n) => isFinite(n));
    if (result[key] !== undefined) result[key] = nums;
  });
  return result;
}

function statusBadge(status) {
  const s = String(status || '').toLowerCase();
  let cls = 'gray';
  if (s === 'optimized') cls = 'green';
  else if (s === 'over-provisioned' || s === 'overprovisioned') cls = 'amber';
  else if (s === 'under-provisioned' || s === 'underprovisioned') cls = 'red';
  else if (s === 'idle') cls = 'blue';
  return '<span class="badge ' + cls + '">' + (status || 'Unknown') + '</span>';
}

async function onRightsize(e) {
  e.preventDefault();
  clearError('rightsizeError');
  const currentId = $('#currentInstance').value;
  const utilText = $('#utilizationData').value.trim();
  if (!currentId) { showError('rightsizeError', 'Choose a current instance.'); return; }
  if (!utilText) { showError('rightsizeError', 'Enter utilization samples.'); return; }

  const parsed = parseUtilization(utilText);
  if (!parsed.cpu.length) { showError('rightsizeError', 'Could not parse any CPU utilization samples.'); return; }

  const submitBtn = e.target.querySelector('button[type="submit"]');
  setLoading(submitBtn, true);
  showSkeleton('rightsizeResults', 'rightsizeEmpty', 3);

  let data;
  try {
    data = await api('/rightsize', {
      current_instance_id: currentId,
      cpu_utilization: parsed.cpu,
      memory_utilization: parsed.memory,
      network_utilization: parsed.network,
    });
  } catch (err) {
    setLoading(submitBtn, false);
    showError('rightsizeError', err.message);
    return;
  }
  setLoading(submitBtn, false);

  state.lastRequest.rightsize = {
    _tab: 'rightsize',
    current_instance_id: currentId,
    utilization_text: utilText,
  };
  state.lastResult.rightsize = data;

  const status = pick(data, ['classification', 'status', 'assessment'], 'unknown');
  const recId = pick(data, ['recommended_instance_id', 'recommended_instance', 'recommendation'], null);
  const savings = pick(data, ['estimated_savings', 'savings_monthly', 'monthly_savings'], null);

  const recName = recId && state.instances.find((i) => String(i.id) === String(recId))
    ? state.instances.find((i) => String(i.id) === String(recId)).name
    : (typeof recId === 'string' ? recId : '—');

  let html = resultsActionsHtml('rightsize') +
    '<div class="panel"><h2 class="panel-title">Assessment</h2>' +
    '<div class="rightsize-status">' +
    '<div><div class="stat-label">Status</div><div style="margin-top:4px">' + statusBadge(status) + '</div></div>' +
    '<div><div class="stat-label">Recommended Instance</div><div class="stat-value">' + recName + '</div></div>' +
    (savings !== null ? '<div><div class="stat-label">Estimated Savings</div><div class="stat-value">' +
      (typeof savings === 'number' ? fmtMoney(savings) : savings) + '/mo</div></div>' : '') +
    '</div>';
  if (data.recommendation && typeof data.recommendation === 'string' && data.recommendation !== recName) {
    html += '<p style="color:var(--text-dim);font-size:13px;margin-top:10px">' + data.recommendation + '</p>';
  }
  html += '</div>';

  html += '<div class="panel"><h2 class="panel-title">Utilization Samples (%)</h2>' +
    '<div class="chart-scroll"><div class="chart-wrap tall"><canvas id="rightsizeChart"></canvas></div></div></div>';

  $('#rightsizeResults').innerHTML = html;
  showResults('rightsizeResults');
  wireResultsActions('rightsizeResults', 'rightsize');

  const maxLen = Math.max(parsed.cpu.length, parsed.memory.length, parsed.network.length, 1);
  const xLabels = Array.from({ length: maxLen }, (_, i) => 'Sample ' + (i + 1));
  const d = chartDefaults();
  const lineSeries = (label, values, color) => ({
    label,
    data: values,
    borderColor: color,
    backgroundColor: color + '22',
    fill: true,
    tension: 0.3,
    spanGaps: true,
    pointRadius: 2,
    borderWidth: 2,
  });
  makeChart('rightsizeChart', {
    type: 'line',
    data: {
      labels: xLabels,
      datasets: [
        lineSeries('CPU', parsed.cpu, '#3b82f6'),
        lineSeries('Memory', parsed.memory, '#22c55e'),
        lineSeries('Network', parsed.network, '#f59e0b'),
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: d.color, boxWidth: 12 } },
        tooltip: {
          callbacks: {
            label: (ctx) => ' ' + ctx.dataset.label + ': ' + fmtNum(ctx.parsed.y) + '%',
          },
        },
      },
      scales: {
        x: { ticks: { color: d.color }, grid: { color: 'rgba(30,42,69,0.5)' } },
        y: {
          beginAtZero: true,
          max: 100,
          title: { display: true, text: 'Utilization (%)', color: d.color },
          ticks: { color: d.color },
          grid: { color: 'rgba(30,42,69,0.5)' },
        },
      },
    },
  });
}

/* ---------- Saved scenarios ---------- */

function defaultScenarioName(tab) {
  const names = {
    calculator: 'Workload calculation',
    compare: 'Instance comparison',
    cost: 'Cost estimate',
    fleet: 'Fleet plan',
    rightsize: 'Right-size analysis',
  };
  return (names[tab] || 'Scenario') + ' — ' + new Date().toLocaleString();
}

function openSaveModal(tab) {
  if (!state.lastRequest[tab]) {
    toast('Run a calculation first, then save it.');
    return;
  }
  state.saveTab = tab;
  $('#saveError').classList.remove('visible');
  $('#scenarioName').value = defaultScenarioName(tab);
  $('#saveModal').classList.add('visible');
  setTimeout(() => $('#scenarioName').focus(), 50);
}

function closeSaveModal() {
  $('#saveModal').classList.remove('visible');
  state.saveTab = null;
}

async function confirmSaveScenario() {
  const name = $('#scenarioName').value.trim();
  if (!name) {
    const err = $('#saveError');
    err.textContent = 'Please enter a name for this scenario.';
    err.classList.add('visible');
    return;
  }
  const tab = state.saveTab;
  const btn = $('#confirmSave');
  setLoading(btn, true);
  try {
    await api('/calculations', {
      name,
      request: state.lastRequest[tab],
      result: state.lastResult[tab],
    });
    closeSaveModal();
    toast('Scenario saved.');
    if ($('.tab-btn.active').dataset.tab === 'saved') loadSavedScenarios();
  } catch (err) {
    const el = $('#saveError');
    el.textContent = err.message;
    el.classList.add('visible');
  } finally {
    setLoading(btn, false);
  }
}

async function loadSavedScenarios() {
  const list = $('#savedList');
  let items;
  try {
    items = await api('/calculations');
  } catch (err) {
    list.innerHTML = '';
    showEmpty('savedEmpty');
    return;
  }
  if (!items.length) {
    list.innerHTML = '';
    showEmpty('savedEmpty');
    return;
  }
  hideEmpty('savedEmpty');
  list.innerHTML = items.map((s) => {
    const tab = (() => { try { return JSON.parse(s.request_json)._tab || 'calculator'; } catch (e) { return 'calculator'; } })();
    const date = new Date(s.created_at).toLocaleString();
    return '<div class="saved-item">' +
      '<div class="saved-item-info">' +
      '<div class="saved-item-name">' + escapeHtml(s.name) + '</div>' +
      '<div class="saved-item-meta">' + escapeHtml(capitalize(tab)) + ' · ' + escapeHtml(date) + '</div>' +
      '</div>' +
      '<div class="saved-item-actions">' +
      '<button type="button" class="btn btn-secondary btn-sm saved-load" data-id="' + s.id + '">Load</button>' +
      '<button type="button" class="btn btn-danger btn-sm saved-delete" data-id="' + s.id + '">Delete</button>' +
      '</div></div>';
  }).join('');

  list.querySelectorAll('.saved-load').forEach((b) =>
    b.addEventListener('click', () => loadScenario(b.dataset.id)));
  list.querySelectorAll('.saved-delete').forEach((b) =>
    b.addEventListener('click', () => deleteScenario(b.dataset.id, b)));
}

async function loadScenario(id) {
  let saved;
  try {
    saved = await api('/calculations/' + id);
  } catch (err) {
    toast('Could not load scenario: ' + err.message);
    return;
  }
  let req;
  try {
    req = JSON.parse(saved.request_json);
  } catch (e) {
    toast('Saved scenario data is corrupted.');
    return;
  }
  const tab = req._tab || 'calculator';
  switchTab(tab);
  populateForm(tab, req);
  const form = $('#' + tab + 'Form');
  if (form) form.requestSubmit();
  toast('Scenario "' + saved.name + '" loaded.');
}

async function deleteScenario(id, btn) {
  if (!confirm('Delete this saved scenario?')) return;
  setLoading(btn, true);
  try {
    await api('/calculations/' + id, null, 'DELETE');
    toast('Scenario deleted.');
    loadSavedScenarios();
  } catch (err) {
    setLoading(btn, false);
    toast('Delete failed: ' + err.message);
  }
}

function populateForm(tab, req) {
  if (tab === 'calculator') {
    $('#workloadType').value = req.workload_type || 'web';
    $('#concurrentUsers').value = req.concurrent_users;
    $('#cpuPerUser').value = req.cpu_per_user;
    $('#memoryPerUser').value = req.memory_per_user_mb;
    $('#storageGb').value = req.storage_gb;
    $('#bandwidthMbps').value = req.bandwidth_mbps;
    $('#growthFactor').value = req.growth_factor;
    $('#headroomPercent').value = req.headroom_percent;
  } else if (tab === 'compare') {
    setSelectValues($('#compareSelect'), req.instance_ids || []);
  } else if (tab === 'cost') {
    setSelectValues($('#costSelect'), req.instance_ids || []);
    $('#pricingModel').value = req.pricing_model || 'ondemand';
    $('#hoursPerMonth').value = req.hours_per_month;
    $('#costStorageGb').value = req.storage_gb;
    $('#egressGb').value = req.data_egress_gb;
  } else if (tab === 'fleet') {
    const container = $('#fleetRows');
    container.innerHTML = '';
    const vms = req.vms && req.vms.length ? req.vms : [{ instance_id: '', count: 1 }];
    for (const vm of vms) {
      addFleetRow();
      const row = container.lastElementChild;
      if (vm.instance_id) row.querySelector('.fleet-instance').value = vm.instance_id;
      row.querySelector('.fleet-count').value = vm.count;
    }
  } else if (tab === 'rightsize') {
    $('#currentInstance').value = req.current_instance_id || '';
    $('#utilizationData').value = req.utilization_text || '';
  }
}

function setSelectValues(select, values) {
  const wanted = new Set(values.map(String));
  Array.from(select.options).forEach((o) => { o.selected = wanted.has(String(o.value)); });
}

function capitalize(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

/* ---------- Keyboard shortcuts ---------- */

function initKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    const tag = (e.target.tagName || '').toLowerCase();
    const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable;

    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      const active = $('.tab-panel.active');
      if (active) {
        const form = active.querySelector('form');
        if (form) form.requestSubmit();
      }
      e.preventDefault();
      return;
    }
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;

    const idx = ['1', '2', '3', '4', '5'].indexOf(e.key);
    if (idx >= 0) {
      const btn = $$('.tab-btn')[idx];
      if (btn) btn.click();
    }
  });
}

/* ---------- Init ---------- */

/* ---------- Cloud Status Modal ---------- */

async function openCloudModal() {
  const modal = $('#cloudModal');
  if (!modal) return;
  modal.classList.add('visible');
  const container = $('#cloudStatusContent');
  container.innerHTML = '<div class="cloud-loading">Scanning local AWS, Azure, and GCP CLI environments...</div>';
  try {
    const data = await api('/cloud/status');
    renderCloudStatus(data);
    updateCloudStatusBadge(data);
  } catch (err) {
    container.innerHTML = '<div class="error-banner visible">' + escapeHtml(err.message) + '</div>';
  }
}

function closeCloudModal() {
  const modal = $('#cloudModal');
  if (modal) modal.classList.remove('visible');
}

function renderCloudStatus(data) {
  const container = $('#cloudStatusContent');
  if (!container) return;
  const providers = (data && data.providers) || {};
  let html = '';
  for (const [key, p] of Object.entries(providers)) {
    const name = key.toUpperCase();
    const isConn = p.authenticated;
    const isInst = p.installed;
    const badgeCls = isConn ? 'badge-green' : (isInst ? 'badge-amber' : 'badge-gray');
    const badgeText = isConn ? 'Connected' : (isInst ? 'Session Missing' : 'Not Installed');
    const cmd = key === 'aws' ? 'aws configure' : (key === 'azure' ? 'az login' : 'gcloud auth application-default login');

    html += '<div class="cloud-card">' +
      '<div class="cloud-card-header">' +
        '<span class="cloud-provider-name">' + name + '</span>' +
        '<span class="cloud-badge ' + badgeCls + '">' + badgeText + '</span>' +
      '</div>' +
      '<div class="cloud-detail">' + escapeHtml(p.message || '') + '</div>' +
      (p.identity ? '<div class="cloud-detail"><strong>Identity:</strong> ' + escapeHtml(p.identity) + '</div>' : '') +
      (p.project ? '<div class="cloud-detail"><strong>Active Project:</strong> ' + escapeHtml(p.project) + '</div>' : '') +
      (!isConn ? '<div class="cloud-command-box"><span>' + cmd + '</span>' +
        '<button type="button" class="cloud-copy-btn" data-copy="' + cmd + '">Copy</button></div>' : '') +
      '</div>';
  }
  container.innerHTML = html;
  container.querySelectorAll('.cloud-copy-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      navigator.clipboard.writeText(btn.dataset.copy);
      toast('Command copied to clipboard');
    });
  });
}

async function updateCloudStatusBadge(data) {
  try {
    const cs = data || await api('/cloud/status');
    const dot = $('#cloudDot');
    const text = $('#cloudStatusText');
    if (!cs || !cs.summary) return;
    const count = cs.summary.connected_count;
    if (count > 0) {
      if (dot) dot.className = 'pill-dot connected';
      if (text) text.textContent = 'Cloud: ' + count + ' Connected';
    } else {
      const anyInstalled = Object.values(cs.providers || {}).some((p) => p.installed);
      if (dot) dot.className = anyInstalled ? 'pill-dot partial' : 'pill-dot';
      if (text) text.textContent = anyInstalled ? 'Cloud: CLI Ready' : 'Cloud CLI';
    }
  } catch (e) {}
}

/* ---------- Live Pricing Modal & Sync ---------- */

async function reloadInstances(overlayLive = true) {
  const url = overlayLive ? '/instances?live=true' : '/instances';
  const instances = await api(url);
  state.instances = Array.isArray(instances) ? instances.map(normInstance)
    : (Array.isArray(instances && instances.instances) ? instances.instances.map(normInstance) : []);
  renderInstanceOptions($('#compareSelect'), { grouped: true });
  renderInstanceOptions($('#costSelect'), { grouped: true });
  renderInstanceOptions($('#currentInstance'));
  $$('#fleetRows .fleet-instance').forEach((sel) => renderInstanceOptions(sel));
}

async function openPricingModal() {
  const modal = $('#pricingModal');
  if (!modal) return;
  modal.classList.add('visible');
  const container = $('#pricingStatusContent');
  container.innerHTML = '<div class="cloud-loading">Checking pricing feed provenance...</div>';
  try {
    const data = await api('/pricing/status');
    renderPricingStatus(data);
    updatePricingBadge(data);
  } catch (err) {
    container.innerHTML = '<div class="error-banner visible">' + escapeHtml(err.message) + '</div>';
  }
}

function closePricingModal() {
  const modal = $('#pricingModal');
  if (modal) modal.classList.remove('visible');
}

function renderPricingStatus(data) {
  const container = $('#pricingStatusContent');
  if (!container) return;
  const providers = (data && data.providers) || {};
  let html = '';
  for (const [key, p] of Object.entries(providers)) {
    const name = key.toUpperCase();
    const isLive = p.mode === 'live';
    const isStale = p.mode === 'live-stale';
    const badgeCls = isLive ? 'badge-green' : (isStale ? 'badge-amber' : 'badge-gray');
    const badgeText = isLive ? 'Live Feed' : (isStale ? 'Live (Stale)' : 'Snapshot Only');
    const feedDesc = key === 'azure'
      ? 'Microsoft Azure Retail Prices API (zero-credentials)'
      : 'Vantage Instances Open Feed for ' + name + ' (zero-credentials)';

    html += '<div class="pricing-card">' +
      '<div class="pricing-card-header">' +
        '<span class="pricing-provider-name">' + name + '</span>' +
        '<span class="pricing-badge ' + badgeCls + '">' + badgeText + '</span>' +
      '</div>' +
      '<div class="pricing-detail"><strong>Feed:</strong> ' + feedDesc + '</div>' +
      '<div class="pricing-detail"><strong>Default Region:</strong> ' + p.region + '</div>' +
      (p.fetched_at ? '<div class="pricing-detail"><strong>Last Synced:</strong> ' + new Date(p.fetched_at).toLocaleString() + ' (' + p.count + ' cached)</div>' : '<div class="pricing-detail">Using bundled baseline catalog</div>') +
      '<div style="margin-top:6px;">' +
        '<button type="button" class="btn btn-secondary btn-sm sync-single-provider" data-provider="' + key + '">' +
          'Sync ' + name + ' Live' +
        '</button>' +
      '</div>' +
      '</div>';
  }
  container.innerHTML = html;
  container.querySelectorAll('.sync-single-provider').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const prov = btn.dataset.provider;
      setLoading(btn, true);
      try {
        await api('/pricing/refresh?provider=' + prov, {}, 'POST');
        toast('Synced live prices for ' + prov.toUpperCase());
        await reloadInstances(true);
        const updated = await api('/pricing/status');
        renderPricingStatus(updated);
        updatePricingBadge(updated);
      } catch (e) {
        toast('Sync failed: ' + e.message);
      } finally {
        setLoading(btn, false);
      }
    });
  });
}

async function refreshAllPrices() {
  const btn = $('#refreshAllPricesBtn');
  setLoading(btn, true);
  try {
    const res = await api('/pricing/refresh?provider=all', {}, 'POST');
    toast('Successfully synced live prices across providers!');
    await reloadInstances(true);
    const updated = await api('/pricing/status');
    renderPricingStatus(updated);
    updatePricingBadge(updated);
  } catch (e) {
    toast('Sync error: ' + e.message);
  } finally {
    setLoading(btn, false);
  }
}

async function updatePricingBadge(data) {
  try {
    const ps = data || await api('/pricing/status');
    const p = ps && ps.providers;
    if (!p) return;
    const liveProviders = Object.keys(p).filter((k) => p[k].mode === 'live');
    const dot = $('#pricingDot');
    const text = $('#syncPricingText');
    if (liveProviders.length === 3) {
      if (dot) dot.className = 'pill-dot live';
      if (text) text.textContent = 'Prices: 3/3 Live';
      setConnection(true, 'API Connected · AWS, Azure, GCP Live Prices');
    } else if (liveProviders.length > 0) {
      if (dot) dot.className = 'pill-dot partial';
      if (text) text.textContent = 'Prices: ' + liveProviders.length + '/3 Live';
      setConnection(true, 'API Connected · ' + liveProviders.join(', ').toUpperCase() + ' Live');
    } else {
      if (dot) dot.className = 'pill-dot';
      if (text) text.textContent = 'Sync Prices';
      setConnection(true, 'API Connected · Verified snapshots');
    }
  } catch (e) {}
}

/* ---------- Telemetry Bridge Helpers ---------- */

function showSamplesBadge(text) {
  const el = $('#samplesBadge');
  if (!el) return;
  el.textContent = text;
  el.classList.remove('hidden');
}

function hideSamplesBadge() {
  const el = $('#samplesBadge');
  if (el) el.classList.add('hidden');
}

function initTelemetryModes() {
  const modeButtons = $$('#telemetryModes .mode-btn');
  modeButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      modeButtons.forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      const mode = btn.dataset.mode;
      $('#subpanelCloud').classList.toggle('hidden', mode !== 'cloud');
      $('#subpanelFile').classList.toggle('hidden', mode !== 'file');
      $('#subpanelPresets').classList.toggle('hidden', mode !== 'presets');
    });
  });
}

function initTelemetryDropzone() {
  const dropzone = $('#telemetryDropzone');
  const fileInput = $('#telemetryFileInput');
  if (!dropzone || !fileInput) return;

  dropzone.addEventListener('click', () => fileInput.click());
  dropzone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropzone.classList.add('dragover');
  });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    if (e.dataTransfer.files.length) handleTelemetryFile(e.dataTransfer.files[0]);
  });
  fileInput.addEventListener('change', () => {
    if (fileInput.files.length) handleTelemetryFile(fileInput.files[0]);
  });
}

async function handleTelemetryFile(file) {
  clearError('rightsizeError');
  try {
    const text = await file.text();
    const parsed = await api('/telemetry/parse', { raw_data: text });
    const formatted = 'CPU: ' + parsed.cpu.join(', ') + '\nMemory: ' + parsed.memory.join(', ') + '\nNetwork: ' + parsed.network.join(', ');
    $('#utilizationData').value = formatted;
    showSamplesBadge('Imported ' + parsed.sample_count + ' samples (' + file.name + ')');
    toast('Imported ' + parsed.sample_count + ' samples from ' + file.name);
  } catch (err) {
    showError('rightsizeError', 'Failed to parse ' + file.name + ': ' + err.message);
  }
}

function initTelemetryPresets() {
  $$('.preset-card').forEach((card) => {
    card.addEventListener('click', () => {
      const key = card.dataset.preset;
      const preset = PRESETS[key];
      if (!preset) return;
      $('#utilizationData').value = preset.data;
      if (preset.instanceId) $('#currentInstance').value = preset.instanceId;
      showSamplesBadge('Loaded: ' + preset.label);
      toast('Loaded preset: ' + preset.label);
    });
  });
}

async function onFetchCloudTelemetry() {
  const btn = $('#fetchCloudTelemetryBtn');
  clearError('rightsizeError');
  const provider = $('#cloudProvider').value;
  const project = $('#cloudProject').value.trim();
  const instance = $('#cloudInstanceId').value.trim();
  const zone = $('#cloudZone').value.trim();
  const days = Number($('#cloudDays').value) || 14;

  if (provider === 'gcp' && (!project || !instance)) {
    showError('rightsizeError', 'Please enter both GCP Project ID and Instance ID/Name to query Cloud Monitoring.');
    return;
  }

  setLoading(btn, true);
  try {
    const res = await api('/telemetry/fetch', { provider, project, instance, zone, days });
    let text = 'CPU: ' + res.cpu.join(', ') + '\nMemory: ' + res.memory.join(', ');
    if (res.network_raw && res.network_raw.length) {
      text += '\nNetwork: ' + res.network_raw.map((n) => Math.round(n.bytes / 1000000)).join(', ');
    }
    $('#utilizationData').value = text;
    showSamplesBadge('Fetched ' + res.cpu.length + ' live samples (' + provider.toUpperCase() + ')');
    toast('Live metrics fetched from ' + provider.toUpperCase() + ' (' + res.cpu.length + ' hourly points)');
  } catch (err) {
    showError('rightsizeError', err.message);
  } finally {
    setLoading(btn, false);
  }
}

async function init() {
  initTabs();
  initKeyboardShortcuts();
  initTelemetryModes();
  initTelemetryDropzone();
  initTelemetryPresets();
  renderInstanceOptions($('#compareSelect'), { grouped: true });
  renderInstanceOptions($('#costSelect'), { grouped: true });
  renderInstanceOptions($('#currentInstance'));
  addFleetRow();

  $('#calcForm').addEventListener('submit', onCalculate);
  $('#compareForm').addEventListener('submit', onCompare);
  $('#costForm').addEventListener('submit', onCost);
  $('#fleetForm').addEventListener('submit', onFleet);
  $('#addFleetRow').addEventListener('click', addFleetRow);
  $('#rightsizeForm').addEventListener('submit', onRightsize);
  $('#loadDemoUtilization').addEventListener('click', () => {
    $('#utilizationData').value = DEMO_UTILIZATION;
    showSamplesBadge('Loaded default demo');
    toast('Demo samples loaded — these are sample values, not your telemetry.');
  });
  $('#clearUtilizationBtn').addEventListener('click', () => {
    $('#utilizationData').value = '';
    hideSamplesBadge();
    clearError('rightsizeError');
    toast('Utilization data cleared');
  });
  $('#fetchCloudTelemetryBtn').addEventListener('click', onFetchCloudTelemetry);

  // Modals & Header triggers
  $('#syncPricingBtn').addEventListener('click', openPricingModal);
  $('#closePricingModal').addEventListener('click', closePricingModal);
  $('#dismissPricingBtn').addEventListener('click', closePricingModal);
  $('#refreshAllPricesBtn').addEventListener('click', refreshAllPrices);
  $('#pricingModal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closePricingModal();
  });

  $('#cloudStatusBtn').addEventListener('click', openCloudModal);
  $('#closeCloudModal').addEventListener('click', closeCloudModal);
  $('#dismissCloudBtn').addEventListener('click', closeCloudModal);
  $('#refreshCloudBtn').addEventListener('click', openCloudModal);
  $('#cloudModal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeCloudModal();
  });

  $('#confirmSave').addEventListener('click', confirmSaveScenario);
  $('#cancelSave').addEventListener('click', closeSaveModal);
  $('#scenarioName').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); confirmSaveScenario(); }
    if (e.key === 'Escape') closeSaveModal();
  });
  $('#saveModal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeSaveModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeSaveModal();
      closeCloudModal();
      closePricingModal();
    }
  });

  try {
    await reloadInstances(true);
    setConnection(true, 'API Connected');
    await updatePricingBadge();
    await updateCloudStatusBadge();
  } catch (err) {
    setConnection(false, 'API Unreachable');
    showError('calcError', err.message);
    showError('compareError', err.message);
    showError('costError', err.message);
    showError('fleetError', err.message);
    showError('rightsizeError', err.message);
    return;
  }
}

document.addEventListener('DOMContentLoaded', init);
