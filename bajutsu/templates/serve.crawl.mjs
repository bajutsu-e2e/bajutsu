// serve.crawl.mjs — the Crawl panel, its history, and the crawl-graph render / zoom-pan / lightbox.
// A serve.*.mjs section module (BE-0247); imports its shared helpers from serve.core.mjs. The
// largest section — the ~400-line graph lives here, kept together with its panel. Its body only
// defines; every top-level listener (the form, the graph interaction, the lightbox) is wired by
// initCrawl(), which the entry module (serve.author.mjs) calls after all sections evaluate.
import {$, esc, getJSON, setStatus, setBusy, streamJob, cancelJob, appendLine, startJob, openModal, closeModal, loadSims, wireHistoryList} from './serve.core.mjs';

// ---- Crawl: explore the app and watch the screen map grow live ----
let crawlPoll=null,crawlJobId=null,crawlRunId=null;
function crawlPickedUdids(){return [...$('#crawl-sims').querySelectorAll('.crawl-simck:checked')].map(c=>c.value)}
function onCrawlSimChange(){const n=crawlPickedUdids().length;if(n>0)$('#crawl-workers').value=n}

// ---- Crawl history (BE-0180): reopen a past crawl's screen map read-only ----
// A crawl writes no manifest.json (it has no pass/fail), so its runs never appear in the Replay tab's
// History; they're keyed on the screenmap.json each one streams and listed here instead. Selecting one
// reuses loadGraph — the same render path as a live crawl — and disables the live form so a past map
// can't be mistaken for a running one. Returning to the Form tab clears the selection.
let crawlHistoryRun=null,crawlLiveRun=null;  // crawlLiveRun parks a running crawl's id while history borrows the graph
function setCrawlFormDisabled(on){
  $('#panel-crawl').querySelectorAll('input,select,button').forEach(el=>{el.disabled=on});
}
// Per-crawl delete + bulk-select (BE-0239), wired via the same shared `wireHistoryList`
// (serve.core.mjs) the Replay history list uses. `crawlHistRuns` keeps the last-fetched rows so the
// delegated open handler (`onOpen`) can resolve an id back to its run object; `crawlSel` is the
// selector wireHistoryList returns (assigned in initCrawl), re-synced by loadCrawlHistory.
let crawlSel=null,crawlHistRuns=[];
async function loadCrawlHistory(){
  const runs=await getJSON('/api/crawl/runs',null);if(!runs)return;
  crawlHistRuns=runs;
  const tab=$('#crawl-histtab');if(tab)tab.textContent='History'+(runs.length?` (${runs.length})`:'');
  const ul=$('#crawl-history');
  if(!runs.length){ul.innerHTML='<li class="muted">no crawls yet</li>';if(crawlSel)crawlSel.sync();return}
  ul.innerHTML=runs.map(r=>`<li data-id="${esc(r.id)}" data-testid="crawl.history-item"${r.id===crawlHistoryRun?' class="sel"':''}><input type="checkbox" class="rowck" aria-label="select crawl for deletion" data-testid="crawl.history-select" value="${esc(r.id)}"><span class="hid">${esc(r.id)}</span><span class="hsum">${r.screens} screens · ${r.transitions} transitions${r.crashes?' · '+r.crashes+' crashes':''}</span><button type="button" class="rowdel" title="Delete this crawl" aria-label="Delete crawl" data-testid="crawl.history-delete">&#128465;</button></li>`).join('');
  if(crawlSel)crawlSel.sync();
}
// Link the crash/flow scenario files a run produced — plain links into the existing /runs/<id>/ mount,
// each opening the raw runnable YAML. Empty groups are omitted; the whole strip hides when there's none.
function crawlArtifactLinks(runId,label,dir,files){
  if(!files||!files.length)return '';
  const links=files.map(f=>`<a href="/runs/${encodeURIComponent(runId)}/${dir}/${encodeURIComponent(f)}" target="_blank" rel="noopener">${esc(f)}</a>`).join('');
  return `<div class="artgroup"><span class="artlabel">${label}</span>${links}</div>`;
}
function viewCrawlRun(r){
  // Park any live crawl's id (only on first entry, so switching between past runs keeps it parked): a
  // nulled crawlRunId makes history mode truly read-only — the streaming redraw's `if(crawlRunId)` guard
  // stops it clobbering this map, and resumePruned's guard blocks a resume against an unrelated run.
  if(crawlHistoryRun===null)crawlLiveRun=crawlRunId;
  crawlRunId=null;
  crawlHistoryRun=r.id;
  setCrawlFormDisabled(true);  // read-only framing: the live form can't drive a past map
  const badge=$('#crawl-pastbadge');badge.textContent='past crawl · '+r.id;badge.hidden=false;
  // Offer "continue exploring" (BE-0181) only when the run left untried operations — a completed
  // crawl has an empty frontier, so continuing it would find nothing. Reuses the live form's budget
  // inputs, so the user can raise Max screens / Max steps before continuing.
  const cont=$('#crawl-continue');
  if(r.frontier>0){cont.hidden=false;cont.textContent='▸ continue exploring · '+r.frontier+' screen'+(r.frontier>1?'s':'')+' left';cont.onclick=()=>continuePastRun(r.id)}
  else{cont.hidden=true;cont.onclick=null}
  setStatus($('#crawl-status'),'','');
  const art=$('#crawl-artifacts');
  const html=crawlArtifactLinks(r.id,'crashes','crashes',r.crashFiles)+crawlArtifactLinks(r.id,'flows','flows',r.flowFiles);
  art.innerHTML=html;art.hidden=!html;
  loadGraph(r.id);
}
// BE-0181: leave read-only history framing to actively explore an open past run — its whole
// remaining frontier (continue) or one pruned branch (resume). BE-0180 makes the history view
// read-only on purpose (a past map must not read as live, nor be driven by accident), so this is
// the deliberate un-lock: re-enable the form, drop the history-mode state, and arm crawlRunId — and
// only when the user explicitly asks to explore. Switches to the Form sub-tab so Stop is reachable.
function armPastRun(runId){
  crawlHistoryRun=null;crawlLiveRun=null;crawlRunId=runId;
  setCrawlFormDisabled(false);
  $('#crawl-pastbadge').hidden=true;$('#crawl-continue').hidden=true;
  $('#crawl-artifacts').hidden=true;$('#crawl-artifacts').innerHTML='';
  document.querySelectorAll('#view-crawl .tab').forEach(t=>t.classList.toggle('active',t.dataset.tab==='crawlform'));
  $('#panel-crawl').hidden=false;$('#crawl-panel-history').hidden=true;
}
// Continue a past run's whole remaining frontier live: re-launch the crawl against the SAME run with
// continue:true — the engine reconstructs every screen with untried ops from its saved map and keeps
// exploring, appending to it. --workers/--udid make the continuation parallel, unlike a resume.
async function continuePastRun(runId){
  armPastRun(runId);
  if(crawlPoll)crawlPoll.close();
  setBusy($('#crawl-go'),$('#crawl-stop'),true,'Continuing…');
  $('#crawl-out').textContent='';
  setStatus($('#crawl-status'),'','run');
  const r=await fetch('/api/crawl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    target:$('#crawl-target').value,udid:crawlPickedUdids().join(',')||'booted',
    workers:parseInt($('#crawl-workers').value,10)||1,
    maxScreens:parseInt($('#crawl-maxscreens').value,10)||50,maxSteps:parseInt($('#crawl-maxsteps').value,10)||200,
    alertHandling:$('#crawl-nodismiss').checked?false:undefined,headed:$('#crawl-headed').checked||undefined,
    runId:runId,continue:true})});
  const {jobId,runId:started,error}=await r.json();
  if(error){setStatus($('#crawl-status'),error,'ng');setBusy($('#crawl-go'),$('#crawl-stop'),false);return;}
  crawlJobId=jobId;crawlRunId=started;
  crawlPoll=streamJob(jobId,line=>{
    appendLine($('#crawl-out'),line);
    if(crawlRunId)loadGraph(crawlRunId);
  },crawlDone);
}
// Leave history mode: re-enable the live form and reset the map/plan/links to their pre-crawl state,
// the same clean slate crawlDone leaves for the next run.
function exitCrawlHistory(){
  if(crawlHistoryRun===null)return;
  crawlHistoryRun=null;
  crawlRunId=crawlLiveRun;crawlLiveRun=null;  // hand the graph back to the live crawl, if one was running
  setCrawlFormDisabled(false);
  $('#crawl-pastbadge').hidden=true;$('#crawl-continue').hidden=true;
  $('#crawl-artifacts').hidden=true;$('#crawl-artifacts').innerHTML='';
  $('#crawl-counts').textContent='';setStatus($('#crawl-status'),'','');
  $('#crawl-graph').innerHTML='<div class="empty">Start a crawl to watch the screen map grow.</div>';
  $('#crawl-plan').innerHTML='<div class="empty">The plan tree grows as the crawl explores.</div>';
  $('#crawl-planpct').textContent='';$('#crawl-planfill').style.width='0';
}
function showCrawlTab(name){
  document.querySelectorAll('#view-crawl .tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===name));
  $('#panel-crawl').hidden=name!=='crawlform';$('#crawl-panel-history').hidden=name!=='crawlhistory';
  if(name==='crawlform')exitCrawlHistory();else loadCrawlHistory();
}

async function crawlDone(j){
  crawlPoll=null;crawlJobId=null;setBusy($('#crawl-go'),$('#crawl-stop'),false);
  if(crawlRunId)await loadGraph(crawlRunId);  // final redraw
  if(j.cancelled){setStatus($('#crawl-status'),'stopped','ng');return}
  setStatus($('#crawl-status'),j.ok?'done ✓':'failed', j.ok?'ok':'ng');
}
async function loadGraph(runId){
  const data=await getJSON('/runs/'+runId+'/screenmap.json',null);if(!data)return;
  renderGraph(data,runId);
  renderPlan(data);  // the right-column plan tree, kept in step with the transition graph
}
// The exploration-plan tree (right column), a separate view from the transition graph: each screen
// branches into its operations, marked explored (✅, already traversed) or pending (⏳, the frontier
// — e.g. a vision-located tab queued to tap). It grows live as the crawl discovers screens and the
// frontier shrinks, so a watcher sees coverage build up and which paths have been walked. Rooted at
// the entry screen and recursing through explored edges; a screen is expanded once (a later
// reference shows "↩ shown above") so the tree stays finite despite cycles.
function shortLabel(n){const ids=n.ids||[];return ids.length?ids[0]+(ids.length>1?' +'+(ids.length-1):''):n.fingerprint.slice(0,7)}
function renderPlan(data){
  const box=$('#crawl-plan');
  const nodes=data.nodes||[],edges=data.edges||[],plan=data.plan||{};
  if(!nodes.length){box.innerHTML='<div class="empty">The plan tree grows as the crawl explores.</div>';return}
  // Operations the engine pruned as duplicate global controls (a tab/nav explored once from its
  // owner screen), grouped by the screen where they were skipped — shown struck through, and tappable
  // to resume exploring that branch.
  const prunedBy=new Map();
  (data.pruned||[]).forEach(p=>{(prunedBy.get(p.src)||prunedBy.set(p.src,[]).get(p.src)).push(p)});
  const idx=new Map(nodes.map(n=>[n.fingerprint,n]));
  const out=new Map(nodes.map(n=>[n.fingerprint,[]]));
  edges.forEach(e=>{if(out.has(e.src))out.get(e.src).push(e)});
  const incoming=new Set(edges.filter(e=>e.src!==e.dst).map(e=>e.dst));
  let roots=nodes.filter(n=>!incoming.has(n.fingerprint)).map(n=>n.fingerprint);
  if(!roots.length)roots=[nodes[0].fingerprint];
  const seen=new Set();
  function branch(fp,depth){
    const n=idx.get(fp);if(!n)return '';
    if(seen.has(fp))
      return `<div class="plrow seen" style="--d:${depth}"><span class="pls">↩</span>${esc(shortLabel(n))} <span class="plmut">(shown above)</span></div>`;
    seen.add(fp);
    const outs=out.get(fp)||[],pend=plan[fp]||[],total=outs.length+pend.length;
    // ● fully explored vs ◔ frontier remaining — whether this screen has been fully traversed.
    const full=pend.length===0;
    let h=`<div class="plrow scr${full?' full':''}" data-fp="${esc(fp)}" style="--d:${depth}" title="${esc(fp.slice(0,7))} — click to enlarge">`+
      `<span class="pls">${full?'●':'◔'}</span><b>${esc(shortLabel(n))}</b> <span class="plcov">${outs.length}/${total}</span></div>`;
    outs.forEach(e=>{const alert=(e.alert||[]).length?' 🛡️':'';
      h+=`<div class="plrow op done" style="--d:${depth+1}"><span class="pls">✅</span>${esc(e.action)}${alert} <span class="plarrow">→</span></div>`;
      h+=branch(e.dst,depth+2)});
    pend.forEach(op=>{h+=`<div class="plrow op pend" style="--d:${depth+1}"><span class="pls">⏳</span>${esc(op)}</div>`});
    // Pruned global ops: struck through, with the owner screen they were explored from. Tapping one
    // resumes exploring that branch from this screen (data-src/data-key drive the resume request).
    if(prunePlan)(prunedBy.get(fp)||[]).forEach(p=>{
      h+=`<div class="plrow op pruned" data-src="${esc(fp)}" data-key="${esc(p.key)}" style="--d:${depth+1}" title="pruned — explored once from ${esc((p.owner||'').slice(0,7))}; tap to resume exploring this branch from here"><span class="pls">✂</span>${esc(p.action)} <span class="plmut">↩ ${esc((p.owner||'').slice(0,7))}</span></div>`;
    });
    return h;
  }
  let html='';roots.forEach(r=>html+=branch(r,0));
  nodes.forEach(n=>{if(!seen.has(n.fingerprint))html+=branch(n.fingerprint,0)});  // unrooted, just in case
  box.innerHTML=`<div class="plantree">${html}</div>`;
}
// Lay the screen map out as a BFS-layered graph of *units*. A unit is a label+info box — the
// screen's identifiers / action / blocked / planned counts, with a small screenshot thumbnail that
// hides itself when the shot is missing — not just a bare image. Screens that are the same UI in
// different states (identical id set, but a form empty vs filled, a switch off vs on) are one
// collapsible group: drawn as a single unit until clicked, then expanded into its individual state
// nodes, with every transition still routed by correct arrows (intra-group state changes become a
// self-loop while collapsed). The plan overlay marks each unit with its still-untried operation
// count — the live exploration frontier, refreshed every poll. Edges are one SVG layer; unit boxes
// are HTML on top. The view (zoom / pan), the data, and which groups are expanded all persist
// across the per-poll re-render so the layout stays put as the map grows.
let crawlGraphData=null,crawlGraphRunId=null;
let prunePlan=true;  // collapse a global op (e.g. a tab switch) repeated across screens to one entry
const expandedGroups=new Set();  // group keys the user expanded in the graph (kept across redraws)
const nodeOverrides=new Map();  // unit-id → {x,y} manual positions (kept across redraws, cleared by realign)
const NODE_W=176,NODE_H=290;  // graph card dimensions (px), shared by renderGraph and liveEdges
const gview={x:0,y:0,k:1};  // pan (px) + zoom (scale), applied as a transform on the graph layer
function shotURL(runId,fp){return `/runs/${encodeURIComponent(runId)}/screens/${encodeURIComponent(fp)}.png`}
// Screens with the same set of accessibility identifiers are the same UI in different states; key
// them by that set so they group. A screen with no ids stands alone (keyed by its fingerprint).
// Two screens are "the same screen in a different transient state" — a form empty vs filled, a
// switch toggled, or an alert/overlay adding a few elements — when their identifier sets overlap
// almost entirely: the smaller set is nearly contained in the larger (>=90%) and they still share a
// solid fraction overall (Jaccard >=40%, so a small screen isn't swallowed by a large superset).
// Screens with too few ids (the structural-fingerprint fallback) never fuzzy-merge. The exact
// fingerprints stay distinct — only the *display* groups them; the crawl still explores every state.
function sameScreen(a,b){
  const A=a.ids||[],B=b.ids||[];if(A.length<2||B.length<2)return false;
  const sb=new Set(B);let inter=0;A.forEach(x=>{if(sb.has(x))inter++});
  if(!inter)return false;
  const uni=A.length+B.length-inter,mn=Math.min(A.length,B.length);
  return inter/mn>=0.9&&inter/uni>=0.4;
}
// Group nodes by that relation (union-find), so the same screen's states collapse into one unit even
// when an overlay or a value changes which ids are present. Each group is keyed by its smallest
// fingerprint, a stable key so the expand/collapse state survives as more states are discovered.
function buildGroups(nodes){
  const parent=nodes.map((_,i)=>i);
  const find=i=>{while(parent[i]!==i){parent[i]=parent[parent[i]];i=parent[i]}return i};
  for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++)
    if(sameScreen(nodes[i],nodes[j]))parent[find(i)]=find(j);
  const byRoot=new Map();
  nodes.forEach((n,i)=>{const r=find(i);(byRoot.get(r)||byRoot.set(r,[]).get(r)).push(n)});
  return [...byRoot.values()];
}
function redrawGraph(){if(crawlGraphData)renderGraph(crawlGraphData,crawlGraphRunId)}
// Untried operations the plan still holds for a unit (a group sums its member states) — the live
// exploration frontier, e.g. a vision-located tab queued to be tapped next.
function plannedOps(u,plan){
  return u.kind==='group'
    ?u.members.reduce((a,m)=>a.concat(plan[m.fingerprint]||[]),[])
    :(plan[u.node.fingerprint]||[]).slice();
}
// One unit box: a collapsed group (▸, click to expand), an expanded member (▾ to collapse), or a
// lone screen — each showing a short id label, an info line, a plan badge, and a thumbnail.
function unitHTML(u,p,plan,runId,NW,NH){
  const ops=plannedOps(u,plan),planned=ops.length;
  // Tooltip lists the queued operations (a vision-located tab shows as "tap tab '…'").
  const tip=planned?`${planned} untried operation(s) — the live frontier:\n`+ops.join('\n'):'';
  const badge=planned?`<span class="gplan" title="${esc(tip)}">⏳ ${planned}</span>`:'';
  // A frontier unit still has untried operations — the leading edge of exploration, coloured apart.
  const front=planned?' frontier':'';
  const style=`left:${p.x}px;top:${p.y}px;width:${NW}px;height:${NH}px`;
  // A real <img> (auto-hiding on error) so the screenshot loads reliably and reads at a glance.
  const shotImg=fp=>`<img class="gshot" src="${shotURL(runId,fp)}" alt="" loading="lazy" onerror="this.classList.add('missing')">`;
  if(u.kind==='group'){
    const ids=u.members[0].ids||[];
    const label=ids.length?ids[0]+(ids.length>1?' +'+(ids.length-1):''):'screen';
    return `<div class="gnode ggroup${front}" data-group="${esc(u.key)}" data-uid="${esc(u.id)}" style="${style}" title="Same UI in ${u.members.length} states — click to expand">`+
      shotImg(u.members[0].fingerprint)+  // the first state's shot, as a representative preview
      `<button class="gexpand" type="button" data-group="${esc(u.key)}" title="Expand ${u.members.length} states">▸ ${u.members.length}</button>`+
      `<div class="gmeta"><div class="ghead"><span class="gtitle">${esc(label)}</span>${badge}</div>`+
      `<div class="gsub">${u.members.length} states · ${ids.length} ids</div></div></div>`;
  }
  const n=u.node,ids=n.ids||[];
  const cls='gnode'+(n.kind==='structural'?' structural':'')+(u.kind==='member'?' member':'')+front;
  const label=ids.length?ids[0]+(ids.length>1?' +'+(ids.length-1):''):n.fingerprint.slice(0,7);
  const info=`${ids.length} ids · ${(n.actions||[]).length} actions`+((n.blocked||[]).length?` · 🔒 ${n.blocked.length}`:'');
  const collapse=u.kind==='member'?`<button class="gcollapse" type="button" data-group="${esc(u.key)}" title="Collapse group">▾</button>`:'';
  return `<div class="${cls}" data-fp="${esc(n.fingerprint)}" data-uid="${esc(u.id)}" style="${style}" title="${esc(n.fingerprint.slice(0,7))} — click to enlarge">`+
    shotImg(n.fingerprint)+collapse+
    `<div class="gmeta"><div class="ghead"><span class="gtitle">${esc(label)}${n.kind==='structural'?' ~':''}</span>${badge}</div>`+
    `<div class="gsub">${esc(info)}</div></div></div>`;
}
function renderGraph(data,runId){
  crawlGraphData=data;crawlGraphRunId=runId;
  const nodes=data.nodes||[],edges=data.edges||[],crashes=data.crashes||[],alerts=data.alerts||[],plan=data.plan||{};
  const planned=Object.values(plan).reduce((s,ops)=>s+ops.length,0);
  // The reason the crawl stopped (set only once it finishes); shown after the counts.
  const why={completed:'completed',max_screens:'screen limit reached',max_steps:'step limit reached'}[data.stop_reason];
  // Progress against the plan: explored operations (each transition or crash is one tried) over the
  // total (explored + still-pending). 100% once nothing is pending (the frontier is exhausted).
  const explored=edges.length+crashes.length,totalOps=explored+planned;
  const pct=totalOps?Math.round(explored/totalOps*100):(data.stop_reason?100:0);
  $('#crawl-counts').textContent=`${nodes.length} screens · ${edges.length} transitions · ${crashes.length} crashes`+
    (alerts.length?` · ${alerts.length} alerts dismissed`:'')+(planned?` · ${planned} planned`:'')+
    (nodes.length?` · ${pct}% explored`:'')+(why?' · '+why:'');
  const pf=$('#crawl-planfill'),pp=$('#crawl-planpct');
  if(pf)pf.style.width=pct+'%';
  if(pp)pp.textContent=pct+'%';
  const box=$('#crawl-graph');
  if(!nodes.length){box.innerHTML='<div class="empty">Reaching the first screen…</div>';return;}
  // Group same-screen states by UI, then resolve to laid-out units: a collapsed group is one unit; an
  // expanded group (or a lone screen) contributes one unit per state. unitOf maps each fingerprint to
  // its unit. Re-run every render so the graph re-optimizes as the plan grows.
  const units=[],unitOf=new Map();
  buildGroups(nodes).forEach(members=>{
    members.sort((a,b)=>a.fingerprint<b.fingerprint?-1:1);
    const key=members[0].fingerprint;  // stable group key (smallest fingerprint)
    if(members.length>1&&!expandedGroups.has(key)){
      const id='g:'+key;units.push({id,kind:'group',key,members});members.forEach(m=>unitOf.set(m.fingerprint,id));
    }else members.forEach(m=>{const id='n:'+m.fingerprint;
      units.push({id,kind:members.length>1?'member':'node',key,node:m});unitOf.set(m.fingerprint,id)});
  });
  // Aggregate transitions between units (intra-unit state changes collapse to a self-loop); a pair
  // is amber if any of its underlying edges tapped through an OS alert.
  const uedges=new Map();
  edges.forEach(e=>{const su=unitOf.get(e.src),du=unitOf.get(e.dst);if(!su||!du)return;
    const k=su+'>'+du,cur=uedges.get(k)||{src:su,dst:du,alert:false};
    if((e.alert||[]).length)cur.alert=true;uedges.set(k,cur)});
  // Depth = BFS distance over units from a root (a unit nothing leads into; fall back to the first).
  const adj=new Map(units.map(u=>[u.id,[]]));
  uedges.forEach(e=>{if(e.src!==e.dst&&adj.has(e.src))adj.get(e.src).push(e.dst)});
  const incoming=new Set([...uedges.values()].filter(e=>e.src!==e.dst).map(e=>e.dst));
  const depth=new Map();
  let roots=units.filter(u=>!incoming.has(u.id)).map(u=>u.id);
  if(!roots.length)roots=[units[0].id];
  const q=[...roots];roots.forEach(r=>depth.set(r,0));
  while(q.length){const f=q.shift(),d=depth.get(f);(adj.get(f)||[]).forEach(t=>{if(!depth.has(t)){depth.set(t,d+1);q.push(t)}})}
  units.forEach(u=>{if(!depth.has(u.id))depth.set(u.id,0)});
  const layers=[];units.forEach(u=>{const d=depth.get(u.id);(layers[d]||(layers[d]=[])).push(u)});
  // Layout: vertical cards — a large screenshot on top, label+info (and any group button) below.
  // Wider than tall-text needs so labels wrap rather than truncate.
  const NW=NODE_W,NH=NODE_H,COLW=250,ROWH=NH+30,PAD=24;
  const pos=new Map();let maxRows=1;
  layers.forEach((layer,d)=>{if(!layer)return;maxRows=Math.max(maxRows,layer.length);layer.forEach((u,i)=>pos.set(u.id,{x:PAD+d*COLW,y:PAD+i*ROWH}))});
  nodeOverrides.forEach((p,id)=>{if(pos.has(id))pos.set(id,p)});
  const W=PAD*2+(layers.length-1)*COLW+NW,H=PAD*2+(maxRows-1)*ROWH+NH;
  // Edge layer (SVG), sized to the same coordinate space the unit boxes are positioned in.
  let svg=`<svg class="graphsvg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`;
  svg+=`<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="var(--mut)"/></marker></defs>`;
  uedges.forEach(e=>{const a=pos.get(e.src),b=pos.get(e.dst);if(!a||!b)return;
    const cls='edge'+(e.alert?' alert':''),ends=`data-a="${esc(e.src)}" data-b="${esc(e.dst)}"`;
    if(e.src===e.dst){const x=a.x+NW,y=a.y+NH/2;svg+=`<path class="${cls} selfloop" ${ends} d="M${x},${y-8} C${x+34},${y-26} ${x+34},${y+26} ${x},${y+8}" marker-end="url(#arrow)"/>`;
      if(e.alert)svg+=`<text class="edgealert" x="${x+30}" y="${y}">🛡️</text>`;return}
    const x1=a.x+NW,y1=a.y+NH/2,x2=b.x,y2=b.y+NH/2,mx=(x1+x2)/2;
    svg+=`<path class="${cls}" ${ends} d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" marker-end="url(#arrow)"/>`;
    // Mark the midpoint with a shield so it's clear the step taps through a system alert.
    if(e.alert)svg+=`<text class="edgealert" x="${mx}" y="${(y1+y2)/2-4}">🛡️</text>`});
  svg+='</svg>';
  // Frame the member states of each expanded group, so it reads as one screen even when its states
  // land in different layers. Drawn behind the unit boxes; a small chip labels the screen + count.
  const memberGroups=new Map();
  units.forEach(u=>{if(u.kind==='member')(memberGroups.get(u.key)||memberGroups.set(u.key,[]).get(u.key)).push(u)});
  let frames='';
  memberGroups.forEach(ms=>{
    if(ms.length<2)return;
    let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
    ms.forEach(u=>{const p=pos.get(u.id);if(!p)return;x0=Math.min(x0,p.x);y0=Math.min(y0,p.y);x1=Math.max(x1,p.x+NW);y1=Math.max(y1,p.y+NH)});
    if(!isFinite(x0))return;
    const F=14,lbl=(ms[0].node.ids||[])[0]||ms[0].node.fingerprint.slice(0,7);
    frames+=`<div class="gframe" style="left:${x0-F}px;top:${y0-F}px;width:${x1-x0+F*2}px;height:${y1-y0+F*2}px"><span class="gframelbl">${esc(lbl)} · ${ms.length} states</span></div>`;
  });
  // Unit boxes (HTML, absolutely positioned over the edges and group frames).
  let tiles='';units.forEach(u=>{tiles+=unitHTML(u,pos.get(u.id),plan,runId,NW,NH)});
  box.innerHTML=`<div class="graphpan"><div class="graphwrap" style="width:${W}px;height:${H}px">${svg}${frames}${tiles}</div></div>`;
  applyView();
}
// ---- zoom / pan ----
// Pan is a translate on the outer layer (GPU-composited, so dragging stays smooth); zoom is CSS
// `zoom` on the inner layer, not `transform: scale`, so the browser re-lays-out and re-rasterizes
// text and borders at the target magnification — crisp instead of a blurred upscaled bitmap. The two
// compose to the same mapping (screen = pan + zoom·content), so the zoomBy/pan math is unchanged.
function applyView(){
  const pan=$('.graphpan'),w=$('.graphwrap');
  if(pan)pan.style.transform=`translate(${gview.x}px,${gview.y}px)`;
  if(w)w.style.zoom=gview.k;
}
function zoomBy(factor,cx,cy){
  const r=$('#crawl-graph').getBoundingClientRect();
  // Keep the point under (cx,cy) fixed while scaling about it.
  const px=(cx-r.left-gview.x)/gview.k,py=(cy-r.top-gview.y)/gview.k;
  gview.k=Math.min(3,Math.max(0.2,gview.k*factor));
  gview.x=cx-r.left-px*gview.k;gview.y=cy-r.top-py*gview.k;applyView();
}
function resetView(){gview.x=0;gview.y=0;gview.k=1;applyView()}
// Graph pointer interaction (wheel-zoom, drag-pan, node drag, touch pan/pinch, hover highlight).
// Wired as one closure over its own drag state; called by initCrawl once the graph panel exists.
function initGraphInteraction(){
  const box=$('#crawl-graph');let drag=null,nodeDrag=null,moved=false;
  const NDW=NODE_W,NDH2=NODE_H/2;
  function liveEdges(wrap,uid,nx,ny){
    wrap.querySelectorAll('.edge').forEach(p=>{
      const isA=p.dataset.a===uid,isB=p.dataset.b===uid;if(!isA&&!isB)return;
      if(p.dataset.a===p.dataset.b){const x=nx+NDW,y=ny+NDH2;p.setAttribute('d',`M${x},${y-8} C${x+34},${y-26} ${x+34},${y+26} ${x},${y+8}`);
        const t=p.nextElementSibling;if(t&&t.classList.contains('edgealert')){t.setAttribute('x',x+30);t.setAttribute('y',y)}return}
      const oid=isA?p.dataset.b:p.dataset.a,oel=wrap.querySelector(`.gnode[data-uid="${CSS.escape(oid)}"]`);if(!oel)return;
      const ox=parseFloat(oel.style.left),oy=parseFloat(oel.style.top);
      const ax=isA?nx:ox,ay=isA?ny:oy,bx=isB?nx:ox,by=isB?ny:oy;
      const x1=ax+NDW,y1=ay+NDH2,x2=bx,y2=by+NDH2,mx=(x1+x2)/2;
      p.setAttribute('d',`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
      const t=p.nextElementSibling;if(t&&t.classList.contains('edgealert')){t.setAttribute('x',mx);t.setAttribute('y',(y1+y2)/2-4)}});
  }
  box.addEventListener('wheel',e=>{if(!$('.graphwrap'))return;e.preventDefault();zoomBy(e.deltaY<0?1.1:1/1.1,e.clientX,e.clientY)},{passive:false});
  box.addEventListener('mousedown',e=>{if(!$('.graphwrap'))return;
    const gn=e.target.closest('.gnode');
    if(gn&&gn.dataset.uid){nodeDrag={uid:gn.dataset.uid,el:gn,sx:e.clientX,sy:e.clientY,ox:parseFloat(gn.style.left),oy:parseFloat(gn.style.top)};moved=false;return}
    drag={x:e.clientX,y:e.clientY,ox:gview.x,oy:gview.y};moved=false;box.classList.add('panning')});
  window.addEventListener('mousemove',e=>{
    if(nodeDrag){const dx=(e.clientX-nodeDrag.sx)/gview.k,dy=(e.clientY-nodeDrag.sy)/gview.k;
      if(Math.abs(e.clientX-nodeDrag.sx)+Math.abs(e.clientY-nodeDrag.sy)>3)moved=true;
      const nx=nodeDrag.ox+dx,ny=nodeDrag.oy+dy;nodeDrag.el.style.left=nx+'px';nodeDrag.el.style.top=ny+'px';
      const w=$('.graphwrap');if(w)liveEdges(w,nodeDrag.uid,nx,ny);return}
    if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>3)moved=true;gview.x=drag.ox+dx;gview.y=drag.oy+dy;applyView()});
  window.addEventListener('mouseup',()=>{
    if(nodeDrag){if(moved){nodeOverrides.set(nodeDrag.uid,{x:parseFloat(nodeDrag.el.style.left),y:parseFloat(nodeDrag.el.style.top)});nodeDrag=null;redrawGraph();return}nodeDrag=null;return}
    if(drag){drag=null;box.classList.remove('panning')}});
  // Touch (BE-0072 + BE-0095): one finger on background pans, on a node drags it; two fingers
  // pinch-zoom. Both reuse the existing translate + `zoom` math. A touch that didn't drift still
  // falls through to `click` for expand/collapse/lightbox.
  let tpan=null,pinch=null;
  const dist=(a,b)=>Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY);
  const mid=(a,b)=>({x:(a.clientX+b.clientX)/2,y:(a.clientY+b.clientY)/2});
  box.addEventListener('touchstart',e=>{if(!$('.graphwrap'))return;
    if(e.touches.length===1){const t=e.touches[0],gn=e.target.closest('.gnode');
      if(gn&&gn.dataset.uid){nodeDrag={uid:gn.dataset.uid,el:gn,sx:t.clientX,sy:t.clientY,ox:parseFloat(gn.style.left),oy:parseFloat(gn.style.top)};pinch=null;tpan=null;moved=false;return;}
      tpan={x:t.clientX,y:t.clientY,ox:gview.x,oy:gview.y};pinch=null;moved=false;box.classList.add('panning');}
    else if(e.touches.length===2){const m=mid(e.touches[0],e.touches[1]);pinch={d:dist(e.touches[0],e.touches[1]),cx:m.x,cy:m.y};tpan=null;nodeDrag=null;moved=true;}
  },{passive:true});
  box.addEventListener('touchmove',e=>{if(!$('.graphwrap'))return;
    if(nodeDrag&&e.touches.length===1){e.preventDefault();const t=e.touches[0],dx=(t.clientX-nodeDrag.sx)/gview.k,dy=(t.clientY-nodeDrag.sy)/gview.k;
      if(Math.abs(t.clientX-nodeDrag.sx)+Math.abs(t.clientY-nodeDrag.sy)>3)moved=true;
      const nx=nodeDrag.ox+dx,ny=nodeDrag.oy+dy;nodeDrag.el.style.left=nx+'px';nodeDrag.el.style.top=ny+'px';
      const w=$('.graphwrap');if(w)liveEdges(w,nodeDrag.uid,nx,ny);}
    else if(pinch&&e.touches.length===2){e.preventDefault();const d=dist(e.touches[0],e.touches[1]),m=mid(e.touches[0],e.touches[1]);
      if(pinch.d>0)zoomBy(d/pinch.d,m.x,m.y);pinch.d=d;pinch.cx=m.x;pinch.cy=m.y;}
    else if(tpan&&e.touches.length===1){e.preventDefault();const t=e.touches[0],dx=t.clientX-tpan.x,dy=t.clientY-tpan.y;
      if(Math.abs(dx)+Math.abs(dy)>3)moved=true;gview.x=tpan.ox+dx;gview.y=tpan.oy+dy;applyView();}
  },{passive:false});
  const endTouch=()=>{
    if(nodeDrag&&moved){nodeOverrides.set(nodeDrag.uid,{x:parseFloat(nodeDrag.el.style.left),y:parseFloat(nodeDrag.el.style.top)});nodeDrag=null;redrawGraph();tpan=null;pinch=null;box.classList.remove('panning');return;}
    nodeDrag=null;tpan=null;pinch=null;box.classList.remove('panning');};
  window.addEventListener('touchend',e=>{if(e.touches.length===0)endTouch();});
  // A touchcancel (gesture takeover, OS context switch) aborts the gesture outright — reset the same
  // way as touchend so a cancelled pan/pinch can't leave stuck state that breaks the next tap/pan.
  window.addEventListener('touchcancel',endTouch);
  // A click that wasn't a drag either expands/collapses a group or opens a screen's lightbox.
  box.addEventListener('click',e=>{if(moved){moved=false;return}
    const col=e.target.closest('.gcollapse');
    if(col){expandedGroups.delete(col.dataset.group);redrawGraph();return}
    const grp=e.target.closest('.ggroup');
    if(grp){expandedGroups.add(grp.dataset.group);redrawGraph();return}
    const node=e.target.closest('.gnode');if(node&&node.dataset.fp)openShot(node.dataset.fp)});
  // Hovering a node lights up only the edges touching it and dims the rest, so a busy web of lines
  // becomes readable on demand. Edges carry data-a/data-b (their endpoint unit ids); the node carries
  // data-uid. Skipped while dragging (pan or node) so a gesture doesn't flicker the highlight.
  box.addEventListener('mouseover',e=>{if(drag||nodeDrag)return;const n=e.target.closest('.gnode');if(!n||!n.dataset.uid)return;
    const wrap=$('.graphwrap');if(!wrap)return;const uid=n.dataset.uid;
    wrap.classList.add('hl');
    wrap.querySelectorAll('.edge').forEach(p=>p.classList.toggle('hot',p.dataset.a===uid||p.dataset.b===uid));
    wrap.querySelectorAll('.gnode').forEach(g=>g.classList.toggle('faded',g!==n&&!isAdjacent(wrap,uid,g.dataset.uid)))});
  box.addEventListener('mouseout',e=>{const n=e.target.closest('.gnode');if(!n)return;
    const wrap=$('.graphwrap');if(!wrap)return;
    wrap.classList.remove('hl');wrap.querySelectorAll('.edge.hot').forEach(p=>p.classList.remove('hot'));
    wrap.querySelectorAll('.gnode.faded').forEach(g=>g.classList.remove('faded'))});
}
// Whether unit `other` is directly connected to `uid` by some edge (either direction).
function isAdjacent(wrap,uid,other){
  if(!other)return false;
  for(const p of wrap.querySelectorAll('.edge'))
    if((p.dataset.a===uid&&p.dataset.b===other)||(p.dataset.b===uid&&p.dataset.a===other))return true;
  return false;
}
// Tap a pruned branch to resume exploring it: re-launch the crawl against the SAME run, seeded to
// replay to that screen and perform the pruned op, appending whatever it finds to the live map.
async function resumePruned(src,key){
  // A pruned branch tapped while viewing a past run (BE-0180 read-only history) is an explicit
  // request to explore it: adopt that run and un-lock, the same deliberate transition continue makes
  // (BE-0181). Live crawl → crawlRunId is already set; a fresh Crawl tab with neither → nothing to do.
  const runId=crawlRunId||crawlHistoryRun;
  if(!runId){setStatus($('#crawl-status'),'no active run to resume','ng');return}
  if(crawlHistoryRun)armPastRun(runId);
  if(crawlPoll)crawlPoll.close();
  setBusy($('#crawl-go'),$('#crawl-stop'),true,'Resuming…');
  setStatus($('#crawl-status'),'','run');
  const r=await fetch('/api/crawl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    target:$('#crawl-target').value,udid:crawlPickedUdids()[0]||'booted',  // a resume is a single-branch walk
    maxScreens:parseInt($('#crawl-maxscreens').value,10)||50,maxSteps:parseInt($('#crawl-maxsteps').value,10)||200,
    alertHandling:$('#crawl-nodismiss').checked?false:undefined,headed:$('#crawl-headed').checked||undefined,
    runId:runId,resumeSrc:src,resumeKey:key})});
  const {jobId,runId:started,error}=await r.json();
  if(error){setStatus($('#crawl-status'),error,'ng');setBusy($('#crawl-go'),$('#crawl-stop'),false);return}
  crawlJobId=jobId;crawlRunId=started;
  crawlPoll=streamJob(jobId,line=>{
    appendLine($('#crawl-out'),line);
    if(crawlRunId)loadGraph(crawlRunId);
  },crawlDone);
}

// ---- lightbox: enlarge a screen's shot and step through the transitions (before / after) ----
let shotFp=null;  // the screen currently shown, so prev/next can walk the graph from it
// One row per transition: the action and the other screen's thumbnail, clickable to step there.
// For outgoing transitions a `targets` map carries the tap rectangle on *this* screen, attached as
// data-rect so hovering the row highlights where the tap lands on the screenshot.
function transitionRows(list,field,targets){
  return list.map(e=>{const fp=e[field],alert=(e.alert||[]).length?' 🛡️':'';
    const rect=targets&&targets[e.action],rd=rect?` data-rect="${rect.join(',')}"`:'';
    return `<button class="nextrow" data-fp="${esc(fp)}"${rd}>`+
      `<img src="${shotURL(crawlGraphRunId,fp)}" alt="" onerror="this.style.visibility='hidden'">`+
      `<span class="nxtxt"><span class="nxa">${esc(e.action)}${alert}</span>`+
      `<span class="nxf">${field==='dst'?'→':'←'} ${esc(fp.slice(0,7))}${fp===shotFp?' (self)':''}${alert?' · via OS alert':''}</span></span></button>`;
  }).join('');
}
function openShot(fp){
  if(!crawlGraphData)return;
  const nodes=crawlGraphData.nodes||[],edges=crawlGraphData.edges||[];
  const node=nodes.find(n=>n.fingerprint===fp);if(!node)return;
  shotFp=fp;hideHi();
  $('#shotimg').src=shotURL(crawlGraphRunId,fp);
  $('#shottitle').textContent=`${fp.slice(0,7)}${node.kind==='structural'?' (structural)':''} · ${(node.ids||[]).length} ids · ${(node.actions||[]).length} actions`;
  const out=edges.filter(e=>e.src===fp),inc=edges.filter(e=>e.dst===fp);
  // Where each operation taps on this screen, normalized to the screenshot — for the hover highlight.
  const targets=node.targets||{};
  // Arrows step to the first transition before / after this screen (the lists below pick a specific one).
  $('#shotprev').disabled=!inc.length;$('#shotfwd').disabled=!out.length;
  // Outgoing transitions carry the tap rect (the tap happens on this screen); incoming don't (their
  // tap was on the source screen, not shown here).
  let h=`<div class="nexthd${out.length?'':' muted'}">Goes to ${out.length} screen(s) →</div>`+transitionRows(out,'dst',targets);
  h+=`<div class="nexthd${inc.length?'':' muted'}">← Comes from ${inc.length} screen(s)</div>`+transitionRows(inc,'src');
  // Planned (untried) operations queued from this screen — the frontier the crawl will try next,
  // including a vision-located tab ("tap tab '…'") for a tab bar the tree couldn't address.
  const planned=(crawlGraphData.plan||{})[fp]||[];
  if(planned.length)h+=`<div class="nexthd">⏳ Planned next (${planned.length} untried)</div>`+
    planned.map(op=>{const rect=targets[op],rd=rect?` data-rect="${rect.join(',')}"`:'';
      return `<div class="planrow"${rd}>${esc(op)}</div>`}).join('');
  $('#shotnext').innerHTML=h;
  // Overlay every actionable spot on the shot (revealed on hover): one taps to a known destination
  // (an outgoing edge — clickable to step there), the rest are still-pending operations. So the shot
  // itself is navigable, the reverse of hovering a transition row to find its tap location.
  const outBy=new Map(out.map(e=>[e.action,e]));
  $('#shothots').innerHTML=Object.entries(targets).map(([desc,r])=>{
    const e=outBy.get(desc),go=!!e;
    const st=`left:${r[0]*100}%;top:${r[1]*100}%;width:${r[2]*100}%;height:${r[3]*100}%`;
    const tip=go?`${desc} → ${e.dst.slice(0,7)}`:`${desc} · not yet explored`;
    return `<div class="hot${go?' go':' pend'}" style="${st}"${go?` data-dst="${esc(e.dst)}"`:''} title="${esc(tip)}"></div>`;
  }).join('');
  openModal($('#shotmodal'));
}
// Show / hide the tap-location highlight over the screenshot. The rect is [x,y,w,h] as fractions
// of the screen, so it positions directly as percentages over the tightly-wrapped image.
function showHi(rectStr){
  const r=(rectStr||'').split(',').map(Number);if(r.length!==4||r.some(isNaN))return;
  const hi=$('#shothi');if(!hi)return;
  hi.style.left=(r[0]*100)+'%';hi.style.top=(r[1]*100)+'%';hi.style.width=(r[2]*100)+'%';hi.style.height=(r[3]*100)+'%';
  hi.hidden=false;
}
function hideHi(){const hi=$('#shothi');if(hi)hi.hidden=true}
// Step to the screen after (a transition out of) or before (a transition into) the current one.
function shotStep(dir){
  if(!crawlGraphData||shotFp==null)return;
  const edges=crawlGraphData.edges||[];
  const e=dir==='fwd'?edges.find(x=>x.src===shotFp&&x.dst!==shotFp):edges.find(x=>x.dst===shotFp&&x.src!==shotFp);
  if(e)openShot(dir==='fwd'?e.dst:e.src);
}
function closeShot(){closeModal($('#shotmodal'),()=>{$('#shotimg').removeAttribute('src');$('#shothots').innerHTML='';shotFp=null;hideHi()})}

// Wire every static listener for the panel, the graph interaction, and the lightbox. Called once by
// the entry module's boot after all sections evaluate (so the shared helpers are defined).
function initCrawl(){
  $('#crawl-simrefresh').addEventListener('click',loadSims);
  $('#crawl-go').addEventListener('click',async()=>{
    // Crawl's own pane clearing (the shared start skeleton lives in startJob).
    $('#crawl-out').textContent='';$('#crawl-counts').textContent='';
    $('#crawl-graph').innerHTML='<div class="empty">Launching the app and reaching the first screen…</div>';
    crawlPoll=await startJob({
      prev:crawlPoll,btn:$('#crawl-go'),stop:$('#crawl-stop'),busyLabel:'Crawling…',status:$('#crawl-status'),
      url:'/api/crawl',body:{
        target:$('#crawl-target').value,udid:crawlPickedUdids().join(',')||'booted',
        workers:parseInt($('#crawl-workers').value,10)||1,
        maxScreens:parseInt($('#crawl-maxscreens').value,10)||50,maxSteps:parseInt($('#crawl-maxsteps').value,10)||200,
        erase:$('#crawl-erase').checked,
        alertHandling:$('#crawl-nodismiss').checked?false:undefined,headed:$('#crawl-headed').checked||undefined},
      onStart:d=>{crawlJobId=d.jobId;crawlRunId=d.runId;},
      onLog:line=>{
        appendLine($('#crawl-out'),line);
        if(crawlRunId)loadGraph(crawlRunId);  // redraw the streamed screenmap.json as it grows
      },onDone:crawlDone});
  });
  $('#crawl-stop').addEventListener('click',()=>cancelJob(crawlJobId,$('#crawl-stop')));
  document.querySelectorAll('#view-crawl .tab').forEach(t=>t.addEventListener('click',()=>showCrawlTab(t.dataset.tab)));
  $('#crawl-refresh').addEventListener('click',loadCrawlHistory);
  // Crawl history row-open + delete + bulk-select (BE-0239), via the same shared wireHistoryList the
  // Replay history list uses (initPanels).
  crawlSel=wireHistoryList({
    list:$('#crawl-history'), noun:'crawl', reload:loadCrawlHistory,
    onOpen:li=>{const r=crawlHistRuns.find(x=>x.id===li.dataset.id);if(r)viewCrawlRun(r);},
    allBox:$('#crawlbulk-all'),bar:$('#crawlbulk'),count:$('#crawlbulk-count'),
    delBtn:$('#crawlbulk-del'),clearBtn:$('#crawlbulk-clear'),
  });
  initGraphInteraction();
  $('#crawl-zoomin').addEventListener('click',()=>{const r=$('#crawl-graph').getBoundingClientRect();zoomBy(1.2,r.left+r.width/2,r.top+r.height/2)});
  $('#crawl-zoomout').addEventListener('click',()=>{const r=$('#crawl-graph').getBoundingClientRect();zoomBy(1/1.2,r.left+r.width/2,r.top+r.height/2)});
  $('#crawl-zoomreset').addEventListener('click',resetView);
  $('#crawl-realign').addEventListener('click',()=>{nodeOverrides.clear();redrawGraph()});
  // A screen row in the plan tree opens that screen's lightbox, same as a graph node.
  $('#crawl-plan').addEventListener('click',e=>{
    // A struck-through pruned op: resume exploring that branch (re-crawl from its screen).
    const pr=e.target.closest('.plrow.op.pruned');
    if(pr&&pr.dataset.src&&pr.dataset.key){resumePruned(pr.dataset.src,pr.dataset.key);return}
    const r=e.target.closest('.plrow.scr');if(r&&r.dataset.fp)openShot(r.dataset.fp)});
  // Toggle pruning of duplicate global ops in the plan tree, re-rendering it in place.
  const t=$('#crawl-prune');if(t)t.addEventListener('change',()=>{prunePlan=t.checked;if(crawlGraphData)renderPlan(crawlGraphData)});
  $('#shotmodal').addEventListener('click',e=>{if(e.target===$('#shotmodal')||e.target===$('#shotclose'))closeShot()});
  $('#shotnext').addEventListener('click',e=>{const b=e.target.closest('.nextrow');if(b&&b.dataset.fp)openShot(b.dataset.fp)});
  // Click an actionable spot on the shot that has a known destination to step there.
  $('#shothots').addEventListener('click',e=>{const h=e.target.closest('.hot.go');if(h&&h.dataset.dst)openShot(h.dataset.dst)});
  // Hovering a transition / planned row with a known tap location highlights it on the screenshot.
  $('#shotnext').addEventListener('mouseover',e=>{const r=e.target.closest('[data-rect]');if(r)showHi(r.dataset.rect)});
  $('#shotnext').addEventListener('mouseout',e=>{if(e.target.closest('[data-rect]'))hideHi()});
  $('#shotprev').addEventListener('click',()=>shotStep('prev'));
  $('#shotfwd').addEventListener('click',()=>shotStep('fwd'));
  // Arrow keys walk the transitions; Esc closes — only while the lightbox is open.
  document.addEventListener('keydown',e=>{
    if($('#shotmodal').hidden)return;
    if(e.key==='ArrowRight')shotStep('fwd');else if(e.key==='ArrowLeft')shotStep('prev');else if(e.key==='Escape')closeShot();
  });
}

export {onCrawlSimChange, initCrawl};
