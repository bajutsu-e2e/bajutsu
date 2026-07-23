// serve.panels.mjs — the Record / Replay / Triage / bundle-upload panels of the serve Web UI.
// A serve.*.mjs section module (BE-0247); imports its shared helpers and cross-panel state from
// serve.core.mjs. Its body only defines; every top-level listener is wired by initPanels(), which
// the entry module (serve.author.mjs) calls after all sections evaluate. Cross-panel mutable state
// (the record/replay job ids, the selected run, the Run-result show/hide hooks) lives on the shared
// `state` object rather than module-level lets, since more than one module writes it.
import {
  $, esc, getJSON, postJSON, setStatus, setBusy, streamJob, cancelJob, appendLine, startJob,
  openModal, closeModal, renderGradeBadge, setCfgName, closeFs, loadShared, loadScenarios, loadSims,
  state, scnFiles, aiAvailable,
  restoreRun, purgeRun, wireHistoryList, retentionDays,
} from './serve.core.mjs';
import {replayCodegen} from './serve.author.mjs';

// ---- Record: author a scenario from a goal ----
async function recDone(j){
  state.recPoll=null;state.recJobId=null;setBusy($('#rec-go'),$('#rec-stop'),false);hideHandoffPanel();
  if(j.cancelled){setStatus($('#rec-status'),'cancelled','ng');return}
  setStatus($('#rec-status'),j.ok?'authored ✓':'failed', j.ok?'ok':'ng');
  if(j.ok&&(j.outPath||state.recPath)){await loadGenerated(j.outPath||state.recPath);loadScenarios();}
}
async function loadGenerated(path){
  state.recPath=path;
  try{
    const d=await (await fetch('/api/scenario?target='+encodeURIComponent($('#rec-target').value)+'&path='+encodeURIComponent(path))).json();
    if(d.yaml!=null){$('#rec-yaml').value=d.yaml;$('#rec-save').disabled=false;$('#rec-run').disabled=false;
      $('#rec-yamlinfo').textContent=path.split('/').pop();}
  }catch(e){}
}
// The scenarios-dir ref to save+run the current YAML to: the recorded path when we have one, else a
// name from the "Save as" field (default generated.yaml) — so hand-pasted/edited YAML is runnable too.
function recRunRef(){
  if(state.recPath)return state.recPath;
  let name=$('#rec-name').value.trim()||'generated.yaml';
  if(!/\.ya?ml$/i.test(name))name+='.yaml';
  return name;
}
// Enable Save / Run whenever the Generated-scenario box holds YAML to save or run (idle only — a
// record or run in progress owns the buttons' disabled state).
function syncRecActions(){
  if(state.recPoll||state.recRunPoll)return;
  const has=!!$('#rec-yaml').value.trim();
  $('#rec-save').disabled=!has;$('#rec-run').disabled=!has;
}
// Show / hide the Run-result pane — the tiler hooks when present (desktop), else the plain `hidden`
// attribute (phone tier, where the pane stacks under the Output tab).
function showReportPanel(){if(state.recReportShow)state.recReportShow();else $('#rec-reportpanel').hidden=false;}
function hideReportPanel(){if(state.recReportHide)state.recReportHide();else{const p=$('#rec-reportpanel');if(p)p.hidden=true;}}
// Human handoff (BE-0179): the record paused for a human. Shown as a modal so it can't be missed
// below the fold; the human resumes by POSTing a response — a supplied value, "I operated the
// device", or a cancel — back to the job.
function showHandoffPanel(){openModal($('#rec-handoffmodal'));}
function hideHandoffPanel(){closeModal($('#rec-handoffmodal'));}
function onHandoffRequest(req){
  $('#rec-handoff-reason').textContent=req.reason||'the agent needs a human to continue';
  $('#rec-handoff-screen').textContent=[req.target&&('target: '+req.target),req.screen].filter(Boolean).join(' · ');
  const shot=$('#rec-handoff-shot');
  if(req.screenshot){shot.src='data:image/png;base64,'+req.screenshot;shot.hidden=false;}else{shot.removeAttribute('src');shot.hidden=true;}
  $('#rec-handoff-value').value='';
  syncHandoffSend();
  showHandoffPanel();
  $('#rec-handoff-value').focus();
}
// "Supply value" only submits a value — it is disabled while the field is empty, so an empty click
// can't fall through to an acted-style resume (empty `values` coerces to acted server-side).
function syncHandoffSend(){$('#rec-handoff-send').disabled=!$('#rec-handoff-value').value.trim();}
async function sendHandoff(body){
  if(!state.recJobId)return;
  // Only hide the pane once we know the response actually reached the paused record — a resume that
  // didn't land (the job already ended / its stdin is gone) must not look like it succeeded.
  try{
    const r=await fetch('/api/jobs/'+state.recJobId+'/respond-human',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){appendLine($('#rec-out'),'handoff response failed ('+r.status+'): '+(d.error||r.statusText));return;}
    if(!d.resumed){appendLine($('#rec-out'),'handoff response did not resume the record (it may have already ended)');return;}
    hideHandoffPanel();
  }catch(e){appendLine($('#rec-out'),'handoff response failed: '+e);}
}
function recRunDone(j){
  state.recRunPoll=null;state.recRunJobId=null;setBusy($('#rec-run'),$('#rec-runstop'),false);
  showReportPanel();  // re-show the pane (the user may have closed it mid-run) so the result lands
  const rs=$('#rec-runstatus');
  if(j.cancelled){if(rs)setStatus(rs,'cancelled','ng');return}
  if(rs)setStatus(rs,j.ok?'PASS':'FAIL', j.ok?'ok':'ng');
  if(j.runId)setReport(j.runId,j.ok,'#rec-report');
}

// ---- Replay: scenario info, run, history ----
function showInfo(){
  const f=scnFiles.find(s=>s.path===$('#scn').value),el=$('#names');
  $('#viewscn').disabled=!$('#scn').value;  // View scenario needs a selection, not a run (BE-0273)
  if(!f){el.innerHTML='';return}
  let h='';
  if(f.description)h+=`<div class="finfo">${esc(f.description)}</div>`;
  if(f.scenarios&&f.scenarios.length)h+='<ul class="scnlist">'+f.scenarios.map(s=>`<li><b>${esc(s.name)}</b>${s.description?' &mdash; <span class="sd">'+esc(s.description)+'</span>':''}</li>`).join('')+'</ul>';
  el.innerHTML=h;
}
// Read-only scenario viewer (BE-0273): show the selected scenario's raw YAML and the runner's own
// per-scenario step parse, so a user can confirm what a run does before spending one on it. Reuses
// the existing GET /api/scenario (no runId → structural steps, no run artifacts); never edits.
function scnViewMode(raw){
  $('#scnviewtree').hidden=raw;$('#scnviewbody').hidden=!raw;
  $('#scnview-raw').classList.toggle('active',raw);
  $('#scnview-structured').classList.toggle('active',!raw);
}
// Compact one step's fields: `{ id: nav.replay }`, `[ { exists: … } ]` — bare keys, no JSON quotes.
function scnCompact(v){
  if(v===null||v===undefined)return '';
  if(Array.isArray(v))return '[ '+v.map(scnCompact).join(', ')+' ]';
  if(typeof v==='object')return '{ '+Object.entries(v).map(([k,val])=>`${k}: ${scnCompact(val)}`).join(', ')+' }';
  return String(v);
}
function scnStepLine(step){
  const fields=scnCompact(step.fields);
  return `<span class="scnview-act">${esc(step.action)}</span>${fields?' '+esc(fields):''}`;
}
async function openScnView(){
  const target=$('#target').value,path=$('#scn').value;
  if(!path)return;
  const tree=$('#scnviewtree');tree.innerHTML='';
  $('#scnviewpath').textContent=path;
  // structure=1 opts into the runner-parsed per-scenario steps (BE-0273); yaml comes back either way.
  const d=await getJSON('/api/scenario?target='+encodeURIComponent(target)+'&path='+encodeURIComponent(path)+'&structure=1',{error:'request failed'});
  $('#scnviewbody').textContent=d.error?d.error:(d.yaml||'');
  const scns=d.scenarios||[];
  const hasStructure=scns.length>0;  // empty on a parse failure → fall back to raw (authoritative)
  if(hasStructure){
    tree.innerHTML=scns.map(s=>{
      const steps=(s.steps||[]).map(st=>`<li>${scnStepLine(st)}</li>`).join('');
      return `<div class="scnview-scn"><div class="scnview-name">${esc(s.name||'(unnamed)')}</div>`
        +(s.description?`<div class="sd">${esc(s.description)}</div>`:'')
        +(steps?`<ol class="scnview-steplist">${steps}</ol>`:'<div class="sd">(no steps)</div>')+'</div>';
    }).join('');
  }
  $('#scnview-structured').disabled=!hasStructure;
  scnViewMode(!hasStructure);
  openModal($('#scnviewmodal'));
}
function closeScnView(){closeModal($('#scnviewmodal'))}
// Grade the selected scenario's determinism (BE-0145). The Replay view has only the file path, so
// the server reads it from {target, path}; advisory, so a failed audit just leaves the badge hidden.
// A sequence guard drops a stale response so a slow audit for a since-changed selection can't
// overwrite the badge for the current one.
let replayAuditSeq=0;
async function replayAudit(){
  const badge=$('#scn-grade'),target=$('#target').value,path=$('#scn').value,seq=++replayAuditSeq;
  badge.hidden=true;
  if(!target||!path)return;
  try{
    const r=await fetch('/api/audit',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({target,path})});
    if(!r.ok||seq!==replayAuditSeq)return;
    const d=await r.json();
    if(seq===replayAuditSeq&&d.ok&&Array.isArray(d.reports))renderGradeBadge(badge,d.reports);
  }catch(e){/* advisory: leave the badge hidden */}
}
function pickedUdids(){return [...$('#sims').querySelectorAll('.simck:checked')].map(c=>c.value)}
function onSimChange(){const n=pickedUdids().length;if(n>0)$('#workers').value=n}
function runDone(j){
  state.poll=null;state.runJobId=null;setBusy($('#go'),$('#stop'),false);
  if(j.cancelled){setStatus($('#status'),'cancelled','ng');loadHistory();return}
  setStatus($('#status'),j.ok?'PASS':'FAIL', j.ok?'ok':'ng');
  if(j.runId)setReport(j.runId,j.ok);
  loadHistory();
}
// Show a run's report inline (no iframe): render report.html into a shadow root so its CSS/JS stay
// isolated, plus an "open full report ↗" link to view it as its own page. report.js is root-aware
// (window.__bajutsuReportRoot), so its queries + delegated listeners run against the shadow root.
async function setReport(id,ok,repSel){
  state.selectedRun=id;
  const rep=$(repSel||'#report');
  // Offer "Triage" only on a failed run — the "why did this fail?" the Replay/History view asks
  // right where the red report is (BE-0147). A passed run has nothing to diagnose.
  const triageBtn=ok===false?`<button class="repbtn" id="triagebtn" data-testid="replay.triage">🔧 Triage</button>`:'';
  // Replay's #report IS its tiled panel, so the drag grip (.tile-grip) and size readout (.tile-size)
  // are direct children the tiling engine owns. innerHTML would wipe them, and the grip — created
  // once at init, unlike the rebuild-healed .tile-size — would never return, leaving the report panel
  // un-draggable. Detach and re-attach them around the rewrite. (Record's #rec-report is nested inside
  // its gripped panel, so it has none of these and this is a no-op there.)
  const gripped=[...rep.querySelectorAll(':scope>.tile-grip,:scope>.tile-size')];
  rep.innerHTML=`<div class="repbar"><a class="repdl" href="/runs/${esc(id)}/archive.zip" download>⬇ download .zip</a><a class="repopen" href="/runs/${esc(id)}/report.html" target="_blank" rel="noopener">open full report ↗</a>${triageBtn}</div><div class="triagepanel" id="triagepanel" data-testid="replay.triage-panel" hidden></div><div class="rephost"></div>`;
  gripped.forEach(el=>rep.appendChild(el));
  if(ok===false)$('#triagebtn').addEventListener('click',()=>openTriage(id));
  const host=rep.querySelector('.rephost');
  let html;
  try{html=await (await fetch(`/runs/${encodeURIComponent(id)}/report.html`)).text();}
  catch(e){host.textContent='report unavailable';return;}
  // No <base> inside a shadow root, so resolve the report's relative asset URLs against its run dir.
  html=html.replace(/\b(src|href|poster)="(?!https?:|data:|\/|#)([^"]*)"/g,`$1="/runs/${id}/$2"`);
  const doc=new DOMParser().parseFromString(html,'text/html');
  const scr=doc.querySelector('script'),js=scr?scr.textContent:'';
  doc.querySelectorAll('script').forEach(s=>s.remove());
  // :root vars and the body rule don't match inside a shadow root → retarget them to :host.
  let css=((doc.querySelector('style')||{}).textContent||'').replace(/:root/g,':host')
    .replace(/(^|[\s,>}])body([\s{])/g,'$1:host$2');
  const sh=host.attachShadow({mode:'open'});
  sh.innerHTML=`<style>:host{display:block}\n${css}</style><div data-run-id="${esc(id)}">${doc.body.innerHTML}</div>`;
  window.__bajutsuReportRoot=sh;                 // report.js reads this to scope to the shadow root
  // A <script> inside a shadow root doesn't execute, so run it from the document (it still targets
  // the shadow via window.__bajutsuReportRoot); inline scripts run synchronously, so remove it after.
  const s=document.createElement('script');s.textContent=js;document.body.appendChild(s);s.remove();
}
// Render a self-contained report page (no scripts, no relative assets) into a host's shadow root so
// its inline CSS stays isolated — only retarget its :root/body rules to :host. Reusing the host's
// shadow root is idempotent, so a refresh replaces the previous content in place. Shared by the Stats
// (BE-0102) and Coverage (BE-0146) dashboards; the richer setReport keeps its own script-aware path.
// Attach (or reuse) the host's shadow root, set its content, and drop any light-DOM `.empty`
// placeholder. Once anything is rendered into the shadow — a report, a stats/coverage dashboard, OR
// an error message — the stale placeholder still reads as the host's text (accessibility / the
// dogfood's element query), so every shadow render clears it, error paths included.
function setShadowContent(host,inner){
  const sh=host.shadowRoot||host.attachShadow({mode:'open'});
  sh.innerHTML=inner;
  host.querySelectorAll(':scope>.empty').forEach(e=>e.remove());
  return sh;
}
function renderReportInShadow(host,html,opts){
  const doc=new DOMParser().parseFromString(html,'text/html');
  const css=((doc.querySelector('style')||{}).textContent||'').replace(/:root/g,':host')
    .replace(/(^|[\s,>}])body([\s{])/g,'$1:host$2');
  // The standalone report centers its body at max-width:880px via `margin:0 auto`; inside the
  // full-width serve card the text dashboards (Stats/Flaky/Usage) read better pinned to the left.
  // An outer stylesheet rule can't override a shadow-tree `:host` declaration, so append the
  // left-align here — same tree scope, later in source order, so it wins on the report's margin.
  const extra=opts&&opts.leftAlign?'\n:host{margin-left:0;margin-right:auto}':'';
  setShadowContent(host,`<style>:host{display:block}\n${css}${extra}</style>${doc.body.innerHTML}`);
}
// ---- Triage (BE-0147): diagnose a failed run in the browser. The heuristic agent is the default
// and fully deterministic; Claude is opt-in and only investigates — no LLM ever decides pass/fail.
// A proposed fix is previewed as a diff and written only on an explicit click, through the same
// validated scenario-save path the editor uses. The run's verdict is read back, never recomputed. ----
function openTriage(id){
  const panel=$('#triagepanel');panel.hidden=false;
  panel.innerHTML=`<div class="triagebar"><span class="triagetitle">Triage</span>`
    +`<label class="triage-aiopt" title="Diagnose with Claude instead of the built-in rules"><input type="checkbox" id="triage-ai" data-testid="replay.triage-ai"${aiAvailable?'':' disabled'}> Claude</label>`
    +`<button class="repbtn" id="triage-go" data-idle="Diagnose" data-testid="replay.triage-go">Diagnose</button>`
    +`<button class="stop" id="triage-stop" data-testid="replay.triage-stop" hidden>Stop</button></div>`
    +`<pre class="triagelog" id="triage-log" data-testid="replay.triage-log" hidden></pre>`
    +`<div class="triageresult" id="triage-result" data-testid="replay.triage-result"></div>`;
  $('#triage-go').addEventListener('click',()=>runTriage(id));
  $('#triage-stop').addEventListener('click',()=>cancelJob(state.triageJobId,$('#triage-stop')));
}
async function runTriage(id){
  const go=$('#triage-go'),stop=$('#triage-stop'),log=$('#triage-log'),res=$('#triage-result');
  res.innerHTML='';log.textContent='';log.hidden=false;
  setBusy(go,stop,true,'Diagnosing…');
  // Capture the scenario source now (target + path) so Apply writes to the file that was
  // diagnosed, even if the user changes the Run-tab selectors while triage is streaming.
  const target=$('#target').value,scenario=$('#scn').value,ai=$('#triage-ai').checked||undefined;
  let r;try{r=await fetch('/api/triage',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({runId:id,target,scenario,ai})})}catch(e){r=null}
  const d=r&&r.ok?await r.json():{error:r?('HTTP '+r.status):'request failed'};
  if(!r||!r.ok||d.error){setBusy(go,stop,false);res.innerHTML=`<div class="triageerr">${esc(d.error||'triage failed')}</div>`;return}
  state.triageJobId=d.jobId;
  streamJob(d.jobId,line=>{const l=$('#triage-log');if(l)appendLine(l,line)},j=>triageDone(id,target,scenario,j));
}
function triageDone(id,target,scenario,j){
  state.triageJobId=null;
  const res=$('#triage-result');if(!res)return;  // the panel was torn down (user navigated away)
  setBusy($('#triage-go'),$('#triage-stop'),false);
  if(j.cancelled){res.innerHTML='<div class="triageerr">cancelled</div>';return}
  if(!j.ok){res.innerHTML='<div class="triageerr">triage failed — see the log above.</div>';return}
  loadTriageResult(id,target,scenario);
}
// Read back the machine-readable result the job wrote into the run dir and render it. A finished
// triage with no diagnosable failure writes no triage.json (exit 0), so a miss is not an error —
// say so rather than leaving the panel blank.
async function loadTriageResult(id,target,scenario){
  let d;try{const r=await fetch(`/runs/${encodeURIComponent(id)}/triage.json`);d=r.ok?await r.json():null}catch(e){d=null}
  const res=$('#triage-result');if(!res)return;
  if(d)renderTriage(id,target,scenario,d);
  else res.innerHTML='<div class="triagefix muted">No diagnosis was produced for this run — see the log above.</div>';
}
function renderTriage(id,target,scenario,d){
  const res=$('#triage-result');if(!res)return;
  let h=`<div class="triagediag"><span class="triagecat">${esc(d.category||'')}</span> ${esc(d.summary||'')}</div>`;
  if(d.suggestions&&d.suggestions.length)h+='<ul class="triagesugg">'+d.suggestions.map(s=>`<li>${esc(s)}</li>`).join('')+'</ul>';
  const ap=d.apply,hasFix=!!(d.fix&&ap&&ap.count>0);
  if(hasFix){
    h+=`<div class="triagefix">${esc(d.fix.summary)}</div><pre class="triagediff">${esc(ap.diff)}</pre>`
      +`<div class="triageactions"><button class="repbtn" id="triage-apply" data-testid="replay.triage-apply">Apply fix</button>`
      +`<button class="repbtn" id="triage-applyrun" data-testid="replay.triage-applyrun">Apply &amp; re-run</button>`
      +`<span class="status" id="triage-applystatus"></span></div>`;
  }else if(d.fix){
    h+=`<div class="triagefix muted">${esc(d.fix.summary)} — not found in the current scenario source, nothing to apply.</div>`;
  }else{
    h+='<div class="triagefix muted">No mechanical fix for this failure — advisory only.</div>';
  }
  res.innerHTML=h;
  if(hasFix){
    $('#triage-apply').addEventListener('click',()=>applyTriage(target,scenario,d,false));
    $('#triage-applyrun').addEventListener('click',()=>applyTriage(target,scenario,d,true));
  }
}
// Apply is the human's explicit action: write the previewed patch through POST /api/scenario (which
// re-validates the YAML), against the scenario source that was diagnosed (captured at Diagnose time).
async function applyTriage(target,scenario,d,rerun){
  const st=$('#triage-applystatus'),apply=$('#triage-apply'),applyrun=$('#triage-applyrun');
  apply.disabled=true;applyrun.disabled=true;setStatus(st,'applying…','');
  let r;try{r=await fetch('/api/scenario',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({target,path:scenario,yaml:d.apply.patched})})}catch(e){r=null}
  const j=r&&r.ok?await r.json():{error:r?('HTTP '+r.status):'request failed'};
  if(!r||!r.ok||j.error){apply.disabled=false;applyrun.disabled=false;setStatus(st,j.error||'apply failed','ng');return}
  setStatus(st,'applied ✓','ok');
  if(!rerun)return;
  // Re-run the diagnosed scenario to confirm the fix. Realign the Run-tab selectors to the file we
  // patched: setting .value fires no `change` event, so load that target's scenarios explicitly and
  // await it (no racing rebuild) before selecting the scenario and running.
  $('#target').value=target;
  await loadScenarios();
  $('#scn').value=scenario;
  $('#go').click();
}
async function loadStats(){
  const host=$('#stats-host');
  let html;
  // Treat a network error or a non-2xx (e.g. 401/500) as unavailable, and render the error into the
  // shadow root so a failed refresh replaces the stale dashboard instead of leaving it on screen.
  try{const r=await fetch('/stats');if(!r.ok)throw 0;html=await r.text();}
  catch(e){setShadowContent(host,'<div style="color:#6e6e73;font-style:italic">stats unavailable</div>');return;}
  renderReportInShadow(host,html,{leftAlign:true});
}
// Flaky (BE-0220): fetch the self-contained flaky-scenario panel and render it into a shadow root,
// the same isolation as loadStats. The ranking stays server-side (/flakiness over the run history);
// the view only displays. A network error or non-2xx replaces the stale panel with an unavailable notice.
async function loadFlaky(){
  const host=$('#flaky-host');
  let html;
  try{const r=await fetch('/flakiness');if(!r.ok)throw 0;html=await r.text();}
  catch(e){setShadowContent(host,'<div style="color:#6e6e73;font-style:italic">flaky scenarios unavailable</div>');return;}
  renderReportInShadow(host,html,{leftAlign:true});
}
// Usage (BE-0195): fetch the self-contained AI usage/cost dashboard and render it into a shadow root,
// the same isolation as loadStats. Aggregation stays server-side (/usage over the ledger); the view
// only displays. A network error or non-2xx replaces the stale dashboard with an unavailable notice.
async function loadUsage(){
  const host=$('#usage-host');
  let html;
  try{const r=await fetch('/usage');if(!r.ok)throw 0;html=await r.text();}
  catch(e){setShadowContent(host,'<div style="color:#6e6e73;font-style:italic">usage unavailable</div>');return;}
  renderReportInShadow(host,html,{leftAlign:true});
}
// Drilldown filter (BE-0241): while set, loadHistory() renders only the runs a Stats-dashboard deep
// link named, with a "filtered: <label> · clear" banner. null (the default) shows the full history.
let historyFilter=null;
function setHistoryFilter(ids,label){
  const clean=(ids||[]).map(s=>String(s).trim()).filter(Boolean);
  historyFilter=clean.length?{ids:new Set(clean),label:label||''}:null;
}
// Drop the ?tab=history&runs=… deep link from the URL too, so a reload after "clear" doesn't let the
// serve.author.mjs boot handler silently reinstate the filter the user just cleared.
function clearHistoryFilter(){historyFilter=null;history.replaceState(null,'',location.pathname);loadHistory();}
function renderHistFilter(shown){
  const box=$('#histfilter');if(!box)return;
  if(!historyFilter){box.hidden=true;return}
  // renderHistFilter owns the run count, so the label is just the drilldown's descriptor (may be blank).
  const label=historyFilter.label?historyFilter.label+' ':'';
  $('#histfilter-label').textContent=`filtered: ${label}(${shown} run${shown===1?'':'s'})`;
  box.hidden=false;
}
// Per-run delete + bulk-select (BE-0239), wired via the shared `wireHistoryList` (serve.core.mjs) —
// the Crawl history list (serve.crawl.mjs) wires the same way, so the delete confirm, bulk-delete
// confirm, and row-click delegation live in one place. `histSel` is the selector wireHistoryList
// returns (assigned in initPanels); loadHistory re-syncs it after each re-render.
let histSel=null;
async function loadHistory(){
  const runs=await getJSON('/api/runs',null);if(!runs)return;
  const tab=$('#histtab');if(tab)tab.textContent='History'+(runs.length?` (${runs.length})`:'');
  const shown=historyFilter?runs.filter(r=>historyFilter.ids.has(r.id)):runs;
  renderHistFilter(shown.length);
  const ul=$('#history');
  if(!shown.length){ul.innerHTML=`<li class="muted">${historyFilter?'no matching runs':'no runs yet'}</li>`;if(histSel)histSel.sync();return;}
  ul.innerHTML=shown.map(r=>`<li data-id="${esc(r.id)}" data-ok="${r.ok?1:0}"${r.id===state.selectedRun?' class="sel"':''}><input type="checkbox" class="rowck" aria-label="select run for deletion" data-testid="replay.history-select" value="${esc(r.id)}"><span class="dot ${r.ok?'ok':'ng'}"></span><span class="hid">${esc(r.id)}</span><span class="hsum">${r.passed}/${r.total}${r.scenarios.length?' · '+esc(r.scenarios.join(', ')):''}</span><button type="button" class="rowdel" title="Delete this run" aria-label="Delete run" data-testid="replay.history-delete">&#128465;</button></li>`).join('');
  if(histSel)histSel.sync();
}
// ---- Trash view (BE-0239): soft-deleted runs (regular + crawl share the trash), each restorable or,
// for an admin, permanently deletable. Restore/purge key on the id alone, so one /api/runs route
// serves both run types here. The list interactions are delegated once in initPanels. ----
function trashHeaderNote(){
  // Guard null the same way trashWindowNote does (serve.core.mjs): loadConfig runs unawaited at
  // boot, so a Trash-tab visit before /api/config resolves — or a failed fetch — must not read as
  // "retention disabled" when it's really just unknown yet.
  if(retentionDays===null)return 'Deleted runs are kept here and can be restored.';
  return retentionDays>0
    ?`Deleted runs are kept here and can be restored. Each is permanently removed ${retentionDays} day${retentionDays===1?'':'s'} after deletion.`
    :'Deleted runs are kept here until permanently removed. Restore one, or delete it forever (admin).';
}
// Render an ISO deletion time in the viewer's locale; fall back to the raw value if it can't parse (a
// hand-edited tombstone), never "Invalid Date".
function fmtDeletedAt(iso){
  if(!iso)return 'unknown time';
  const d=new Date(iso);
  return isNaN(d.getTime())?iso:d.toLocaleString();
}
async function loadTrash(){
  const list=$('#trash-list');if(!list)return;
  const note=$('#trash-note');if(note)note.textContent=trashHeaderNote();
  const runs=await getJSON('/api/runs/trash',null);
  if(!runs){list.innerHTML='<li class="muted">trash unavailable</li>';return}
  if(!runs.length){list.innerHTML='<li class="muted" data-testid="trash.empty">Trash is empty</li>';return}
  list.innerHTML=runs.map(r=>`<li data-id="${esc(r.id)}" data-testid="trash.item"><span class="hid">${esc(r.id)}</span><span class="hsum">deleted ${esc(fmtDeletedAt(r.deletedAt))}</span><span class="trashacts"><button type="button" class="cfgbtn" data-act="restore" data-testid="trash.restore">Restore</button><button type="button" class="cfgbtn prjremove" data-act="purge" data-testid="trash.purge">Delete forever</button></span></li>`).join('');
}
async function restoreTrashRun(id){
  const d=await restoreRun(id);
  if(d&&d.error)window.alert('Restore failed: '+d.error);
  loadTrash();
}
async function purgeTrashRun(id){
  // The one irreversible step — an emphatic confirm distinct from the soft-delete one. A non-admin on
  // a hosted backend gets a 403 (no purge right); surface it plainly rather than silently doing nothing.
  if(!window.confirm(`Permanently delete run ${id}?\n\nThis cannot be undone — its report, screenshots, video, and network capture are erased for good.`))return;
  const d=await purgeRun(id);
  if(d&&d.error)window.alert(d.error==='forbidden'?'Only an admin can permanently delete a run.':'Delete failed: '+d.error);
  loadTrash();
}

// Coverage (BE-0146): POST the target (+ optional run set) to /api/coverage and render the returned
// self-contained report into a shadow root — the same isolation as loadStats. The aggregation stays
// server-side (the CLI's `bajutsu coverage`), so nothing is recomputed in JS; the view only displays.
async function coverageInit(){
  // Fill the run picker from the same history the Replay view lists; a target is already populated by
  // loadShared. Selecting runs is optional — it folds in the endpoint / observed-id dimensions.
  const runs=await getJSON('/api/runs',[]);
  $('#cov-runs').innerHTML=runs.map(r=>`<option value="${esc(r.id)}">${esc(r.id)}${r.scenarios&&r.scenarios.length?' · '+esc(r.scenarios.join(', ')):''}</option>`).join('');
}
async function loadCoverage(){
  const host=$('#cov-host');
  // Render errors through setShadowContent too, so a failed recompute replaces the stale map and
  // clears the light-DOM placeholder — the same reasoning (and helper) as loadStats.
  const fail=msg=>{setShadowContent(host,`<div style="color:#6e6e73;font-style:italic">${esc(msg)}</div>`)};
  const target=$('#cov-target').value;
  if(!target){fail('Open a config and pick a target first.');return}
  const runs=[...$('#cov-runs').selectedOptions].map(o=>o.value);
  let resp;
  try{const r=await fetch('/api/coverage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target,runs})});
    resp=await r.json();if(!r.ok)throw new Error(resp.error||'coverage failed');}
  catch(e){fail(e.message||'coverage unavailable');return}
  renderReportInShadow(host,resp.html);
}
function showTab(name){
  document.querySelectorAll('#view-replay .tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===name));
  $('#panel-run').hidden=name!=='run';$('#panel-history').hidden=name!=='history';
  if(name==='history')loadHistory();
}

// ---- Upload a bundle as the active config (BE-0073) ----
// A self-contained .zip (config + scenarios + the built app binary its appPath names) is POSTed as a
// raw body (not multipart: the SPA controls the request, so a streamed body needs no parser). The
// server extracts it into a sandbox and binds it as the active config — exactly like the file-browser
// and Git sources — so the Replay / Record / Crawl tabs run from it. Provenance (file name + sha256)
// shows briefly before the modal closes.
function fmtSize(n){if(n<1024)return n+' B';if(n<1048576)return (n/1024).toFixed(0)+' KB';return (n/1048576).toFixed(1)+' MB';}
async function chooseUploadConfig(file){
  if(!file)return;
  const meta=$('#up-meta'),err=$('#up-error');err.hidden=true;
  meta.hidden=false;meta.textContent='Uploading '+file.name+' ('+fmtSize(file.size)+')…';
  let d;
  try{
    const r=await fetch('/api/upload?name='+encodeURIComponent(file.name),
      {method:'POST',headers:{'Content-Type':'application/zip'},body:file});
    d=await r.json();
  }catch(e){meta.hidden=true;err.textContent='upload failed';err.hidden=false;return;}
  if(d.error){meta.hidden=true;err.textContent=d.error;err.hidden=false;return;}
  const s=d.source||{};
  // textContent (not innerHTML): the file name comes from a file input, so never reinterpret it as HTML.
  meta.textContent='Bound '+(s.filename||file.name)+' · '+fmtSize(s.size||file.size)+' · sha256 '+(s.sha256||'').slice(0,12)+'…';
  setCfgName(d.config,true);closeFs();await loadShared();
}

// Wire a file zone: a "pick" button opening the hidden file input, that input's change, and
// drag/drop onto the zone — all funnelling the chosen File to `onFile`. Shared by the bundle upload
// (one zone) and the compose picker (one zone per artifact kind), so the drag-highlight contract and
// the re-pick fix live once. Any of the three elements may be absent (a hosted deployment hides some).
function wireFileZone(pickBtn,fileInput,dropEl,onFile){
  if(pickBtn&&fileInput){
    pickBtn.addEventListener('click',()=>fileInput.click());
    fileInput.addEventListener('change',e=>{const f=e.target.files[0];e.target.value='';if(f)onFile(f);});  // clear value so re-picking the same file still fires change
  }
  if(dropEl){
    const stop=e=>{e.preventDefault();e.stopPropagation();};
    ['dragenter','dragover'].forEach(ev=>dropEl.addEventListener(ev,e=>{stop(e);dropEl.classList.add('dragover');}));
    ['dragleave','drop'].forEach(ev=>dropEl.addEventListener(ev,e=>{stop(e);dropEl.classList.remove('dragover');}));
    dropEl.addEventListener('drop',e=>{const f=e.dataTransfer.files[0];if(f)onFile(f);});
  }
}

// ---- Compose the active config from independently-uploaded artifacts (BE-0268) ----
// Each of config/scenarios/binary is content-addressed: hash it in the browser, ask whether the
// server already holds those bytes (`/api/artifacts/exists`), and POST only on a miss — so an
// unchanged binary never travels the wire. A composition is a triple of shas assembled at run time
// (`/api/compose`), so a new binary×scenario combination is a fresh triple over stored parts, not a
// fresh upload (the combination matrix). Selections persist while the modal is open, so swapping one
// part and composing again reuses the others as-is.
const COMPOSE_KINDS=['config','scenarios','binary'];
const composeState={config:null,scenarios:null,binary:null};  // per kind: {sha, filename, reused}
const composeBusy=new Set();  // kinds mid-hash/mid-upload; #cmp-run stays disabled while any is in flight
function setComposeBusy(kind,busy){
  // Disable Compose & load while ANY zone is still working, so a leg that's nulled-out for the
  // duration of chooseArtifact can't be silently dropped from the /api/compose body by an early click.
  if(busy)composeBusy.add(kind);else composeBusy.delete(kind);
  const b=$('#cmp-run');if(b)b.disabled=composeBusy.size>0;
}
async function sha256Hex(file){
  const buf=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
  return Array.from(new Uint8Array(buf)).map(b=>b.toString(16).padStart(2,'0')).join('');
}
function renderComposeZone(kind){
  const el=$('#cmp-'+kind+'-state');if(!el)return;const s=composeState[kind];
  if(!s){el.textContent='';el.classList.remove('reused');return;}
  // textContent (not innerHTML): the file name comes from a file input, so never reinterpret it as HTML.
  el.textContent=s.filename+' · '+s.sha.slice(0,12)+'… '+(s.reused?'(already stored — skipped)':'(uploaded)');
  el.classList.toggle('reused',s.reused);
}
async function chooseArtifact(kind,file){
  if(!file)return;
  const err=$('#cmp-error'),state=$('#cmp-'+kind+'-state');err.hidden=true;
  composeState[kind]=null;renderComposeZone(kind);
  setComposeBusy(kind,true);  // cleared in finally, so every exit path re-enables the button
  try{
    // Content-addressing is computed in the browser, which needs WebCrypto — only available in a
    // secure context (HTTPS or localhost). Say so plainly rather than surfacing a generic read error
    // when serve is reached over plain HTTP on a non-localhost host.
    if(!(window.crypto&&crypto.subtle)){state.textContent='';err.textContent='composing from artifacts needs a secure context (open serve over HTTPS or on localhost)';err.hidden=false;return;}
    state.textContent='Hashing '+file.name+'…';
    let sha;
    try{sha=await sha256Hex(file);}
    catch(e){state.textContent='';err.textContent='could not read '+file.name;err.hidden=false;return;}
    // Skip the upload when the server already has these exact bytes (content-addressed dedup).
    let reused=false;
    try{const d=await getJSON('/api/artifacts/exists?kind='+kind+'&sha256='+sha,null);reused=!!(d&&d.exists);}
    catch(e){reused=false;}
    if(!reused){
      state.textContent='Uploading '+file.name+' ('+fmtSize(file.size)+')…';
      let d;
      try{
        const r=await fetch('/api/artifacts/'+kind,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});
        d=await r.json();
        if(!r.ok||d.error){state.textContent='';err.textContent=(d&&d.error)||'upload failed';err.hidden=false;return;}
      }catch(e){state.textContent='';err.textContent='upload failed';err.hidden=false;return;}
    }
    composeState[kind]={sha:sha,filename:file.name,reused:reused};
    renderComposeZone(kind);
  }finally{setComposeBusy(kind,false);}
}
async function composeAndLoad(){
  const err=$('#cmp-error'),meta=$('#cmp-meta'),btn=$('#cmp-run');err.hidden=true;
  if(!composeState.config){err.textContent='a config artifact is required';err.hidden=false;return;}
  const body={config:composeState.config.sha,filename:composeState.config.filename};
  // scenariosName names a single-.yaml scenarios artifact server-side; omit it for a .zip so the
  // same zip under two filenames stays one cache entry (the compose key is salted only by this name).
  if(composeState.scenarios){body.scenarios=composeState.scenarios.sha;if(!/\.zip$/i.test(composeState.scenarios.filename))body.scenariosName=composeState.scenarios.filename;}
  if(composeState.binary)body.binary=composeState.binary.sha;
  // Guard against a double-click firing two concurrent /api/compose calls; the finally restores
  // disabled from composeBusy so a zone that began uploading during the POST keeps the button off.
  if(btn)btn.disabled=true;
  meta.hidden=false;meta.textContent='Composing…';
  try{
    const d=await postJSON('/api/compose',body,{error:'compose failed'});
    if(!d||d.error){meta.hidden=true;err.textContent=(d&&d.error)||'compose failed';err.hidden=false;return;}
    meta.textContent='Composed and bound '+((d.targets||[]).length)+' target(s)';
    setCfgName(d.config,true);closeFs();await loadShared();
  }finally{if(btn)btn.disabled=composeBusy.size>0;}
}

// Wire every static listener. Called once by the entry module's boot after all sections evaluate.
function initPanels(){
  // Record.
  $('#rec-simrefresh').addEventListener('click',loadSims);
  $('#rec-go').addEventListener('click',async()=>{
    const goal=$('#rec-goal').value.trim();
    if(!goal){setStatus($('#rec-status'),'enter a goal first','ng');return}
    // Record's own pane clearing (the shared start skeleton lives in startJob). Clear any prior
    // in-place run so its report/status don't linger over a fresh authoring. The report / status live
    // in the Run-result pane, which the tiler detaches while hidden, so guard the lookups — they're
    // null until a run has shown the pane.
    $('#rec-out').textContent='';
    $('#rec-yaml').value='';$('#rec-save').disabled=true;$('#rec-yamlinfo').textContent='';state.recPath=null;
    if(state.recRunPoll)state.recRunPoll.close();
    $('#rec-run').disabled=true;
    const rep0=$('#rec-report');if(rep0)rep0.innerHTML='';
    const rs0=$('#rec-runstatus');if(rs0)setStatus(rs0,'','');
    hideReportPanel();hideHandoffPanel();
    state.recPoll=await startJob({
      prev:state.recPoll,btn:$('#rec-go'),stop:$('#rec-stop'),busyLabel:'Authoring…',status:$('#rec-status'),
      url:'/api/record',body:{
        goal,target:$('#rec-target').value,
        udid:$('#rec-device').value||'booted',name:$('#rec-name').value.trim()||undefined,
        erase:$('#rec-erase').checked,dismissAlerts:$('#rec-nodismiss').checked?false:undefined,
        headed:$('#rec-headed').checked||undefined},
      onStart:d=>{state.recPath=d.path;state.recJobId=d.jobId;},
      onLog:line=>appendLine($('#rec-out'),line),onDone:recDone,onHuman:onHandoffRequest});
  });
  $('#rec-stop').addEventListener('click',()=>cancelJob(state.recJobId,$('#rec-stop')));
  $('#rec-yaml').addEventListener('input',syncRecActions);
  $('#rec-handoff-value').addEventListener('input',syncHandoffSend);
  $('#rec-handoff-value').addEventListener('keydown',e=>{if(e.key==='Enter'&&!$('#rec-handoff-send').disabled)$('#rec-handoff-send').click();});
  $('#rec-handoff-send').addEventListener('click',()=>{const v=$('#rec-handoff-value').value.trim();if(!v)return;sendHandoff({values:[v]});});
  $('#rec-handoff-acted').addEventListener('click',()=>sendHandoff({acted:true}));
  $('#rec-handoff-cancel').addEventListener('click',()=>sendHandoff({cancelled:true}));
  // Run the current scenario in place, without switching to Replay. Persist the YAML first (creating
  // the file for hand-pasted YAML; a parse error means it isn't runnable, so it's surfaced and Run
  // stops), then start a normal run: the live log streams to the Progress console (like Generate) and
  // only the final report lands in the dismissable Run-result pane.
  $('#rec-run').addEventListener('click',async()=>{
    const yaml=$('#rec-yaml').value;
    if(!yaml.trim())return;
    if(state.recRunPoll)state.recRunPoll.close();
    const target=$('#rec-target').value;
    // Re-evaluate the scenario on every Run: clear a prior syntax error first, so a since-fixed
    // scenario shows no stale error and only a still-broken one re-surfaces below.
    setStatus($('#rec-status'),'','');
    const sd=await postJSON('/api/scenario',{target,path:recRunRef(),yaml},{error:'request failed'});
    if(sd.error){setStatus($('#rec-status'),sd.error,'ng');return}
    state.recPath=sd.path;$('#rec-yamlinfo').textContent=state.recPath.split('/').pop();loadScenarios();
    setBusy($('#rec-run'),$('#rec-runstop'),true,'Running…');
    $('#rec-out').textContent='';  // the live run log shares the Progress console, like Generate
    showReportPanel();  // attaches the pane, so the report / status lookups below resolve
    const rep=$('#rec-report');if(rep)rep.innerHTML='';
    const rs=$('#rec-runstatus');if(rs)setStatus(rs,'','run');
    const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      scenario:state.recPath,target,udid:$('#rec-device').value||'booted',
      erase:$('#rec-erase').checked||undefined,dismissAlerts:$('#rec-nodismiss').checked?false:undefined})});
    const {jobId,error}=await r.json();
    if(error){setStatus($('#rec-runstatus'),error,'ng');setBusy($('#rec-run'),$('#rec-runstop'),false);return;}
    state.recRunJobId=jobId;
    state.recRunPoll=streamJob(jobId,line=>appendLine($('#rec-out'),line),recRunDone);
  });
  $('#rec-runstop').addEventListener('click',()=>cancelJob(state.recRunJobId,$('#rec-runstop')));
  // Dismiss the Run-result pane with its X. A run in progress keeps going (Stop lives on the Generated
  // scenario panel) and the pane reappears on the next Run.
  $('#rec-runclose').addEventListener('click',hideReportPanel);
  $('#rec-save').addEventListener('click',async()=>{
    if(!$('#rec-yaml').value.trim())return;
    $('#rec-save').disabled=true;$('#rec-save').textContent='Saving…';
    const r=await fetch('/api/scenario',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({target:$('#rec-target').value,path:recRunRef(),yaml:$('#rec-yaml').value})});
    const d=await r.json();
    $('#rec-save').textContent='Save';$('#rec-save').disabled=false;
    if(d.error){setStatus($('#rec-status'),d.error,'ng')}
    else{state.recPath=d.path;$('#rec-yamlinfo').textContent=state.recPath.split('/').pop();setStatus($('#rec-status'),'saved ✓','ok');loadScenarios()}
  });
  // Replay.
  $('#scn').addEventListener('change',()=>{showInfo();replayAudit();replayCodegen.reset();});
  $('#viewscn').addEventListener('click',openScnView);
  $('#scnviewclose').addEventListener('click',closeScnView);
  $('#scnview-structured').addEventListener('click',()=>scnViewMode(false));
  $('#scnview-raw').addEventListener('click',()=>scnViewMode(true));
  $('#scnviewmodal').addEventListener('click',e=>{if(e.target===$('#scnviewmodal'))closeScnView()});
  $('#simrefresh').addEventListener('click',loadSims);
  $('#go').addEventListener('click',async()=>{
    $('#out').textContent='';  // Replay's own pane clearing (the shared start skeleton lives in startJob)
    state.poll=await startJob({
      prev:state.poll,btn:$('#go'),stop:$('#stop'),busyLabel:'Running…',status:$('#status'),
      url:'/api/run',body:{
        scenario:$('#scn').value,target:$('#target').value,udid:pickedUdids().join(',')||'booted',
        workers:parseInt($('#workers').value,10)||1,headed:$('#headed').checked||undefined,
        erase:$('#erasedev').checked||undefined,dismissAlerts:$('#nodismiss').checked?false:undefined},
      onStart:d=>{state.runJobId=d.jobId;},
      onLog:line=>appendLine($('#out'),line),onDone:runDone});
  });
  $('#stop').addEventListener('click',()=>cancelJob(state.runJobId,$('#stop')));
  $('#refresh').addEventListener('click',loadHistory);
  $('#histfilter-clear').addEventListener('click',clearHistoryFilter);
  // History row-open + delete + bulk-select (BE-0239), via the shared wireHistoryList — the Crawl
  // history list wires the same way in initCrawl.
  histSel=wireHistoryList({
    list:$('#history'), noun:'run', reload:loadHistory,
    onOpen:li=>setReport(li.dataset.id,li.dataset.ok==='1'),
    allBox:$('#histbulk-all'),bar:$('#histbulk'),count:$('#histbulk-count'),
    delBtn:$('#histbulk-del'),clearBtn:$('#histbulk-clear'),
  });
  // Trash view (BE-0239): refresh + delegated Restore / Delete-forever on the stable list.
  $('#trash-refresh').addEventListener('click',loadTrash);
  $('#trash-list').addEventListener('click',e=>{
    const li=e.target.closest('li[data-id]');if(!li)return;
    const act=e.target.closest('button[data-act]');if(!act)return;
    if(act.dataset.act==='restore')restoreTrashRun(li.dataset.id);
    else if(act.dataset.act==='purge')purgeTrashRun(li.dataset.id);
  });
  $('#stats-refresh').addEventListener('click',loadStats);
  $('#flaky-refresh').addEventListener('click',loadFlaky);
  $('#usage-refresh').addEventListener('click',loadUsage);
  $('#cov-go').addEventListener('click',loadCoverage);
  document.querySelectorAll('#view-replay .tab').forEach(t=>t.addEventListener('click',()=>showTab(t.dataset.tab)));
  // Upload & compose file zones (BE-0073 bundle upload, BE-0268 compose-from-artifacts). Both go
  // through wireFileZone; every element may be absent in a hosted deployment, so each binding self-guards.
  wireFileZone($('#up-pick'),$('#up-file'),$('#up-drop'),chooseUploadConfig);
  COMPOSE_KINDS.forEach(kind=>wireFileZone(
    $('#cmp-'+kind+'-pick'),$('#cmp-'+kind+'-file'),$('#cmp-'+kind+'-drop'),f=>chooseArtifact(kind,f)));
  const cmpRun=$('#cmp-run');if(cmpRun)cmpRun.addEventListener('click',composeAndLoad);
}

export {
  loadHistory, loadStats, loadFlaky, loadUsage, coverageInit, showInfo, replayAudit, onSimChange,
  setHistoryFilter, showTab, initPanels, loadTrash,
};
