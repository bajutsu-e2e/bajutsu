// serve.metrics.mjs — the cross-target comparison view. A serve.*.mjs section module (BE-0247);
// imports its shared helpers from serve.core.mjs. Its body only defines — the one top-level listener
// is wired by initMetrics(), which the entry module (serve.author.mjs) calls after every section has
// evaluated.
import {$, esc, getJSON, isFetchError, FETCH_ERROR} from './serve.core.mjs';

// ---- Cross-target comparison (BE-0226 unit 3, repointed by BE-0404): the org's targets ranked ----
// Client-rendered from the unit-2 /api/metrics/targets model (JSON, not a server-rendered report
// like /stats) because the surface is interactive: sortable columns and a row that opens the
// target's run history. Read-only — it re-presents the deterministic verdicts `run` already
// decided, adding no LLM to the path. The tab is revealed only when the bound config declares more
// than one target (loadShared); a single-target config never sees it, since there is nothing to
// compare.
//
// Nothing here writes. The earlier per-row Activate rebound the deployment's config; a
// target is not a binding, so the comparison is now purely a read — which is what a reader ranking
// rows expected of it all along (#1720).
let metricsCache=[];
let metricsSort={key:'pass_rate',dir:'asc'};  // default: worst pass-rate first — the target to look at
// The target whose read-only detail is open, or null for the ranking. Doubles as the guard that
// drops an in-flight detail fetch whose reader has already gone back or opened another target.
let metricsDetail=null;

// Each column, and for the sortable ones whether "worst first" is ascending (pass-rate: low is bad)
// or descending (flaky-rate / duration: high is bad), so a first click surfaces the worst offender.
const METRIC_COLS=[
  {key:'name',label:'Target',cell:m=>metricOpen(m)},
  {key:'runs',label:'Runs',cell:m=>String(m.runs)},
  {key:'pass_rate',label:'Pass-rate',sortable:true,worst:'asc',cell:m=>metricCell(m,metricPct(m.pass_rate))},
  {key:'flaky_rate',label:'Flaky-rate',sortable:true,worst:'desc',cell:m=>metricCell(m,metricPct(m.flaky_rate))},
  {key:'duration_p50_s',label:'p50',sortable:true,worst:'desc',cell:m=>metricCell(m,metricSecs(m.duration_p50_s))},
  {key:'duration_p95_s',label:'p95',sortable:true,worst:'desc',cell:m=>metricCell(m,metricSecs(m.duration_p95_s))},
  {key:'trend',label:'Trend',cell:m=>metricSpark(m.trend)},
];

// The drill-down's control is a real button in the name cell, not the row itself. Giving the <tr>
// role="button" would have been the shorter route to keyboard access, but it overrides the row's
// implicit table semantics — a screen reader loses row/cell navigation over the very ranking this
// view exists to present. A button keeps the table intact and brings Enter and Space with it,
// natively.
function metricOpen(m){
  return `<button type="button" class="mopen" data-act="open" data-testid="metrics.open"`
    +` title="Open this target’s run history">${esc(m.name)}</button>`;
}

function metricPct(v){return Math.round(v*100)+'%'}
function metricSecs(v){return v.toFixed(1)+'s'}
// An unrun target's scalars are all 0.0 (a blank row, not a real zero) — dash them so it never
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
  // FETCH_ERROR, not an empty list (#1716): "no targets declared" and "the comparison couldn't be
  // read" are different situations and want different copy — the first tells you to bind a config,
  // the second to retry. An empty fallback here reported a healthy config as an empty one.
  const rows=await getJSON('/api/metrics/targets',FETCH_ERROR);
  if(isFetchError(rows)){host.innerHTML='<div class="mempty" data-testid="metrics.error">Couldn’t load the comparison. Refresh to retry.</div>';return}
  metricsCache=Array.isArray(rows)?rows:[];
  if(!metricsCache.length){host.innerHTML='<div class="mempty" data-testid="metrics.empty">No targets to compare — open a config that declares some.</div>';return}
  renderMetrics();
}

function sortedMetrics(){
  const {key,dir}=metricsSort;
  return metricsCache.slice().sort((a,b)=>{
    // An unrun target's scalars are all 0.0 (no signal, not a real worst) — keep it out of the
    // ranking so pass-rate-ascending never puts it ahead of a target that genuinely scores low.
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
  // event one of the row's own buttons raised — one guard on the row instead of a stopPropagation
  // each button could forget.
  host.querySelectorAll('tr.mrow').forEach(tr=>
    tr.addEventListener('click',e=>{if(!e.target.closest('button'))openMetricsDetail(tr.dataset.name)}));
}

// The read-only drill-down a row opens: that target's run history, newest first. Read from the
// ordinary run list, scoped server-side by the run's own `target` stamp (`ranTarget`, BE-0404
// unit 3) so the list is the same newest-N window of *this* target the ranking row was computed
// over — a client-side filter over a global window would let the detail contradict the row that
// opened it (#1718). The label filter is opened to every label, since the comparison ranks a
// config's targets rather than one partition of them.
async function openMetricsDetail(name){
  const host=$('#metrics-host');
  metricsDetail=name;
  host.innerHTML=`<div class="mdetail" data-testid="metrics.detail">`
    +`<div class="mdetailhead"><button type="button" class="cfgbtn" data-act="back" data-testid="metrics.detail-back">&#8592; Comparison</button>`
    +`<span class="mname">${esc(name)}</span><span class="muted" style="font-size:.8em">read-only — opening this history changes nothing</span></div>`
    +`<ul class="fslist mruns" data-testid="metrics.detail-runs"><li class="muted">Loading run history…</li></ul></div>`;
  host.querySelector('button[data-act="back"]').addEventListener('click',renderMetrics);
  const runs=await getJSON('/api/runs?label=*&ranTarget='+encodeURIComponent(name),FETCH_ERROR);
  // The reader may have gone back, or opened another target, while the fetch was in flight —
  // rendering into whatever is on screen now would attribute one target's runs to another.
  if(metricsDetail!==name)return;
  const ul=host.querySelector('[data-testid="metrics.detail-runs"]');
  if(!ul)return;
  if(isFetchError(runs)){ul.innerHTML='<li class="muted" data-testid="metrics.detail-error">Couldn’t load this target’s runs. Go back and retry.</li>';return}
  if(!Array.isArray(runs)||!runs.length){ul.innerHTML='<li class="muted" data-testid="metrics.detail-empty">No runs recorded for this target yet.</li>';return}
  ul.innerHTML=runs.map(r=>`<li data-testid="metrics.detail-run"><span class="dot ${r.ok?'ok':'ng'}"></span>`
    +`<span class="hid">${esc(r.id)}</span>`
    +`<span class="hsum">${r.passed}/${r.total}${r.scenarios&&r.scenarios.length?' · '+esc(r.scenarios.join(', ')):''}</span></li>`).join('');
}

// Wire the one static listener. Called once by the entry module's boot after every section evaluates.
function initMetrics(){
  $('#metrics-refresh').addEventListener('click',loadMetrics);
}

export {loadMetrics, initMetrics};
