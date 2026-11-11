// Dashboard front end: case list, expected-vs-ours comparison, step trace. No build step, no framework.
import { createViewer, VIEWS, COLORS } from '/static/viewer.js';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const VIEW_NAMES = Object.keys(VIEWS);
const VIEW_LABEL = (name) => name.replace(/^view_\d+_/, '').replaceAll('_', ' ');

const S = {
  datasets: [], runs: [], cases: {}, now: 0,
  dataset: null, caseName: null, runId: null,  // runId '' = committed outputs / no run
  view: 'view_6_iso_front_right_top', follow: true,
};
const open = new Set();        // expanded steps: "<run>/<case>/<step id>"
const traces = new Map();      // "<run>/<case>" -> { events, loading }
const textCache = new Map();   // artifact url -> text
const openText = new Set();    // artifact urls shown inline
let live = { id: 0, viewers: [] };

// -- helpers ------------------------------------------------------------------------------------

const htmlCache = new WeakMap();
function setHTML(element, html) {  // skip identical updates: keeps focus, open menus and scroll intact
  if (!element || htmlCache.get(element) === html) return false;
  htmlCache.set(element, html);
  element.innerHTML = html;
  return true;
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok || data.error) throw new Error(data.error || response.statusText);
  return data;
}

function toast(message, bad = false) {
  const node = document.createElement('div');
  node.className = 'toast' + (bad ? ' bad' : '');
  node.textContent = message;
  document.body.append(node);
  setTimeout(() => node.remove(), 5000);
}

const num = (value, digits = 2) => (value == null || !isFinite(value) ? '–'
  : Number(value).toLocaleString('en-US', { maximumFractionDigits: digits }));
function duration(seconds) {
  if (seconds == null || !isFinite(seconds)) return '–';
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)} m ${Math.round(seconds % 60)} s`;
}
function size(bytes) {
  if (bytes == null) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} kB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}
const clock = (ts) => (ts ? new Date(ts * 1000).toLocaleString('en-GB', {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '');

const dataset = () => S.datasets.find((d) => d.id === S.dataset);
const cases = () => S.cases[S.dataset] || [];
const currentCase = () => cases().find((c) => c.name === S.caseName);
const currentRun = () => S.runs.find((r) => r.id === S.runId);
const datasetRuns = () => S.runs.filter((r) => !r.dataset || r.dataset === S.dataset);
const runCase = (run, name) => run && run.cases.find((c) => c.name === name);

function envelope(c) {
  const b = c.facts && c.facts.bounding_box_mm;
  return b ? `${num(b.x, 1)} × ${num(b.y, 1)} × ${num(b.z, 1)} mm` : '';
}

// Our output for the current case: from the selected run, else the committed file
function oursFor(c) {
  const run = currentRun();
  if (run) {
    const rc = runCase(run, c.name);
    if (rc && rc.output) {
      const url = `/files/runs/${run.id}/${c.name}/${rc.output}`;
      return { url, key: url, label: `run ${run.id}` };
    }
    return { missing: rc ? (rc.status === 'running' ? 'running' : 'no-output') : 'not-run' };
  }
  if (c.output) return { url: c.output.url, key: `${c.output.url}?${c.output.mtime}`, label: 'artifacts/outputs' };
  return { missing: dataset() && dataset().committed ? 'no-committed' : 'no-run' };
}

// -- STEP loading -------------------------------------------------------------------------------

let worker = null, jobs = new Map(), jobId = 0;
const stepCache = new Map();
function loadStep(url, key = url) {
  if (stepCache.has(key)) return stepCache.get(key);
  const promise = (async () => {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) throw new Error(`could not load file (${response.status})`);
    const buffer = await response.arrayBuffer();
    const head = new TextDecoder().decode(buffer.slice(0, 64)).trimStart();
    if (!head.startsWith('ISO-10303-21')) throw new Error('not a STEP file (no ISO-10303-21 header)');
    if (!worker) {
      worker = new Worker('/static/step-worker.js');
      worker.onmessage = ({ data }) => {
        const job = jobs.get(data.id);
        jobs.delete(data.id);
        if (job) data.ok ? job.resolve(data) : job.reject(new Error(data.error));
      };
    }
    return new Promise((resolve, reject) => {
      const id = ++jobId;
      jobs.set(id, { resolve, reject });
      worker.postMessage({ id, buffer }, [buffer]);
    });
  })();
  stepCache.set(key, promise);
  promise.catch(() => {});
  if (stepCache.size > 8) stepCache.delete(stepCache.keys().next().value);
  return promise;
}

// -- routing ------------------------------------------------------------------------------------

function writeHash() {
  const hash = `#/${S.dataset}/${S.caseName || ''}${S.runId ? `?run=${S.runId}` : ''}`;
  if (location.hash !== hash) history.replaceState(null, '', hash);
}

function readHash() {
  const [path, query] = location.hash.replace(/^#\/?/, '').split('?');
  const [ds, name] = path.split('/');
  if (ds) S.dataset = ds;
  if (name) S.caseName = name;
  const run = new URLSearchParams(query || '').get('run');
  if (run) S.runId = run;
}

async function ensureCases(force = false) {
  await Promise.all(S.datasets.filter((d) => force || !S.cases[d.id]).map(async (d) => {
    S.cases[d.id] = (await api(`/api/cases?dataset=${d.id}`)).cases;
  }));
}

function normalise() {
  if (!S.datasets.some((d) => d.id === S.dataset)) S.dataset = S.datasets[0] && S.datasets[0].id;
  const runs = datasetRuns();
  if (S.runId == null || (S.runId && !runs.some((r) => r.id === S.runId))) S.runId = runs[0] ? runs[0].id : '';
  if (cases().length && !currentCase()) {
    const run = currentRun();
    const first = run && run.cases[0] && cases().find((c) => c.name === run.cases[0].name);
    S.caseName = (first || cases()[0]).name;
  }
}

// -- top bar ------------------------------------------------------------------------------------

function runLabel(run) {
  const done = run.cases.filter((c) => c.status !== 'running').length;
  return `${clock(run.started_at)} · ${done} case${done === 1 ? '' : 's'} · ${run.status}`;
}

function caseLabel(c) {
  const generic = !c.title || /^mechanical part$/i.test(c.title) || c.title === c.name;
  return `#${c.number} · ${generic ? envelope(c) || c.name : c.title}${c.tier ? ` · ${c.tier}` : ''}`;
}

function renderTop() {
  const ds = dataset(), run = currentRun(), runs = datasetRuns();
  const inRun = new Set(run ? run.cases.map((c) => c.name) : []);
  setHTML($('#bar'), `
    <select id="case-select" aria-label="Test case">
      ${S.datasets.map((d) => `<optgroup label="${esc(d.label)} (${d.count})">
        ${(S.cases[d.id] || []).map((c) => `<option value="${d.id}/${c.name}" ${d.id === S.dataset && c.name === S.caseName ? 'selected' : ''}>
          ${esc(caseLabel(c))}${d.id === S.dataset && inRun.has(c.name) ? ' ●' : ''}</option>`).join('')}</optgroup>`).join('')}
    </select>
    <button class="btn" data-move="-1" title="Previous case (k)" aria-label="Previous case">‹</button>
    <button class="btn" data-move="1" title="Next case (j)" aria-label="Next case">›</button>
    <label class="field">Run
      <select id="run-select">
        <option value="">${ds && ds.committed ? 'Committed outputs' : 'No run'}</option>
        ${runs.map((r) => `<option value="${esc(r.id)}" ${r.id === S.runId ? 'selected' : ''}>${esc(runLabel(r))}</option>`).join('')}
      </select>
    </label>
    <span class="spacer"></span>
    ${run && run.status === 'running' && run.launched_by === 'ui' ? '<button class="btn" data-action="stop">Stop</button>' : ''}
    <button class="btn primary" data-action="run" title="Run scripts/main.py on this case with tracing">Run this case</button>`);
}

// -- main ---------------------------------------------------------------------------------------

function caseStatusHTML(c) {
  const run = currentRun(), rc = runCase(run, c.name);
  if (!run || !rc) return '';
  const end = rc.ended_at || (rc.status === 'running' ? S.now : rc.last_ts);
  return `<span class="st st-${rc.status}">${esc(rc.status)}</span>
    <span class="muted">${rc.steps} step${rc.steps === 1 ? '' : 's'}${rc.errors ? `, ${rc.errors} failed` : ''} · ${duration(end - rc.started_at)}</span>`;
}

function renderMain(force = false) {
  const main = $('#main'), c = currentCase();
  if (!c) {
    setHTML(main, `<p class="empty">${S.datasets.length ? 'Loading cases…' : 'No datasets found.'}</p>`);
    return;
  }
  const ours = oursFor(c);
  const key = [S.dataset, c.name, S.runId, ours.key || ours.missing].join('|');
  if (force || main.dataset.key !== key) {
    main.dataset.key = key;
    live.viewers.forEach((v) => v.dispose());
    live = { id: live.id + 1, viewers: [] };
    htmlCache.delete(main);
    renderCompare(c, ours);
    main.scrollTop = 0;
  }
  renderSteps(c);
  setHTML($('#case-status'), caseStatusHTML(c));
}

// -- compare ------------------------------------------------------------------------------------

const MISSING = {
  running: ['Still running', 'The output appears here as soon as this case finishes.'],
  'no-output': ['No output file', 'This case ran but recorded no .stp. A missing file scores 0.'],
  'not-run': ['Not part of this run', 'Pick another run, or run this case from “Run pipeline”.'],
  'no-committed': ['No committed output', 'artifacts/outputs has no .stp for this case yet. Select a run or run the pipeline.'],
  'no-run': ['No run selected', 'Run the pipeline on this dataset to see your output next to the expected part.'],
};

function renderCompare(c, ours) {
  const ds = dataset(), base = `${ds.inputs_url}/${c.name}`;
  const images = [...c.views.map((file) => file.replace(/\.png$/, '')), ...(c.context ? ['context'] : [])];
  if (!images.includes(S.view) || S.view === 'context') S.view = 'view_6_iso_front_right_top';
  $('#main').innerHTML = `
    <div class="layout">
      <section class="images">
        <div class="col-head"><span>Input images</span><span class="muted">click one to turn the 3D views to it</span></div>
        <div class="img-grid">
          ${images.map((name) => `<button data-view="${name}" aria-pressed="${name === S.view}">
            <img loading="lazy" alt="${VIEW_LABEL(name)} view" src="${base}/${name === 'context' ? 'context.png' : `images/${name}.png`}">
            <span>${VIEW_LABEL(name)}</span></button>`).join('')}
        </div>
      </section>
      <figure class="pane">
        <figcaption><span><i class="sw sw-ours"></i>Our output</span><span class="muted">${esc(ours.label || '')}</span></figcaption>
        <div class="stage" id="stage-ours"></div>
      </figure>
      <figure class="pane">
        <figcaption><span><i class="sw sw-expected"></i>Ground truth</span><span class="muted">${c.truth ? size(c.truth.size) : ''}</span></figcaption>
        <div class="stage" id="stage-truth">${c.truth ? '' : `<div class="stage-msg"><strong>No ground truth file</strong>
          <span>The practice set ships no expected .stp. Compare against the images and the numbers below.</span></div>`}</div>
      </figure>
    </div>
    <div id="metrics"></div>
    <section class="steps-section"><h2>Steps</h2><div id="steps"></div></section>
    <details class="brief"><summary>Design brief</summary><article class="prose" id="brief-text"></article></details>`;
  loadCompare(c, ours);
  const id = live.id;
  fetch(`/api/description?dataset=${S.dataset}&case=${c.name}`).then((r) => r.text()).then((text) => {
    if (id === live.id && $('#brief-text')) $('#brief-text').innerHTML = markdown(text);
  });
}

function applyView() {
  $$('.img-grid button').forEach((b) => b.setAttribute('aria-pressed', b.dataset.view === S.view));
  if (S.view !== 'context') live.viewers.forEach((v) => v.setView(S.view));
}

function stageMessage(stage, title, detail, bad = false) {
  if (stage) stage.innerHTML = `<div class="stage-msg ${bad ? 'bad' : ''}"><strong>${esc(title)}</strong><span>${esc(detail)}</span></div>`;
}

async function loadCompare(c, ours) {
  const id = live.id;
  const stale = () => id !== live.id;
  const oursStage = $('#stage-ours'), truthStage = $('#stage-truth');
  const loading = '<div class="stage-msg"><span class="spin"></span><span>Loading STEP…</span></div>';
  const state = { ours: undefined, truth: undefined };
  const settle = (promise) => promise.then((value) => ({ value }), (error) => ({ error }));
  const mount = async (stage, meshes, color) => {
    const viewer = await createViewer(stage, [{ meshes, color }]);
    if (stale()) return viewer.dispose();
    live.viewers.push(viewer);
    viewer.setView(S.view);
    return viewer;
  };
  const link = () => {  // rotating one view rotates the other
    const [a, b] = [state.oursViewer, state.truthViewer];
    if (!a || !b) return;
    a.onChange((pose) => b.setPose(pose));
    b.onChange((pose) => a.setPose(pose));
  };

  if (!ours.url) {
    state.ours = null;
    stageMessage(oursStage, ...MISSING[ours.missing]);
  } else {
    oursStage.innerHTML = loading;
    settle(loadStep(ours.url, ours.key)).then(async (result) => {
      if (stale()) return;
      state.ours = result;
      renderMetrics(c, ours, state);
      if (result.error) return stageMessage(oursStage, 'Not a readable STEP solid', `${result.error.message}. This would score 0.`, true);
      state.oursViewer = await mount(oursStage, result.value.meshes, COLORS.ours)
        .catch((e) => stageMessage(oursStage, 'Could not render', e.message, true));
      link();
    });
  }
  if (c.truth) {
    truthStage.innerHTML = loading;
    settle(loadStep(c.truth.url, `${c.truth.url}?${c.truth.mtime}`)).then(async (result) => {
      if (stale()) return;
      state.truth = result;
      renderMetrics(c, ours, state);
      if (result.error) return stageMessage(truthStage, 'Could not read the ground truth .stp', result.error.message, true);
      state.truthViewer = await mount(truthStage, result.value.meshes, COLORS.expected)
        .catch((e) => stageMessage(truthStage, 'Could not render', e.message, true));
      link();
    });
  }
  renderMetrics(c, ours, state);
}

function delta(expected, actual) {
  if (expected == null || actual == null || !isFinite(expected) || !isFinite(actual) || !expected) return '<td class="num muted">–</td>';
  const pct = (actual - expected) / expected * 100;
  const level = Math.abs(pct) <= 1 ? 'good' : Math.abs(pct) <= 5 ? 'warn' : 'bad';
  const mark = { good: '✓', warn: '~', bad: '✗' }[level];
  const text = Math.abs(pct).toFixed(Math.abs(pct) < 10 ? 2 : 0);
  return `<td class="num d-${level}">${mark} ${Number(text) === 0 ? '' : pct > 0 ? '+' : '−'}${text} %</td>`;
}

function renderMetrics(c, ours, state) {
  const facts = c.facts || {}, box = facts.bounding_box_mm;
  const truth = state.truth && state.truth.value && state.truth.value.measured;
  const mine = state.ours && state.ours.value && state.ours.value.measured;
  const sorted = (dims) => (dims ? [...dims].sort((a, b) => b - a) : [null, null, null]);
  const expectedBox = sorted(box ? [box.x, box.y, box.z] : truth && truth.bbox);
  const mineBox = sorted(mine && mine.bbox);
  const rows = [
    ['Longest side', 'mm', expectedBox[0], mineBox[0]],
    ['Middle side', 'mm', expectedBox[1], mineBox[1]],
    ['Shortest side', 'mm', expectedBox[2], mineBox[2]],
    ['Volume', 'cm³', (facts.volume_mm3 ?? (truth && truth.volume)) / 1000, mine && mine.volume / 1000],
    ['Surface area', 'cm²', (facts.surface_area_mm2 ?? (truth && truth.area)) / 100, mine && mine.area / 100],
  ];
  const pending = ours.url && state.ours === undefined;
  const invalid = state.ours && state.ours.error;
  const note = pending ? 'measuring…' : invalid ? 'unreadable .stp' : !ours.url ? MISSING[ours.missing][0].toLowerCase() : '';
  setHTML($('#metrics'), `
    <div class="metrics">
      <table>
        <caption>Size check <span class="muted">— sides sorted by length; ours measured from the mesh (about ±0.5 %)</span></caption>
        <thead><tr><th></th><th class="num">Ground truth</th><th class="num">Ours</th><th class="num">Difference</th></tr></thead>
        <tbody>${rows.map(([label, unit, expected, actual]) => `<tr>
          <th>${label}</th><td class="num">${num(expected)} <small>${unit}</small></td>
          <td class="num">${actual == null ? `<span class="muted">${note || '–'}</span>` : `${num(actual)} <small>${unit}</small>`}</td>
          ${delta(expected, actual)}</tr>`).join('')}</tbody>
      </table>
    </div>`);
}

// -- steps --------------------------------------------------------------------------------------

function buildTrace(events) {
  const t = { steps: new Map(), roots: [], logs: [], artifacts: [], start: null, end: null, status: 'running', error: null, last: null };
  for (const e of events) {
    if (t.start == null) t.start = e.ts;
    t.last = e.ts;
    if (e.t === 'case_start') t.start = e.ts;
    else if (e.t === 'case_end') Object.assign(t, { end: e.ts, status: e.status, error: e.error });
    else if (e.t === 'step_start') {
      const step = { ...e, start: e.ts, end: null, status: 'running', children: [], logs: [], artifacts: [] };
      t.steps.set(e.id, step);
      (t.steps.get(e.parent) ? t.steps.get(e.parent).children : t.roots).push(step);
    } else if (e.t === 'step_end') {
      const step = t.steps.get(e.id);
      if (step) Object.assign(step, { end: e.ts, status: e.status, output: e.output, metrics: e.metrics, error: e.error });
    } else if (e.t === 'log') (t.steps.get(e.step) || t).logs.push(e);
    else if (e.t === 'artifact') (t.steps.get(e.step) || t).artifacts.push(e);
  }
  return t;
}

async function fetchTrace(run, name) {
  const key = `${run}/${name}`;
  const entry = traces.get(key) || { events: [], loading: false };
  traces.set(key, entry);
  if (entry.loading) return;
  entry.loading = true;
  try {
    const data = await api(`/api/events?run=${encodeURIComponent(run)}&case=${encodeURIComponent(name)}&after=${entry.events.length}`);
    if (data.events.length || !entry.loaded) {
      entry.events.push(...data.events);
      entry.loaded = true;
      if (S.runId === run && S.caseName === name) renderSteps(currentCase());
    }
  } catch (error) { entry.failed = error.message; } finally { entry.loading = false; }
}

function valueHTML(value, top = true) {
  if (value == null) return '<span class="muted">none</span>';
  if (typeof value === 'string') return `<pre>${esc(value)}</pre>`;
  if (typeof value !== 'object') return `<code>${esc(value)}</code>`;
  if (top && !Array.isArray(value) && Object.keys(value).length <= 16) {
    return `<dl class="kv">${Object.entries(value).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${valueHTML(v, false)}</dd>`).join('')}</dl>`;
  }
  return `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`;
}

function artifactsHTML(list, base) {
  if (!list.length) return '';
  const item = (a) => {
    const url = `${base}/${a.path}`;
    if (/\.(png|jpe?g|gif|webp|svg)$/i.test(a.name)) {
      return `<button class="art art-img" data-image="${esc(url)}" title="${esc(a.name)}"><img loading="lazy" src="${esc(url)}" alt=""><span>${esc(a.name)}</span></button>`;
    }
    const meta = `<span class="muted">${size(a.size)}</span>${a.role === 'output' ? '<span class="tag">final output</span>' : ''}`;
    if (/\.(stp|step)$/i.test(a.name)) {
      return `<div class="art art-file"><span class="art-name">${esc(a.name)}</span>${meta}
        <button class="link" data-step3d="${esc(url)}" data-name="${esc(a.name)}">View 3D</button><a class="link" href="${esc(url)}" target="_blank">Open</a></div>`;
    }
    const shown = openText.has(url);
    return `<div class="art art-file"><span class="art-name">${esc(a.name)}</span>${meta}
      <button class="link" data-text="${esc(url)}">${shown ? 'Hide' : 'Show'}</button><a class="link" href="${esc(url)}" target="_blank">Open</a>
      ${shown ? `<pre>${esc(textCache.get(url) ?? 'Loading…')}</pre>` : ''}</div>`;
  };
  const images = list.filter((a) => /\.(png|jpe?g|gif|webp|svg)$/i.test(a.name));
  const others = list.filter((a) => !images.includes(a));
  return `<h4>Artifacts</h4>${images.length ? `<div class="art-grid">${images.map(item).join('')}</div>` : ''}${others.map(item).join('')}`;
}

function logsHTML(logs, start) {
  if (!logs.length) return '';
  return `<h4>Log</h4><div class="logs">${logs.map((l) => `<div class="log log-${esc(l.level)}">
    <time>+${duration(l.ts - start)}</time><span>${esc(l.msg)}</span></div>`).join('')}</div>`;
}

function renderSteps(c) {
  const tab = $('#steps'), run = currentRun();
  if (!tab) return;
  if (!run) {
    setHTML(tab, `<p class="muted">No run selected. Press <strong>Run this case</strong>; every <code>trace.step(...)</code>
      in the agent then shows up here live, with its input, output, logs and files.</p>`);
    return;
  }
  const key = `${run.id}/${c.name}`, entry = traces.get(key);
  if (!entry || !entry.loaded) {
    setHTML(tab, entry && entry.failed ? `<p class="empty">Could not load the trace: ${esc(entry.failed)}</p>` : '<p class="empty">Loading trace…</p>');
    if (!entry) fetchTrace(run.id, c.name);
    return;
  }
  if (!entry.events.length) {
    setHTML(tab, '<p class="muted">This case is not in the selected run. Pick another run, or press <strong>Run this case</strong>.</p>');
    return;
  }
  const t = buildTrace(entry.events);
  const running = run.status === 'running' && t.status === 'running';
  if (!running && t.status === 'running') t.status = 'error';  // the process died mid-case
  const now = Date.now() / 1000;
  const end = t.end || (running ? now : t.last);
  const total = Math.max(end - t.start, 1e-6);
  const base = `/files/runs/${run.id}/${c.name}`;
  const all = [...t.steps.values()];
  if (running && S.follow) {
    const active = all.filter((s) => !s.end).pop();
    if (active) open.add(`${key}/${active.id}`);
  }

  const stepHTML = (s, depth) => {
    const isOpen = open.has(`${key}/${s.id}`);
    const status = s.end ? s.status : running ? 'running' : 'interrupted';
    const stop = s.end || (running ? now : t.last);
    const left = Math.min((s.start - t.start) / total * 100, 99.5), width = Math.max((stop - s.start) / total * 100, 0.6);
    const chips = Object.entries(s.metrics || {}).map(([k, v]) => `<span class="chip-m">${esc(k)} <b>${esc(typeof v === 'number' ? num(v, 3) : v)}</b></span>`).join('');
    const count = s.artifacts.length ? `<span class="chip-m">${s.artifacts.length} file${s.artifacts.length === 1 ? '' : 's'}</span>` : '';
    let body = '';
    if (isOpen) {
      const parts = [
        s.error ? `<h4>Error</h4><pre class="error">${esc(s.error)}</pre>` : '',
        s.input !== undefined ? `<h4>Input</h4>${valueHTML(s.input)}` : '',
        s.end && s.output !== undefined ? `<h4>Output</h4>${valueHTML(s.output)}` : '',
        s.meta ? `<h4>Meta</h4>${valueHTML(s.meta)}` : '',
        logsHTML(s.logs, s.start),
        artifactsHTML(s.artifacts, base),
      ].join('');
      body = `<div class="step-body" style="margin-left:${depth * 20 + 30}px">${parts || '<span class="muted">Nothing recorded for this step.</span>'}</div>`;
    }
    return `<div class="step">
      <button class="step-row" data-step="${esc(s.id)}" aria-expanded="${isOpen}" style="padding-left:${depth * 20 + 8}px">
        <span class="caret"></span><span class="kind kind-${esc(s.kind)}">${esc(s.kind)}</span>
        <span class="step-name">${esc(s.name)}</span><span class="chips">${chips}${count}</span>
        <span class="wf"><i class="wf-${status}" style="left:${left}%;width:${Math.min(width, 100 - left)}%"></i></span>
        <span class="dur">${duration(stop - s.start)}</span><span class="st st-${status}">${status}</span>
      </button>${body}${s.children.map((child) => stepHTML(child, depth + 1)).join('')}</div>`;
  };

  const failed = all.filter((s) => s.status === 'error').length;
  setHTML(tab, `
    <div class="steps-bar">
      <span class="st st-${t.status}">${esc(t.status)}</span>
      <span><b>${all.length}</b> step${all.length === 1 ? '' : 's'}</span>
      ${failed ? `<span class="d-bad"><b>${failed}</b> failed</span>` : ''}
      <span>total <b>${duration(end - t.start)}</b></span>
      <span class="muted">started ${clock(t.start)}</span>
      <span class="spacer"></span>
      ${running ? `<label class="check"><input type="checkbox" id="follow" ${S.follow ? 'checked' : ''}> Follow live</label>` : ''}
      <button class="link" data-steps="open">Expand all</button><button class="link" data-steps="close">Collapse all</button>
    </div>
    ${t.error ? `<pre class="error">${esc(t.error)}</pre>` : ''}
    <div class="steps">${t.roots.map((s) => stepHTML(s, 0)).join('') || '<p class="empty">No steps recorded yet.</p>'}</div>
    ${t.logs.length || t.artifacts.length ? `<div class="case-level"><h3>Outside any step</h3>
      ${logsHTML(t.logs, t.start)}${artifactsHTML(t.artifacts, base)}</div>` : ''}`);
}

// -- brief --------------------------------------------------------------------------------------

function markdown(text) {
  const inline = (line) => esc(line).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>');
  let html = '', list = false;
  for (const line of text.split('\n')) {
    const item = line.match(/^\s*[-*]\s+(.*)/), heading = line.match(/^(#{1,4})\s+(.*)/);
    if (list && !item) { html += '</ul>'; list = false; }
    if (item) { html += `${list ? '' : '<ul>'}<li>${inline(item[1])}</li>`; list = true; }
    else if (heading) html += `<h${heading[1].length + 1}>${inline(heading[2])}</h${heading[1].length + 1}>`;
    else if (line.trim()) html += `<p>${inline(line)}</p>`;
  }
  return html + (list ? '</ul>' : '');
}

// -- overlay (image lightbox, 3D preview, test results) -------------------------------------------

let overlayViewer = null;
function closeOverlay() {
  if (overlayViewer) overlayViewer.dispose();
  overlayViewer = null;
  $('#overlay').className = '';
  $('#overlay').innerHTML = '';
}
function openOverlay(html) {
  closeOverlay();
  $('#overlay').className = 'open';
  $('#overlay').innerHTML = `<div class="dialog" role="dialog" aria-modal="true"><button class="close" data-close aria-label="Close">×</button>${html}</div>`;
}

async function openStep3D(url, name) {
  openOverlay(`<h2>${esc(name)}</h2><div class="stage big" id="stage-dialog"><div class="stage-msg"><span class="spin"></span><span>Loading STEP…</span></div></div><p id="dialog-info" class="muted"></p>`);
  try {
    const { meshes, measured } = await loadStep(url);
    const stage = $('#stage-dialog');
    if (!stage) return;
    const viewer = await createViewer(stage, [{ meshes, color: COLORS.ours }]);
    if (!stage.isConnected) return viewer.dispose();
    overlayViewer = viewer;
    $('#dialog-info').textContent = `${[...measured.bbox].map((v) => num(v)).join(' × ')} mm · volume ${num(measured.volume / 1000)} cm³ · area ${num(measured.area / 100)} cm² · ${num(measured.triangles, 0)} triangles`;
  } catch (error) { stageMessage($('#stage-dialog'), 'Not a readable STEP solid', error.message, true); }
}

// -- events -------------------------------------------------------------------------------------

function render() {
  normalise();
  writeHash();
  renderTop();
  renderMain();
}

function selectCase(name, datasetId = S.dataset) {
  if (datasetId !== S.dataset) Object.assign(S, { dataset: datasetId, runId: null });
  S.caseName = name;
  render();
  if (S.runId) fetchTrace(S.runId, name);
}

function moveCase(offset) {
  const list = cases(), next = list[list.findIndex((c) => c.name === S.caseName) + offset];
  if (next) selectCase(next.name);
}

document.addEventListener('click', async (event) => {
  const target = event.target.closest('button, [data-close], a');
  if (event.target.id === 'overlay' || (target && target.dataset.close !== undefined)) return closeOverlay();
  if (!target || target.tagName === 'A') return;
  const d = target.dataset;
  if (d.move) return moveCase(Number(d.move));
  if (d.view) { S.view = d.view; return applyView(); }
  if (d.image) return openOverlay(`<img class="lightbox" src="${esc(d.image)}" alt="">`);
  if (d.step3d) return openStep3D(d.step3d, d.name || 'STEP');
  if (d.step) {
    const key = `${S.runId}/${S.caseName}/${d.step}`;
    if (open.has(key)) { open.delete(key); S.follow = false; } else open.add(key);
    return renderSteps(currentCase());
  }
  if (d.steps) {
    const entry = traces.get(`${S.runId}/${S.caseName}`);
    const prefix = `${S.runId}/${S.caseName}/`;
    [...open].filter((k) => k.startsWith(prefix)).forEach((k) => open.delete(k));
    if (d.steps === 'open' && entry) entry.events.filter((e) => e.t === 'step_start').forEach((e) => open.add(prefix + e.id));
    else S.follow = false;
    return renderSteps(currentCase());
  }
  if (d.text) {
    if (openText.has(d.text)) openText.delete(d.text);
    else {
      openText.add(d.text);
      if (!textCache.has(d.text)) {
        fetch(d.text).then((r) => r.text()).then((text) => {
          textCache.set(d.text, text.length > 300000 ? `${text.slice(0, 300000)}\n… clipped` : text);
          renderSteps(currentCase());
        });
      }
    }
    return renderSteps(currentCase());
  }
  if (d.action === 'run') {
    try {
      const { id } = await api('/api/run', { dataset: S.dataset, only: [S.caseName], target: 'run' });
      S.runId = id;
      toast(`Started run ${id}`);
      await poll();
    } catch (error) { toast(error.message, true); }
    return;
  }
  if (d.action === 'stop') return api('/api/stop', { run: S.runId }).then(poll, (e) => toast(e.message, true));
});

document.addEventListener('change', (event) => {
  const el = event.target;
  if (el.id === 'case-select') { const [ds, name] = el.value.split('/'); selectCase(name, ds); }
  else if (el.id === 'run-select') { S.runId = el.value; render(); if (S.runId) fetchTrace(S.runId, S.caseName); }
  else if (el.id === 'follow') S.follow = el.checked;
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') return closeOverlay();
  if (event.target.matches('input, select, textarea') || event.metaKey || event.ctrlKey || event.altKey) return;
  if (event.key === 'j' || event.key === 'ArrowRight') moveCase(1);
  else if (event.key === 'k' || event.key === 'ArrowLeft') moveCase(-1);
  else if (/^[1-8]$/.test(event.key)) { S.view = VIEW_NAMES[Number(event.key) - 1]; applyView(); }
});

window.addEventListener('hashchange', async () => { readHash(); await ensureCases(); render(); });

// -- polling ------------------------------------------------------------------------------------

let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const state = await api('/api/state');
    const finished = S.runs.filter((r) => r.status === 'running').map((r) => r.id);
    Object.assign(S, { datasets: state.datasets, runs: state.runs, now: state.now });
    normalise();
    await ensureCases(finished.some((id) => (S.runs.find((r) => r.id === id) || {}).status !== 'running'));
    render();
    const run = currentRun();
    if (run && S.caseName) {
      const entry = traces.get(`${run.id}/${S.caseName}`);
      const rc = runCase(run, S.caseName);
      if (!entry || run.status === 'running' || (rc && entry.loaded && !entry.events.length)) fetchTrace(run.id, S.caseName);
    }
    document.body.classList.remove('offline');
  } catch (error) {
    document.body.classList.add('offline');
  } finally { polling = false; }
}

readHash();
await poll();
setInterval(() => { if (!document.hidden) poll(); }, 2000);
