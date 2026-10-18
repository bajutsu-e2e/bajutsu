// serve.runset.mjs — the Replay tab's Environment selector and its run-set panel (BE-0465).
// A serve.*.mjs section module (BE-0247). Choosing a device-cloud environment a live worker
// advertises turns the Run form into a fan-out: Run posts the chosen scenarios to /api/run-set, and
// the panel tracks one row per dispatched job. `local` leaves the single-run form untouched.
import {
  $, esc, getJSON, postJSON, setStatus, setBusy, streamJob, appendLine, scnFiles, FETCH_ERROR, isFetchError,
} from './serve.core.mjs';
import {setReport} from './serve.panels.mjs';

const POLL_MS=2000;
// Consecutive failed status reads before a row stops polling and reads `unknown`.
const MAX_POLL_ERRORS=5;
// The batch entries of the last /api/environments read: run-set refuses a fan-out to a grid.
let environments=[];
// The open run-set: its rows, the pending poll timer, and the one row whose log is streaming.
let runSet=null;

async function loadEnvironments(){
  const d=await getJSON('/api/environments',FETCH_ERROR);
  // A failed read keeps the last list and selection: an empty rebuild would drop a chosen environment.
  if(isFetchError(d)){const r=$('#env-reason');r.textContent='Could not load environments — Refresh to retry';r.hidden=false;return;}
  environments=(Array.isArray(d.environments)?d.environments:[]).filter(e=>e.kind==='batch');
  const sel=$('#env'),prev=sel.value;
  sel.innerHTML='<option value="local">local</option>'+environments.map(e=>{
    const n=Number(e.liveWorkers)||0;
    return `<option value="${esc(e.name)}">${esc(e.name)} (${n} worker${n===1?'':'s'})</option>`;
  }).join('');
  // An environment whose last worker went away drops out of the list, and the form falls back to local.
  sel.value=[...sel.options].some(o=>o.value===prev)?prev:'local';
  syncCloudForm();
}
function targetBackend(){const o=$('#target').selectedOptions[0];return (o&&o.dataset.backend)||'';}
// A fan-out installs the target's app on a cloud device; a web target has none, so run-set refuses it.
function cloudReason(){
  return environments.length&&targetBackend()==='web'
    ?'Device-cloud environments run iOS or Android targets; this target runs in a browser.':'';
}
function cloudEnvironment(){const v=$('#env').value;return v&&v!=='local'?v:'';}
// Re-derive the form from the target and environment: called on either change and after every
// scenario-list load, so the multi-select always lists what the target's scenario dir holds.
function syncCloudForm(){
  const reason=cloudReason(),sel=$('#env');
  [...sel.options].forEach(o=>{if(o.value!=='local')o.disabled=!!reason;});
  if(reason)sel.value='local';
  const r=$('#env-reason');r.textContent=reason;r.hidden=!reason;
  $('#view-replay').classList.toggle('cloud',!!cloudEnvironment());
  syncRunSetLive();
  renderScenarioChoices();
}
function runSetLive(){return !!runSet&&runSet.rows.some(r=>!r.terminal);}
// The panel is cloud-only, except while a fan-out still runs: its rows and Stop all stay in view.
function syncRunSetLive(){$('#view-replay').classList.toggle('runset-live',runSetLive());}
function renderScenarioChoices(){
  const sel=$('#scns'),kept=new Set([...sel.selectedOptions].map(o=>o.value));
  sel.innerHTML=scnFiles.map(s=>`<option value="${esc(s.file)}"${kept.has(s.file)?' selected':''}>${esc(s.file)}</option>`).join('');
  sel.disabled=$('#all-scns').checked;
}
// Always explicit names: an omitted list would let the server re-list the dir at dispatch, and a
// file saved in between would shift every later row onto the wrong scenario.
function chosenScenarios(){
  return $('#all-scns').checked?scnFiles.map(s=>s.file):[...$('#scns').selectedOptions].map(o=>o.value);
}
async function dispatchRunSet(){
  const environment=cloudEnvironment(),scenarios=chosenScenarios(),status=$('#status');
  if(!scenarios.length){setStatus(status,'pick at least one scenario','ng');return;}
  // A new fan-out replaces the panel, which would orphan the running rows and their Stop all.
  if(runSetLive()){setStatus(status,'a run-set is still running — Stop all or wait','ng');return;}
  setBusy($('#go'),$('#stop'),true,'Dispatching…');setStatus(status,'','run');
  const d=await postJSON('/api/run-set',{target:$('#target').value,environment,scenarios},{error:'request failed'});
  setBusy($('#go'),$('#stop'),false);
  if(d.error){setStatus(status,d.error,'ng');return;}
  if(!Array.isArray(d.jobIds)){setStatus(status,'no jobs started','ng');return;}
  setStatus(status,`dispatched ${d.jobIds.length} of ${scenarios.length} to ${environment}`,'run');
  // The server dispatches in request order and stops at the first capped job, so the ids pair, by
  // position, with the leading names.
  openRunSet(d.jobIds.map((id,i)=>({id:String(id),name:scenarios[i]})),scenarios.length);
}

function rowHtml(r){
  return `<li class="rs-row" data-job="${esc(r.id)}" data-testid="replay.runset-row">`
    +`<div class="rs-head"><button type="button" class="linklike rs-toggle" aria-expanded="false" data-testid="replay.runset-toggle">${esc(r.name)}</button>`
    +`<span class="rs-status run" data-testid="replay.runset-status">dispatched</span>`
    +`<button type="button" class="linklike rs-report" data-testid="replay.runset-report" hidden>report</button></div>`
    +`<div class="subhint rs-detail" data-testid="replay.runset-detail" hidden></div>`
    +`<pre class="out rs-log" data-testid="replay.runset-log" hidden></pre></li>`;
}
function openRunSet(rows,asked){
  closeRunSet();
  const list=$('#runset-rows');
  list.innerHTML=rows.map(rowHtml).join('');
  runSet={rows:rows.map((r,i)=>({...r,el:list.children[i],view:null,terminal:false})),timer:null,open:null};
  const missed=asked-rows.length,p=$('#runset-partial');
  p.textContent=missed>0?`${missed} scenario${missed===1?' was':'s were'} not dispatched: the concurrency cap cut the fan-out short. Run ${missed===1?'it':'them'} again once these finish.`:'';
  p.hidden=missed<=0;
  $('#runset-empty').hidden=true;
  const stop=$('#runset-stop');stop.disabled=false;stop.textContent=stop.dataset.idle;
  syncRunSetLive();
  tick(runSet);
}
function closeRunSet(){
  if(!runSet)return;
  clearTimeout(runSet.timer);closeStream(runSet);runSet=null;
  syncRunSetLive();
}
// A worker's failure payload is {ok:false,error} with no status, so it ends a row as `done` does.
function isTerminal(v){return v.status==='done'||v.ok===false||!!v.error;}
function rowState(v){
  if(!v)return ['dispatched','run'];
  if(v.cancelled)return v.status==='done'?['cancelled','ng']:['cancelling…','run'];
  if(v.status==='done')return v.ok?['passed','ok']:['failed','ng'];
  if(v.ok===false||v.error)return ['failed','ng'];
  return [v.notice?'waiting for a worker':'running','run'];
}
function renderRow(row){
  const v=row.view,[label,cls]=row.lost?['unknown','ng']:rowState(v),el=row.el;
  const st=el.querySelector('.rs-status');st.textContent=label;st.className='rs-status '+cls;
  const detail=row.pollError||row.cancelError||(v&&(v.error||v.notice))||'',d=el.querySelector('.rs-detail');
  d.textContent=detail;d.hidden=!detail;
  el.querySelector('.rs-report').hidden=!(v&&v.runId);
  syncRunSetLive();
}
function applyView(row,v){
  row.pollErrors=0;row.pollError='';row.lost=false;
  row.view=v;row.terminal=isTerminal(v);if(row.terminal)row.cancelError='';renderRow(row);
}
// A failed read is shown on the row and retried; MAX_POLL_ERRORS in a row end it as `unknown`, so a
// job the server can no longer report on never reads `running` forever.
function pollFailed(set,row,why){
  if(set!==runSet)return;
  row.pollErrors=(row.pollErrors||0)+1;
  row.lost=row.pollErrors>=MAX_POLL_ERRORS;row.terminal=row.lost;
  row.pollError=`status check failed: ${why}${row.lost?'':' — retrying'}`;
  renderRow(row);
}
async function pollRow(set,row){
  let res,v;
  try{res=await fetch('/api/jobs/'+encodeURIComponent(row.id)+'?lines=0');v=await res.json();}
  catch(e){pollFailed(set,row,(e&&e.message)||'network error');return;}
  if(set!==runSet)return;
  if(res.ok&&v&&typeof v==='object'){applyView(row,v);return;}
  // A 404 means this server never registered the job (a restart, or another replica), not that it
  // failed: a worker may still run it, so the row ends `unknown` and stays cancellable (DB-backed).
  if(res.status===404){
    row.pollErrors=0;row.lost=true;row.terminal=true;
    row.pollError='this server no longer tracks the job (restart or another replica); its state is unknown';
    renderRow(row);return;
  }
  pollFailed(set,row,'HTTP '+res.status);
}
async function tick(set){
  await Promise.all(set.rows.filter(r=>!r.terminal).map(r=>pollRow(set,r)));
  if(set!==runSet)return;
  // A lost row may still run somewhere, so Stop all stays clickable while one is listed.
  if(set.rows.every(r=>r.terminal)){if(!set.rows.some(r=>r.lost)){const stop=$('#runset-stop');stop.disabled=true;stop.textContent=stop.dataset.idle;}return;}
  set.timer=setTimeout(()=>tick(set),POLL_MS);
}
function closeStream(set){
  if(!set.open)return;
  const {row,es}=set.open;es.close();set.open=null;
  row.el.querySelector('.rs-log').hidden=true;
  row.el.querySelector('.rs-toggle').setAttribute('aria-expanded','false');
}
// One stream at a time: opening a row closes the previous one, so the panel holds at most one
// EventSource however many rows it lists. The stream replays the log from its first line.
function toggleRow(set,row){
  const wasOpen=set.open&&set.open.row===row;
  closeStream(set);
  if(wasOpen)return;
  const log=row.el.querySelector('.rs-log');log.textContent='';log.hidden=false;
  row.el.querySelector('.rs-toggle').setAttribute('aria-expanded','true');
  const es=streamJob(row.id,line=>appendLine(log,line),v=>{if(set===runSet)applyView(row,v);});
  // Every (re)connect replays the log from its first line, so a reconnect starts the pane over.
  es.addEventListener('open',()=>{log.textContent='';});
  set.open={row,es};
}
function onRowClick(e){
  const set=runSet,li=e.target.closest('li[data-job]');
  if(!set||!li)return;
  const row=set.rows.find(r=>r.id===li.dataset.job);if(!row)return;
  if(e.target.closest('.rs-toggle'))toggleRow(set,row);
  else if(e.target.closest('.rs-report')&&row.view&&row.view.runId)setReport(row.view.runId,row.view.ok);
}
// The cancel's refusal, or '' when it was accepted. The shared cancel helper swallows a refusal, so
// Stop all posts its own and reports each failure on its row.
async function cancelRow(row){
  let res,v=null;
  try{res=await fetch('/api/jobs/'+encodeURIComponent(row.id)+'/cancel',{method:'POST'});}
  catch(e){return (e&&e.message)||'network error';}
  try{v=await res.json();}catch(e){}
  if(!res.ok)return (v&&v.error)||'HTTP '+res.status;
  return v&&v.error?String(v.error):'';
}
async function stopAll(){
  const set=runSet;if(!set)return;
  const stop=$('#runset-stop');stop.disabled=true;stop.textContent='Stopping…';
  // A lost row's state is unknown, so it is cancelled too rather than assumed finished.
  const rows=set.rows.filter(r=>!r.terminal||r.lost);
  const errs=await Promise.all(rows.map(cancelRow));
  if(set!==runSet)return;
  rows.forEach((r,i)=>{r.cancelError=errs[i]?`cancel failed: ${errs[i]}`:'';renderRow(r);});
  const failed=rows.filter((r,i)=>errs[i]);
  if(!failed.length){if(!runSetLive())stop.textContent=stop.dataset.idle;return;}
  setStatus($('#status'),`Stop all: ${failed.length} of ${rows.length} cancel${rows.length===1?'':'s'} failed — try again`,'ng');
  stop.disabled=false;stop.textContent=stop.dataset.idle;
}

function initRunSet(){
  $('#env').addEventListener('change',syncCloudForm);
  $('#env-refresh').addEventListener('click',loadEnvironments);
  $('#target').addEventListener('change',syncCloudForm);
  $('#all-scns').addEventListener('change',()=>{$('#scns').disabled=$('#all-scns').checked;});
  $('#runset-rows').addEventListener('click',onRowClick);
  $('#runset-stop').addEventListener('click',stopAll);
  // Workers come and go, so the list is read again whenever the Replay tab opens.
  const tab=document.querySelector('.toptab[data-view="replay"]');
  if(tab)tab.addEventListener('click',loadEnvironments);
  loadEnvironments();
}

export {initRunSet, syncCloudForm, cloudEnvironment, dispatchRunSet};
