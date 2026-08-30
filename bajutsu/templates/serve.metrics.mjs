// serve.metrics.mjs — the hub's cross-project comparison view. A serve.*.mjs section module
// (BE-0247); imports its shared helpers from serve.core.mjs. Its body only defines — the one
// top-level listener is wired by initMetrics(), which the entry module (serve.author.mjs) calls
// after every section has evaluated.
import {$, esc, getJSON, isFetchError, FETCH_ERROR, switchProject, projectsCache, unavailableReason} from './serve.core.mjs';

// ---- Cross-project comparison (BE-0226 unit 3): the hub's projects ranked side by side ----
// Client-rendered from the unit-2 /api/metrics/projects model (JSON, not a server-rendered report
// like /stats) because the surface is interactive: sortable columns and a row that opens the
// project's run history. Read-only — it re-presents the deterministic verdicts `run` already
// decided, adding no LLM to the path. The tab is revealed only when a real hub exists
// (loadProjects, >1 project); a single-config serve never sees it, since there is nothing to
// compare.
//
// Navigating and activating are deliberately separate gestures (#1720). A row click used to POST
// .../activate, so a reader ranking projects rebound the config every tab against the deployment
// reads, having pressed nothing that announced a write. The row now only opens a read-only detail;
// rebinding takes the row's own Activate button and a confirmation.
let metricsCache=[];
let metricsSort={key:'pass_rate',dir:'asc'};  // default: worst pass-rate first — the project to look at
// The project whose read-only detail is open, or null for the ranking. Doubles as the guard that
// drops an in-flight detail fetch whose reader has already gone back or opened another project.
let metricsDetail=null;

// Each column, and for the sortable ones whether "worst first" is ascending (pass-rate: low is bad)
// or descending (flaky-rate / duration: high is bad), so a first click surfaces the worst offender.
const METRIC_COLS=[
  {key:'name',label:'Project',cell:m=>metricOpen(m)},
  {key:'runs',label:'Runs',cell:m=>String(m.runs)},
  {key:'pass_rate',label:'Pass-rate',sortable:true,worst:'asc',cell:m=>metricCell(m,metricPct(m.pass_rate))},
  {key:'flaky_rate',label:'Flaky-rate',sortable:true,worst:'desc',cell:m=>metricCell(m,metricPct(m.flaky_rate))},
  {key:'duration_p50_s',label:'p50',sortable:true,worst:'desc',cell:m=>metricCell(m,metricSecs(m.duration_p50_s))},
  {key:'duration_p95_s',label:'p95',sortable:true,worst:'desc',cell:m=>metricCell(m,metricSecs(m.duration_p95_s))},
  {key:'trend',label:'Trend',cell:m=>metricSpark(m.trend)},
  {key:'act',label:'',cell:m=>metricAction(m)},
];

// The row's one state-changing control, or the marker that says there is nothing to change. Mirrors
// how the Projects page distinguishes the active binding from a switchable one (BE-0275), so the two
// surfaces read the same.
//
// A reader who may not activate gets the button disabled with the server's own reason on it, from
// the boot read's capability block (#1721) — the same seam the Orgs tab gates on. The flag reports;
// it never gates: the endpoint still refuses on its own, and switchProject spells that refusal out
// for a role that changed since boot.
function metricAction(m){
  const active=projectsCache.find(p=>p.name===m.name);
  if(active&&active.active)return '<span class="mactive" data-testid="metrics.active">active</span>';
  const blocked=unavailableReason('activate');
  return `<button type="button" class="cfgbtn mact" data-act="activate" data-testid="metrics.activate"`
    +`${blocked?` disabled title="${esc(blocked)}"`:''}>Activate</button>`;
}

// The drill-down's control is a real button in the name cell, not the row itself. Giving the <tr>
// role="button" would have been the shorter route to keyboard access, but it overrides the row's
// implicit table semantics — a screen reader loses row/cell navigation over the very ranking this
// view exists to present — and it nests the row's own Activate button inside an ARIA button, which
// ARIA forbids. A button keeps the table intact and brings Enter and Space with it, natively.
function metricOpen(m){
  return `<button type="button" class="mopen" data-act="open" data-testid="metrics.open"`
    +` title="Open this project\u2019s run history">${esc(m.name)}</button>`;
}

function metricPct(v){return Math.round(v*100)+'%'}
function metricSecs(v){return v.toFixed(1)+'s'}
// An unrun project's scalars are all 0.0 (a blank row, not a real zero) — dash them so it never
// looks like the best pass-rate or the fastest run.
function metricCell(m,txt){return m.runs?txt:'<span class="none">—</span>'}

// The pass-rate trend as an inline SVG polyline (x by index, y inverted so 0% sits at the bottom),
// the same shape as the single-config /stats trend. Fewer than two points can't draw a line.
function metricSpark(trend){
  const pts=Array.isArray(trend)?trend:[];
  if(pts.length<2)return '<span class="none">—</span>';
  const points=pts.map((d,i)=>`${(i/(pts.length-1)*100).toFixed(1)},${(22-d.pass_rate*22).toFixed(1)}`).join(' ');
  return `<svg class="mspark" viewBox="0 0 100 22" preserveAspectRatio="none" role="img" aria-label="pass-rate trend"><line class="axis" x1="0" y1="22" x2="100" y2="22"/><polyline points="${points}"/></svg>`;
}

async function loadMetrics(){
  const host=$('#metrics-host');
  // FETCH_ERROR, not an empty list (#1716): "no projects registered" and "the comparison couldn't be
  // read" are different situations and want different copy — the first tells you to register another
  // project, the second to retry. An empty fallback here reported a healthy hub as an empty one.
  const rows=await getJSON('/api/metrics/projects',FETCH_ERROR);
  if(isFetchError(rows)){host.innerHTML='<div class="mempty" data-testid="metrics.error">Couldn\u2019t load the comparison. Refresh to retry.</div>';return}
  metricsCache=Array.isArray(rows)?rows:[];
  if(!metricsCache.length){host.innerHTML='<div class="mempty" data-testid="metrics.empty">No projects to compare — register more than one with <code>bajutsu project add</code>.</div>';return}
  renderMetrics();
}

function sortedMetrics(){
  const {key,dir}=metricsSort;
  return metricsCache.slice().sort((a,b)=>{
    // An unrun project's scalars are all 0.0 (no signal, not a real worst) — keep it out of the
    // ranking so pass-rate-ascending never puts it ahead of a project that genuinely scores low.
    if(!a.runs!==!b.runs)return a.runs?-1:1;
    const av=a[key],bv=b[key];
    const c=typeof av==='string'?av.localeCompare(bv):av-bv;
    return dir==='asc'?c:-c;
  });
}

// Click a sortable header to rank by it (worst-first on first click); click the active one to flip.
function sortMetrics(key){
  const col=METRIC_COLS.find(c=>c.key===key);
  if(!col||!col.sortable)return;
  if(metricsSort.key===key)metricsSort.dir=metricsSort.dir==='asc'?'desc':'asc';
  else metricsSort={key,dir:col.worst};
  renderMetrics();
}

function renderMetrics(){
  const host=$('#metrics-host');
  metricsDetail=null;  // the ranking is on screen now, so any in-flight detail fetch is stale
  const {key,dir}=metricsSort;
  const head=METRIC_COLS.map(c=>{
    if(!c.sortable)return `<th>${c.label}</th>`;
    const active=c.key===key,arrow=active?(dir==='asc'?'▲':'▼'):'';
    return `<th class="msort${active?' active':''}" data-key="${c.key}" data-testid="metrics.sort.${c.key}">${c.label}<span class="arrow">${arrow}</span></th>`;
  }).join('');
  const body=sortedMetrics().map(m=>
    `<tr class="mrow" data-testid="metrics.row" data-name="${esc(m.name)}">`
    +METRIC_COLS.map(c=>`<td>${c.cell(m)}</td>`).join('')+'</tr>').join('');
  host.innerHTML=`<table class="mtable" data-testid="metrics.table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  host.querySelectorAll('th.msort').forEach(th=>th.addEventListener('click',()=>sortMetrics(th.dataset.key)));
  host.querySelectorAll('button[data-act="open"]').forEach(b=>
    b.addEventListener('click',()=>openMetricsDetail(b.closest('tr.mrow').dataset.name)));
  // Clicking anywhere else on the row is the pointer convenience the view already had. It ignores an
  // event one of the row's own buttons raised, so Activate never also navigates — one guard on the
  // row instead of a stopPropagation each button could forget.
  host.querySelectorAll('tr.mrow').forEach(tr=>
    tr.addEventListener('click',e=>{if(!e.target.closest('button'))openMetricsDetail(tr.dataset.name)}));
  host.querySelectorAll('button[data-act="activate"]').forEach(b=>
    b.addEventListener('click',()=>activateFromComparison(b.closest('tr.mrow').dataset.name)));
}

// The read-only drill-down a row opens: that project's run history, newest first. Served by the
// project hub's existing per-project runs route, which answers the same run-summary shape the Replay
// history renders — so nothing is added server-side and the two lists agree on shape.
async function openMetricsDetail(name){
  const host=$('#metrics-host');
  metricsDetail=name;
  host.innerHTML=`<div class="mdetail" data-testid="metrics.detail">`
    +`<div class="mdetailhead"><button type="button" class="cfgbtn" data-act="back" data-testid="metrics.detail-back">&#8592; Comparison</button>`
    +`<span class="mname">${esc(name)}</span><span class="muted" style="font-size:.8em">read-only — opening this history activates nothing</span></div>`
    +`<ul class="fslist mruns" data-testid="metrics.detail-runs"><li class="muted">Loading run history\u2026</li></ul></div>`;
  host.querySelector('button[data-act="back"]').addEventListener('click',renderMetrics);
  const runs=await getJSON('/api/projects/'+encodeURIComponent(name)+'/runs',FETCH_ERROR);
  // The reader may have gone back, or opened another project, while the fetch was in flight —
  // rendering into whatever is on screen now would attribute one project's runs to another.
  if(metricsDetail!==name)return;
  const ul=host.querySelector('[data-testid="metrics.detail-runs"]');
  if(!ul)return;
  if(isFetchError(runs)){ul.innerHTML='<li class="muted" data-testid="metrics.detail-error">Couldn\u2019t load this project\u2019s runs. Go back and retry.</li>';return}
  if(!Array.isArray(runs)||!runs.length){ul.innerHTML='<li class="muted" data-testid="metrics.detail-empty">No runs recorded for this project yet.</li>';return}
  ul.innerHTML=runs.map(r=>`<li data-testid="metrics.detail-run"><span class="dot ${r.ok?'ok':'ng'}"></span>`
    +`<span class="hid">${esc(r.id)}</span>`
    +`<span class="hsum">${r.passed}/${r.total}${r.scenarios&&r.scenarios.length?' \u00b7 '+esc(r.scenarios.join(', ')):''}</span></li>`).join('');
}

// Activate from the comparison: an explicit press, confirmed first, naming what the rebind reaches.
// The confirm is the whole point of the split — activation is deployment-wide state, not a per-viewer
// preference, so the reader has to have meant it.
async function activateFromComparison(name){
  if(!window.confirm(`Activate project "${name}"?\n\nThis rebinds the live config this deployment serves — every tab against this server follows, not just yours. It does not change any recorded run.`))return;
  await switchProject(name);  // surfaces its own error, including the admin-only refusal
  renderMetrics();  // move the "active" marker to whatever the server now reports
}

// Wire the one static listener. Called once by the entry module's boot after every section evaluates.
function initMetrics(){
  $('#metrics-refresh').addEventListener('click',loadMetrics);
}

export {loadMetrics, initMetrics};
