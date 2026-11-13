// Run by ui/check_ui.py. Scrolls the side list's two lists in a real Chrome, waits for several polls that redraw
// them, and exits 1 if either list moved or nothing was redrawn (then the check proved nothing).
const port = process.argv[2];
const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const page = targets.find((t) => t.type === 'page');
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
let id = 0; const waiting = new Map();
ws.addEventListener('message', (m) => { const d = JSON.parse(m.data); if (waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); } });
const send = (method, params = {}) => new Promise((r) => { const i = ++id; waiting.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const run = async (expression) => (await send('Runtime.evaluate', { expression, returnByValue: true })).result.result.value;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
await sleep(5000);
console.log('running rows', await run(`document.querySelectorAll('#nav-now .now button').length`),
  'overflow', await run(`(() => { const n = document.querySelector('#nav-now .now'); return n ? n.scrollHeight - n.clientHeight : -1 })()`));
await run(`document.querySelector('#nav-now .now').scrollTop = 120; document.querySelector('#nav-cases').scrollTop = 400; 1`);
const before = await run(`[document.querySelector('#nav-now .now').scrollTop, document.querySelector('#nav-cases').scrollTop]`);
await run(`window.__redraws = 0; new MutationObserver(() => window.__redraws++).observe(document.querySelector('#nav-now'), { childList: true }); 1`);
await sleep(7000);   // three or more polls
const after = await run(`[document.querySelector('#nav-now .now').scrollTop, document.querySelector('#nav-cases').scrollTop]`);
const redrawn = await run(`window.__redraws`);
console.log('before', before, 'after', after, 'redraws of the running list meanwhile:', redrawn);
let ok = redrawn > 0 && before[0] === after[0] && before[1] === after[1];

// The side list is styled (a lost block of CSS shows as unstyled runs and search)
const styled = await run(`(() => { const r = document.querySelector('#side .runs .run'), f = document.querySelector('#nav-filter');
  return { runs: r ? getComputedStyle(r).display : 'none', search: f ? getComputedStyle(f).height : '' }; })()`);
console.log('side list styles', styled);
ok = ok && styled.runs === 'grid' && styled.search === '32px';

// An agent at work: its run shows the pipeline band, the agent band and a live stop
await run(`location.hash = '#/practice/test_case_1?run=agents-live'; 1`);
await sleep(5000);
const line = await run(`({ bands: [...document.querySelectorAll('#timeline .phase b')].map((b) => b.textContent),
  live: document.querySelectorAll('#timeline .stop.agent.running').length,
  stops: [...document.querySelectorAll('#timeline .stop .name')].map((n) => n.textContent) })`);
console.log('agent run line', JSON.stringify(line));
ok = ok && line.bands.length === 2 && line.live === 1 && line.stops.includes('draft 1');
// nothing published yet: the output file still holds the first solid
const liveFile = await run(`(document.querySelector('#timeline .stop.infile .name') || {}).textContent`);
console.log('in the file while the agent works:', liveFile);
ok = ok && liveFile === 'bounding box';

// A stop the reader chose stays chosen when the agent adds a stop before it (a draft saved earlier)
const { appendFileSync } = await import('node:fs');
await run(`[...document.querySelectorAll('#timeline .stop')].find((b) => b.querySelector('.name').textContent === 'check 1').click(); 1`);
await sleep(500);
const chosen = () => run(`(document.querySelector('#timeline .stop[aria-current="true"] .name') || {}).textContent`);
const picked = await chosen();
appendFileSync(`${process.argv[3]}/previews.jsonl`, JSON.stringify({ index: 1, t: Date.now() / 1000 - 60, script: 'early.py', ok: true, path: 'x', sha: 'y' }) + '\n');
await sleep(5000);
const still = await chosen(), names = await run(`[...document.querySelectorAll('#timeline .stop .name')].map((n) => n.textContent)`);
console.log('chosen stop', picked, '->', still, 'after a draft came in before it:', JSON.stringify(names));
ok = ok && picked === 'check 1' && still === 'check 1' && JSON.stringify(names) === JSON.stringify(['bounding box', 'draft 1', 'draft 2', 'check 1', 'working']);
// A finished agent run ends on what its output file holds, and the case list names who made it
await run(`location.hash = '#/practice/test_case_3?run=agents-done'; 1`);
await sleep(4000);
const ended = await run(`({ stops: [...document.querySelectorAll('#timeline .stop .name')].map((n) => n.textContent),
  values: [...document.querySelectorAll('#timeline .stop .v')].map((n) => n.textContent.trim()),
  by: (document.querySelector('#nav-cases [data-goto="practice/test_case_3"] .by') || {}).textContent,
  file: (document.querySelector('#timeline .stop.infile .name') || {}).textContent,
  listed: (document.querySelector('#nav-cases [data-goto="practice/test_case_3"] em') || {}).textContent })`);
const endChosen = await chosen();
console.log('finished agent run', JSON.stringify(ended), 'opens on', endChosen);
ok = ok && JSON.stringify(ended.stops) === JSON.stringify(['bounding box', 'check 1', 'pipeline done', 'output'])
  && ended.values[ended.values.length - 1] === '0.860' && endChosen === 'output' && ended.by === 'P'
  && ended.file === 'output' && ended.listed === '0.86';

// A finished pipeline run opens on its last step, export, though export left no solid of its own
await run(`location.hash = '#/practice/test_case_2?run=20260101-000000-test'; 1`);
await sleep(4000);
const finalStop = await chosen();
console.log('finished run opens on', finalStop);
ok = ok && finalStop === 'export step';
ws.close();
process.exit(ok ? 0 : 1);
