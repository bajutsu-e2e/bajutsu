// serve.metrics.mjs — the hub's cross-project comparison view. A serve.*.mjs section module
// (BE-0247); imports its shared helpers from serve.core.mjs. Its body only defines — the one
// top-level listener is wired by initMetrics(), which the entry module (serve.author.mjs) calls
// after every section has evaluated.
import {$, esc, getJSON, switchProject} from './serve.core.mjs';

// ---- Cross-project comparison (BE-0226 unit 3): the hub's projects ranked side by side ----
// Client-rendered from the unit-2 /api/metrics/projects model (JSON, not a server-rendered report
// like /stats) because the surface is interactive: sortable columns and a row click that deep-links
// into that project's BE-0102 single-config dashboard through the hub switcher. Read-only — it
// re-presents the deterministic verdicts `run` already decided, adding no LLM to the path. The tab
// is revealed only when a real hub exists (loadProjects, >1 project); a single-config serve never
// sees it, since there is nothing to compare.
let metricsCache=[];
let metricsSort={key:'pass_rate',dir:'asc'};  // default: worst pass-rate first — the project to look at

// Each column, and for the sortable ones whether "worst first" is ascending (pass-rate: low is bad)
// or descending (flaky-rate / duration: high is bad), so a first click surfaces the worst offender.
const METRIC_COLS=[
  {key:'name',label:'Project',cell:m=>`<span class="mname">${esc(m.name)}</span>`},
  {key:'runs',label:'Runs',cell:m=>String(m.runs)},
  {key:'pass_rate',label:'Pass-rate',sortable:true,worst:'asc',cell:m=>metricCell(m,metricPct(m.pass_rate))},
  {key:'flaky_rate',label:'Flaky-rate',sortable:true,worst:'desc',cell:m=>metricCell(m,metricPct(m.flaky_rate))},
  {key:'duration_p50_s',label:'p50',sortable:true,worst:'desc',cell:m=>metricCell(m,metricSecs(m.duration_p50_s))},
  {key:'duration_p95_s',label:'p95',sortable:true,worst:'desc',cell:m=>metricCell(m,metricSecs(m.duration_p95_s))},
  {key:'trend',label:'Trend',cell:m=>metricSpark(m.trend)},
];

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
  const rows=await getJSON('/api/metrics/projects',[]);
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
  // A row click rebinds that project through the hub switcher and lands on its single-config dashboard.
  host.querySelectorAll('tr.mrow').forEach(tr=>tr.addEventListener('click',()=>switchProject(tr.dataset.name,{goStats:true})));
}

// Wire the one static listener. Called once by the entry module's boot after every section evaluates.
function initMetrics(){
  $('#metrics-refresh').addEventListener('click',loadMetrics);
}

export {loadMetrics, initMetrics};
