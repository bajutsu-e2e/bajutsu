// ---- login: a request 401s when the server requires auth. If GitHub OAuth is configured, send the
// browser through it; otherwise prompt for the shared token, POST /api/login (which sets an HttpOnly
// session cookie), then reload. No-op on an open server (BE-0051 token; BE-0015 7b-2 OAuth). ----
const _bjFetch=window.fetch.bind(window);
let _bjLoginShown=false;
window.fetch=async(...a)=>{
  const r=await _bjFetch(...a);
  if(r.status===401 && !String(a[0]).includes('/api/login')) _bjLogin();
  return r;
};
async function _bjLogin(){
  if(_bjLoginShown)return; _bjLoginShown=true;
  // When OAuth is configured, /api/oauth/login 302s to GitHub; detect that (without following it)
  // and navigate there. A non-redirect (404) means OAuth is off — fall back to the token prompt.
  try{
    const probe=await _bjFetch('/api/oauth/login',{redirect:'manual'});
    if(probe.type==='opaqueredirect'||probe.status===302){window.location='/api/oauth/login';return}
  }catch(e){}
  const t=prompt('This bajutsu server requires a token:');
  if(!t){_bjLoginShown=false;return;}
  _bjFetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:t})})
    .then(r=>{if(r.ok){location.reload()}else{_bjLoginShown=false;alert('invalid token')}})
    .catch(()=>{_bjLoginShown=false});
}

const $=s=>document.querySelector(s);
let poll=null,recPoll=null,selectedRun=null,recPath=null,scnFiles=[],targets=[],sims=[];
let recJobId=null,runJobId=null;
// Toggle a run/stop button pair between idle and running (amber + spinner via the .running class).
function setBusy(btn,stop,on,busyLabel){
  btn.classList.toggle('running',on);btn.disabled=on;btn.textContent=on?busyLabel:btn.dataset.idle;
  stop.hidden=!on;stop.disabled=false;stop.textContent='Stop';
}
// Ask the server to abort a running job; the live stream then sees it finish and resets the UI.
async function cancelJob(id,stop){
  if(!id)return;stop.disabled=true;stop.textContent='Stopping…';
  try{await fetch('/api/jobs/'+id+'/cancel',{method:'POST'})}catch(e){}
}
// Live-stream a job's log over SSE (BE-0015): a `log` event per line, then one `done` event with
// the job's final view. Returns the EventSource so a restart can close it. Replaces 1s polling.
function streamJob(id,onLog,onDone){
  const es=new EventSource('/api/jobs/'+id+'/events');
  es.addEventListener('log',e=>onLog(e.data));
  es.addEventListener('done',e=>{es.close();onDone(JSON.parse(e.data))});
  return es;
}
function appendLine(el,line){el.textContent+=(el.textContent?'\n':'')+line;el.scrollTop=el.scrollHeight}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function setStatus(el,t,c){el.textContent=t;el.className='status '+c}

// ---- determinism audit badge (BE-0145): shared by the Author editor and the Replay views ----
// The audit returns one report per scenario in a file; the badge shows the file's worst grade and
// its overall id-based selector ratio. Read-only and AI-free — it never gates a run.
const GRADE_CLASS={Stable:'stable',Moderate:'moderate',Fragile:'fragile'};
const GRADE_RANK={Stable:0,Moderate:1,Fragile:2};
function gradeSummary(reports){
  const grade=reports.reduce((w,r)=>GRADE_RANK[r.grade]>GRADE_RANK[w]?r.grade:w,'Stable');
  const sel=reports.reduce((a,r)=>a+r.selectors,0);
  const stable=reports.reduce((a,r)=>a+r.stable,0);
  return {grade,text:grade+(sel?' · '+stable+'/'+sel+' id-based':'')};
}
function renderGradeBadge(el,reports){
  if(!reports||!reports.length){el.hidden=true;return;}
  const {grade,text}=gradeSummary(reports);
  el.hidden=false;el.textContent=text;el.className='grade-badge '+GRADE_CLASS[grade];
}

// ---- dark / light toggle (matching CSS-variable blocks live in serve.themes.css) ----
// Two themes only, driven by a checkbox switch: checked == daylight (light), else midnight (dark).
// Default behaviour follows the OS and updates live; a manual flip persists until the OS changes.
const SYS_MQ=matchMedia('(prefers-color-scheme: light)');
function systemTheme(){return SYS_MQ.matches?'daylight':'midnight'}
function currentTheme(){
  return document.documentElement.getAttribute('data-theme')
    ||localStorage.getItem('bajutsu-theme')
    ||systemTheme();
}
function applyTheme(t,persist){
  document.documentElement.setAttribute('data-theme',t);
  try{if(persist)localStorage.setItem('bajutsu-theme',t)}catch(e){}
  const sw=$('#theme');if(sw)sw.checked=(t==='daylight');
}
function initTheme(){
  const sw=$('#theme');
  applyTheme(currentTheme(),false);
  // Manual flip wins for now and is remembered.
  sw.addEventListener('change',()=>applyTheme(sw.checked?'daylight':'midnight',true));
  // An OS theme change drops any manual override and adopts the new system mode.
  const onSys=()=>{try{localStorage.removeItem('bajutsu-theme')}catch(e){}applyTheme(systemTheme(),false)};
  if(SYS_MQ.addEventListener)SYS_MQ.addEventListener('change',onSys);
  else if(SYS_MQ.addListener)SYS_MQ.addListener(onSys);
}

// Phone tier (BE-0072): below this width the single-column stack + per-view switcher take over, so
// the desktop drag-resize / tiling layouts are not applied (they have no touch equivalent and no
// room). One matchMedia, shared by the layout guards and the switcher wiring.
const NARROW_MQ=matchMedia('(max-width:640px)');

// ---- per-view switcher (BE-0072): at the phone tier, bring one stacked pane to full width ----
// Each switcher's buttons carry data-pane; clicking one sets the view's data-pane, which the
// narrow CSS reads to show that pane and collapse the rest. A no-op on desktop (the switcher is
// display:none and every pane shows at once), so it's safe to wire unconditionally.
document.querySelectorAll('.viewswitch').forEach(sw=>{
  const view=sw.closest('main');
  sw.querySelectorAll('.vstab').forEach(b=>b.addEventListener('click',()=>{
    view.dataset.pane=b.dataset.pane;
    sw.querySelectorAll('.vstab').forEach(x=>x.classList.toggle('active',x===b));
  }));
});

// ---- top-level Record / Replay / Crawl views ----
function showView(name){
  document.querySelectorAll('.toptab').forEach(t=>t.classList.toggle('active',t.dataset.view===name));
  $('#view-record').hidden=name!=='record';$('#view-replay').hidden=name!=='replay';$('#view-crawl').hidden=name!=='crawl';$('#view-author').hidden=name!=='author';$('#view-stats').hidden=name!=='stats';$('#view-coverage').hidden=name!=='coverage';
  if(name==='replay')loadHistory();
  if(name==='author')authorInit();
  if(name==='stats')loadStats();
  if(name==='coverage')coverageInit();
}
document.querySelectorAll('.toptab').forEach(t=>t.addEventListener('click',()=>showView(t.dataset.view)));

// ---- config: bound at startup or opened from the UI's file browser ----
// Whether the file-browser source is offered — a hosted deployment omits `fs` from configSources
// (BE-0108), so we hide that block and never call browseFs. Git + Upload are always offered.
let fsSourceEnabled=true;
async function loadConfig(){
  let c;try{c=await (await fetch('/api/config')).json()}catch(e){c={hasConfig:false}}
  fsSourceEnabled=!c.configSources||c.configSources.includes('fs');
  $('#fssrc').hidden=!fsSourceEnabled;
  $('#cfgname').textContent=c.hasConfig?c.config:'no config bound — open one →';
  if(c.hasConfig){await loadShared()}else{openFs()}
}
// Browse the server's --root for a config.yml. Paths returned by /api/fs are absolute and the
// server re-validates every one against --root, so clicking can never escape the browse ceiling.
async function browseFs(dir){
  let d;try{d=await (await fetch('/api/fs'+(dir?('?dir='+encodeURIComponent(dir)):''))).json()}catch(e){d={error:'failed'}}
  if(d.error){$('#fslist').innerHTML='<li class="muted">'+esc(d.error)+'</li>';return}
  $('#fspath').textContent=d.cwd;
  let h='';
  if(d.parent!=null)h+=`<li class="dir" data-dir="${esc(d.parent)}"><span class="ic">&#8593;</span><span class="nm">..</span></li>`;
  h+=d.dirs.map(n=>`<li class="dir" data-dir="${esc(d.cwd+'/'+n)}"><span class="ic">&#128193;</span><span class="nm">${esc(n)}</span></li>`).join('');
  h+=d.files.map(n=>`<li class="file" data-file="${esc(d.cwd+'/'+n)}"><span class="ic">&#128196;</span><span class="nm">${esc(n)}</span></li>`).join('');
  $('#fslist').innerHTML=h||'<li class="muted">empty</li>';
  $('#fslist').querySelectorAll('li[data-dir]').forEach(li=>li.addEventListener('click',()=>browseFs(li.dataset.dir)));
  $('#fslist').querySelectorAll('li[data-file]').forEach(li=>li.addEventListener('click',()=>chooseConfig(li.dataset.file)));
}
function openFs(){$('#fsmodal').hidden=false;if(fsSourceEnabled)browseFs('')}
function closeFs(){$('#fsmodal').hidden=true}
$('#opencfg').addEventListener('click',openFs);
$('#fsclose').addEventListener('click',closeFs);
$('#fsmodal').addEventListener('click',e=>{if(e.target===$('#fsmodal'))closeFs()});

// ---- Claude API key: write-once — shown masked only, never revealed (BE-0136) ----
let keyState={set:false,masked:''};
function setSettingsStatus(t,c){const st=$('#setstatus');st.textContent=t;st.className='keystatus '+(c||'')}
function renderKey(){
  const cur=$('#keycur'),inp=$('#apikey');
  if(keyState.set){
    cur.innerHTML='Current key: <code>'+esc(keyState.masked)+'</code>';
    inp.placeholder='Enter a new key to replace it';
  }else{cur.textContent='No key set yet.';inp.placeholder='sk-ant-…'}
}
async function loadKey(){
  let d;try{d=await (await fetch('/api/apikey')).json()}catch(e){d={set:false}}
  keyState={set:!!d.set,masked:d.masked||''};
  renderKey();
}
async function postKey(value){
  const r=await fetch('/api/apikey',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({value})});
  return r.json();
}
async function clearKey(){
  if(!keyState.set){setSettingsStatus('no key to clear','ng');return}
  setSettingsStatus('clearing…','');
  let d;try{d=await postKey('')}catch(e){d={error:'request failed'}}
  if(d.error){setSettingsStatus(d.error,'ng');return}
  $('#apikey').value='';keyState={set:false,masked:''};renderKey();
  setSettingsStatus('cleared','ok');
}
$('#keyclear').addEventListener('click',clearKey);

// ---- AI provider: Anthropic API (Claude API key), Amazon Bedrock (AWS creds), or Anthropic CLI (ant, OAuth) ----
// Show only the selected provider's own config block — nothing until one is explicitly picked.
function renderProv(){
  const v=$('#provider').value;
  $('#apikeysection').hidden=v!=='api-key';        // the Claude API key is the api-key provider's config
  $('#bedrockfields').hidden=v!=='bedrock';        // region + model id
  $('#antfields').hidden=v!=='ant';                // ant CLI prerequisites (no inputs — OAuth sign-in)
}
async function loadProv(){
  // Explicit selection: don't pre-select a provider from the server's (env-derived) default —
  // the user must consciously pick one, so the #provider placeholder stays until they do. The
  // region/model are still pre-filled so picking Bedrock shows the saved values.
  let d;try{d=await (await fetch('/api/provider')).json()}catch(e){d={}}
  $('#bedrock-region').value=d.region||'';
  $('#bedrock-model').value=d.model||'';
  renderProv();
}
// ---- Claude reachability (BE-0101): the record/crawl surfaces degrade gracefully when Claude
// can't be reached — the tabs read disabled and each view shows an inline explanation naming what
// is missing with a pointer to Settings, instead of only failing on click. Flips live as soon as a
// key is saved / a provider is picked (saveSettings re-runs this). Availability is data from
// /api/provider (claudeAvailable / claudeHint), so the three surfaces never disagree.
async function refreshAiAvailability(){
  let d;try{d=await (await fetch('/api/provider')).json()}catch(e){d={}}
  const ok=d.claudeAvailable!==false, hint=d.claudeHint||'set an API key, configure Bedrock, or sign in with `ant auth login`.';
  document.querySelectorAll('.toptab[data-view="record"],.toptab[data-view="crawl"]').forEach(t=>t.classList.toggle('disabled',!ok));
  [['#rec-aigate','#rec-go'],['#crawl-aigate','#crawl-go']].forEach(([gate,btn])=>{
    const g=$(gate);
    if(g){g.hidden=ok;if(!ok)g.innerHTML='<b>This needs Claude.</b> '+esc(hint)+' <button class="link" type="button" data-open-settings>Open Settings</button>';}
    const b=$(btn);if(b)b.disabled=!ok;
  });
}
// One delegated listener: the gate banner's "Open Settings" button is re-created on every refresh.
document.addEventListener('click',e=>{if(e.target.closest('[data-open-settings]'))openSettings();});
// ---- Settings: one Save persists the provider; the API key saves on every path but Bedrock ----
async function saveSettings(){
  const provider=$('#provider').value,body={provider};
  if(!provider){setSettingsStatus('select an AI provider','ng');return}  // explicit choice required
  if(provider==='bedrock'){
    body.region=$('#bedrock-region').value.trim();
    body.model=$('#bedrock-model').value.trim();
    if(!body.model){setSettingsStatus('enter a Bedrock model id','ng');return}
  }
  setSettingsStatus('saving…','');
  let d;try{d=await (await fetch('/api/provider',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json()}catch(e){d={error:'request failed'}}
  if(d.error){setSettingsStatus(d.error,'ng');return}
  if(provider==='api-key'){  // only the api-key provider needs the key (Bedrock uses AWS creds, ant uses its OAuth token)
    const v=$('#apikey').value.trim();
    if(v){
      let k;try{k=await postKey(v)}catch(e){k={error:'request failed'}}
      if(k.error){setSettingsStatus(k.error,'ng');return}
      $('#apikey').value='';keyState={set:true,masked:k.masked||''};renderKey();
    }
  }
  setSettingsStatus('saved','ok');
  refreshAiAvailability();  // a just-saved key / provider can flip the record/crawl gate live
}
// ---- Settings modal: one panel for the provider + API-key controls ----
function openSettings(){$('#settingsmodal').hidden=false;$('#apikey').value='';setSettingsStatus('','');loadKey();loadProv()}
function closeSettings(){$('#settingsmodal').hidden=true}
$('#opensettings').addEventListener('click',openSettings);
$('#settingsclose').addEventListener('click',closeSettings);
$('#settingsmodal').addEventListener('click',e=>{if(e.target===$('#settingsmodal'))closeSettings()});
$('#provider').addEventListener('change',renderProv);
$('#settingssave').addEventListener('click',saveSettings);
async function chooseConfig(path){
  const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
  const d=await r.json();
  if(d.error){$('#fslist').innerHTML='<li class="muted">'+esc(d.error)+'</li>';return}
  $('#cfgname').textContent=d.config;closeFs();await loadShared();
}
// From-Git picker (BE-0063): POST the github:… spec; the server materializes the checkout, binds
// its config, and repoints its cwd there. Errors (a bad spec, a fetch/auth failure) show inline.
async function chooseGitConfig(){
  const git=$('#gitspec').value.trim();
  const err=$('#gitsrcerr');err.hidden=true;
  if(!git){err.textContent='Enter a github:owner/repo[@ref][:path] spec.';err.hidden=false;return}
  const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({git})});
  const d=await r.json();
  if(d.error){err.textContent=d.error;err.hidden=false;return}
  $('#cfgname').textContent=d.config;closeFs();await loadShared();
}
$('#gitload').addEventListener('click',chooseGitConfig);
$('#gitspec').addEventListener('keydown',e=>{if(e.key==='Enter')chooseGitConfig()});

// ---- shared data: targets, scenarios, simulators (used by both views) ----
async function loadShared(){
  try{targets=await (await fetch('/api/targets')).json()}catch(e){targets=[]}
  // each target carries its primary backend (data-backend) so picking a web target hides the iOS-only UI
  const opts=targets.map(a=>{const n=typeof a==='string'?a:a.name,b=typeof a==='string'?'':(a.backend||'');
    return `<option value="${esc(n)}" data-backend="${esc(b)}">${esc(n)}</option>`;}).join('');
  $('#target').innerHTML=opts;$('#rec-target').innerHTML=opts;$('#crawl-target').innerHTML=opts;$('#au-target').innerHTML=opts;$('#cov-target').innerHTML=opts;
  syncPlatform('#panel-run','#target');
  syncPlatform('#panel-record','#rec-target');
  syncPlatform('#panel-crawl','#crawl-target');
  syncPlatform('#panel-author','#au-target');
  replayCodegen.sync();  // offer the emit valid for the (re)loaded Replay target
  await loadScenarios();
  if(!$('#view-author').hidden)authorRefresh();
  refreshAiAvailability();  // a newly-bound config's ai.keyEnv can change reachability
}
// Scenarios come from the selected target's configured dir, so reload when the Replay target changes.
async function loadScenarios(){
  const target=$('#target').value;
  try{scnFiles=target?await (await fetch('/api/scenarios?target='+encodeURIComponent(target))).json():[]}catch(e){scnFiles=[]}
  $('#scn').innerHTML=scnFiles.map(s=>`<option value="${esc(s.path)}">${esc(s.file)}</option>`).join('');
  showInfo();replayAudit();
}
$('#target').addEventListener('change',()=>{loadScenarios();replayCodegen.sync();replayCodegen.reset();});
async function loadSims(){
  try{sims=await (await fetch('/api/simulators')).json()}catch(e){sims=[]}
  // Replay: multi-select checkboxes (parallel pool).
  const el=$('#sims');
  el.innerHTML=sims.length?sims.map(s=>`<label><input type="checkbox" class="simck" value="${esc(s.udid)}"><span class="dot ${s.booted?'ok':'off'}" title="${s.booted?'booted':'shut down'}"></span><span>${esc(s.name)}</span><span class="rt">${esc(s.runtime)}${s.booted?'':' · off'}</span></label>`).join(''):'<div class="empty">no simulators found</div>';
  el.querySelectorAll('.simck').forEach(c=>c.addEventListener('change',onSimChange));
  // Crawl: multi-select checkboxes too (a parallel pool sharing one screen map — BE-0064).
  const cel=$('#crawl-sims');
  cel.innerHTML=sims.length?sims.map(s=>`<label><input type="checkbox" class="crawl-simck" value="${esc(s.udid)}"><span class="dot ${s.booted?'ok':'off'}" title="${s.booted?'booted':'shut down'}"></span><span>${esc(s.name)}</span><span class="rt">${esc(s.runtime)}${s.booted?'':' · off'}</span></label>`).join(''):'<div class="empty">no simulators found</div>';
  cel.querySelectorAll('.crawl-simck').forEach(c=>c.addEventListener('change',onCrawlSimChange));
  // Record: single-device dropdown ("booted" = whatever is already up).
  const single='<option value="booted">booted (already up)</option>'+sims.map(s=>`<option value="${esc(s.udid)}">${esc(s.name)} · ${esc(s.runtime)}${s.booted?'':' · off'}</option>`).join('');
  $('#rec-device').innerHTML=single;
}

// ---- Record: author a scenario from a goal ----
$('#rec-simrefresh').addEventListener('click',loadSims);
$('#rec-go').addEventListener('click',async()=>{
  const goal=$('#rec-goal').value.trim();
  if(!goal){setStatus($('#rec-status'),'enter a goal first','ng');return}
  if(recPoll)recPoll.close();
  setBusy($('#rec-go'),$('#rec-stop'),true,'Authoring…');$('#rec-out').textContent='';
  $('#rec-yaml').value='';$('#rec-save').disabled=true;$('#rec-yamlinfo').textContent='';recPath=null;
  setStatus($('#rec-status'),'','run');
  const r=await fetch('/api/record',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    goal,target:$('#rec-target').value,
    udid:$('#rec-device').value||'booted',name:$('#rec-name').value.trim()||undefined,
    erase:$('#rec-erase').checked,dismissAlerts:$('#rec-nodismiss').checked?false:undefined,
    headed:$('#rec-headed').checked||undefined})});
  const {jobId,path,error}=await r.json();
  if(error){setStatus($('#rec-status'),error,'ng');setBusy($('#rec-go'),$('#rec-stop'),false);return}
  recPath=path;recJobId=jobId;
  recPoll=streamJob(jobId,line=>appendLine($('#rec-out'),line),recDone);
});
$('#rec-stop').addEventListener('click',()=>cancelJob(recJobId,$('#rec-stop')));
async function recDone(j){
  recPoll=null;recJobId=null;setBusy($('#rec-go'),$('#rec-stop'),false);
  if(j.cancelled){setStatus($('#rec-status'),'cancelled','ng');return}
  setStatus($('#rec-status'),j.ok?'authored ✓':'failed', j.ok?'ok':'ng');
  if(j.ok&&(j.outPath||recPath)){await loadGenerated(j.outPath||recPath);loadScenarios();}
}
async function loadGenerated(path){
  recPath=path;
  try{
    const d=await (await fetch('/api/scenario?target='+encodeURIComponent($('#rec-target').value)+'&path='+encodeURIComponent(path))).json();
    if(d.yaml!=null){$('#rec-yaml').value=d.yaml;$('#rec-save').disabled=false;
      $('#rec-yamlinfo').textContent=path.split('/').pop();}
  }catch(e){}
}
$('#rec-save').addEventListener('click',async()=>{
  if(!recPath)return;
  $('#rec-save').disabled=true;$('#rec-save').textContent='Saving…';
  const r=await fetch('/api/scenario',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({target:$('#rec-target').value,path:recPath,yaml:$('#rec-yaml').value})});
  const d=await r.json();
  $('#rec-save').textContent='Save';$('#rec-save').disabled=false;
  if(d.error){setStatus($('#rec-status'),d.error,'ng')}
  else{setStatus($('#rec-status'),'saved ✓','ok');loadScenarios()}
});

// ---- Replay: scenario info, run, history ----
function showInfo(){
  const f=scnFiles.find(s=>s.path===$('#scn').value),el=$('#names');
  if(!f){el.innerHTML='';return}
  let h='';
  if(f.description)h+=`<div class="finfo">${esc(f.description)}</div>`;
  if(f.scenarios&&f.scenarios.length)h+='<ul class="scnlist">'+f.scenarios.map(s=>`<li><b>${esc(s.name)}</b>${s.description?' &mdash; <span class="sd">'+esc(s.description)+'</span>':''}</li>`).join('')+'</ul>';
  el.innerHTML=h;
}
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
$('#scn').addEventListener('change',()=>{showInfo();replayAudit();replayCodegen.reset();});
function pickedUdids(){return [...$('#sims').querySelectorAll('.simck:checked')].map(c=>c.value)}
function onSimChange(){const n=pickedUdids().length;if(n>0)$('#workers').value=n}
$('#simrefresh').addEventListener('click',loadSims);
$('#go').addEventListener('click',async()=>{
  if(poll)poll.close();
  setBusy($('#go'),$('#stop'),true,'Running…');$('#out').textContent='';
  setStatus($('#status'),'','run');
  const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    scenario:$('#scn').value,target:$('#target').value,udid:pickedUdids().join(',')||'booted',
    workers:parseInt($('#workers').value,10)||1,headed:$('#headed').checked||undefined,
    erase:$('#erasedev').checked||undefined,dismissAlerts:$('#nodismiss').checked?false:undefined})});
  const {jobId,error}=await r.json();
  if(error){setStatus($('#status'),error,'ng');setBusy($('#go'),$('#stop'),false);return}
  runJobId=jobId;
  poll=streamJob(jobId,line=>appendLine($('#out'),line),runDone);
});
$('#stop').addEventListener('click',()=>cancelJob(runJobId,$('#stop')));
function runDone(j){
  poll=null;runJobId=null;setBusy($('#go'),$('#stop'),false);
  if(j.cancelled){setStatus($('#status'),'cancelled','ng');loadHistory();return}
  setStatus($('#status'),j.ok?'PASS':'FAIL', j.ok?'ok':'ng');
  if(j.runId)setReport(j.runId);
  loadHistory();
}
// Show a run's report inline (no iframe): render report.html into a shadow root so its CSS/JS stay
// isolated, plus an "open full report ↗" link to view it as its own page. report.js is root-aware
// (window.__bajutsuReportRoot), so its queries + delegated listeners run against the shadow root.
async function setReport(id,repSel){
  selectedRun=id;
  const rep=$(repSel||'#report');
  rep.innerHTML=`<div class="repbar"><a class="repdl" href="/runs/${esc(id)}/archive.zip" download>⬇ download .zip</a><a class="repopen" href="/runs/${esc(id)}/report.html" target="_blank" rel="noopener">open full report ↗</a></div><div class="rephost"></div>`;
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
function renderReportInShadow(host,html){
  const sh=host.shadowRoot||host.attachShadow({mode:'open'});
  const doc=new DOMParser().parseFromString(html,'text/html');
  const css=((doc.querySelector('style')||{}).textContent||'').replace(/:root/g,':host')
    .replace(/(^|[\s,>}])body([\s{])/g,'$1:host$2');
  sh.innerHTML=`<style>:host{display:block}\n${css}</style>${doc.body.innerHTML}`;
}
async function loadStats(){
  const host=$('#stats-host');
  let html;
  // Treat a network error or a non-2xx (e.g. 401/500) as unavailable, and render the error into the
  // shadow root so a failed refresh replaces the stale dashboard instead of leaving it on screen.
  try{const r=await fetch('/stats');if(!r.ok)throw 0;html=await r.text();}
  catch(e){(host.shadowRoot||host.attachShadow({mode:'open'})).innerHTML='<div style="color:#6e6e73;font-style:italic">stats unavailable</div>';return;}
  renderReportInShadow(host,html);
}
async function loadHistory(){
  let runs;try{runs=await (await fetch('/api/runs')).json()}catch(e){return}
  const tab=$('#histtab');if(tab)tab.textContent='History'+(runs.length?` (${runs.length})`:'');
  const ul=$('#history');
  if(!runs.length){ul.innerHTML='<li class="muted">no runs yet</li>';return}
  ul.innerHTML=runs.map(r=>`<li data-id="${r.id}"${r.id===selectedRun?' class="sel"':''}><span class="dot ${r.ok?'ok':'ng'}"></span><span class="hid">${r.id}</span><span class="hsum">${r.passed}/${r.total}${r.scenarios.length?' · '+r.scenarios.join(', '):''}</span></li>`).join('');
  ul.querySelectorAll('li[data-id]').forEach(li=>li.addEventListener('click',()=>{setReport(li.dataset.id);ul.querySelectorAll('li').forEach(x=>x.classList.remove('sel'));li.classList.add('sel')}));
}
$('#refresh').addEventListener('click',loadHistory);
$('#stats-refresh').addEventListener('click',loadStats);

// Coverage (BE-0146): POST the target (+ optional run set) to /api/coverage and render the returned
// self-contained report into a shadow root — the same isolation as loadStats. The aggregation stays
// server-side (the CLI's `bajutsu coverage`), so nothing is recomputed in JS; the view only displays.
async function coverageInit(){
  // Fill the run picker from the same history the Replay view lists; a target is already populated by
  // loadShared. Selecting runs is optional — it folds in the endpoint / observed-id dimensions.
  let runs;try{runs=await (await fetch('/api/runs')).json()}catch(e){runs=[]}
  $('#cov-runs').innerHTML=runs.map(r=>`<option value="${esc(r.id)}">${esc(r.id)}${r.scenarios&&r.scenarios.length?' · '+esc(r.scenarios.join(', ')):''}</option>`).join('');
}
async function loadCoverage(){
  const host=$('#cov-host');
  // Render errors into the shadow root too (once attached it shadows the light-DOM empty state), so a
  // failed recompute replaces the stale map — the same reasoning as loadStats.
  const fail=msg=>{(host.shadowRoot||host.attachShadow({mode:'open'})).innerHTML=`<div style="color:#6e6e73;font-style:italic">${esc(msg)}</div>`};
  const target=$('#cov-target').value;
  if(!target){fail('Open a config and pick a target first.');return}
  const runs=[...$('#cov-runs').selectedOptions].map(o=>o.value);
  let resp;
  try{const r=await fetch('/api/coverage',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target,runs})});
    resp=await r.json();if(!r.ok)throw new Error(resp.error||'coverage failed');}
  catch(e){fail(e.message||'coverage unavailable');return}
  renderReportInShadow(host,resp.html);
}
$('#cov-go').addEventListener('click',loadCoverage);
function showTab(name){
  document.querySelectorAll('#view-replay .tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===name));
  $('#panel-run').hidden=name!=='run';$('#panel-history').hidden=name!=='history';
  if(name==='history')loadHistory();
}
document.querySelectorAll('#view-replay .tab').forEach(t=>t.addEventListener('click',()=>showTab(t.dataset.tab)));

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
  $('#cfgname').textContent=d.config;closeFs();await loadShared();
}
$('#up-pick').addEventListener('click',()=>$('#up-file').click());
$('#up-file').addEventListener('change',e=>{const f=e.target.files[0];e.target.value='';if(f)chooseUploadConfig(f);});  // clear value so re-picking the same .zip still fires change
(function(){
  const drop=$('#up-drop');if(!drop)return;
  const stop=e=>{e.preventDefault();e.stopPropagation();};
  ['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{stop(e);drop.classList.add('dragover');}));
  ['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{stop(e);drop.classList.remove('dragover');}));
  drop.addEventListener('drop',e=>{const f=e.dataTransfer.files[0];if(f)chooseUploadConfig(f);});
})();

// ---- Crawl: explore the app and watch the screen map grow live ----
let crawlPoll=null,crawlJobId=null,crawlRunId=null;
function crawlPickedUdids(){return [...$('#crawl-sims').querySelectorAll('.crawl-simck:checked')].map(c=>c.value)}
function onCrawlSimChange(){const n=crawlPickedUdids().length;if(n>0)$('#crawl-workers').value=n}
$('#crawl-simrefresh').addEventListener('click',loadSims);
$('#crawl-go').addEventListener('click',async()=>{
  if(crawlPoll)crawlPoll.close();
  setBusy($('#crawl-go'),$('#crawl-stop'),true,'Crawling…');
  $('#crawl-out').textContent='';$('#crawl-counts').textContent='';
  $('#crawl-graph').innerHTML='<div class="empty">Launching the app and reaching the first screen…</div>';
  setStatus($('#crawl-status'),'','run');
  const r=await fetch('/api/crawl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    target:$('#crawl-target').value,udid:crawlPickedUdids().join(',')||'booted',
    workers:parseInt($('#crawl-workers').value,10)||1,
    maxScreens:parseInt($('#crawl-maxscreens').value,10)||50,maxSteps:parseInt($('#crawl-maxsteps').value,10)||200,
    erase:$('#crawl-erase').checked,
    dismissAlerts:$('#crawl-nodismiss').checked?false:undefined,headed:$('#crawl-headed').checked||undefined})});
  const {jobId,runId,error}=await r.json();
  if(error){setStatus($('#crawl-status'),error,'ng');setBusy($('#crawl-go'),$('#crawl-stop'),false);return}
  crawlJobId=jobId;crawlRunId=runId;
  crawlPoll=streamJob(jobId,line=>{
    appendLine($('#crawl-out'),line);
    if(crawlRunId)loadGraph(crawlRunId);  // redraw the streamed screenmap.json as it grows
  },crawlDone);
});
$('#crawl-stop').addEventListener('click',()=>cancelJob(crawlJobId,$('#crawl-stop')));
async function crawlDone(j){
  crawlPoll=null;crawlJobId=null;setBusy($('#crawl-go'),$('#crawl-stop'),false);
  if(crawlRunId)await loadGraph(crawlRunId);  // final redraw
  if(j.cancelled){setStatus($('#crawl-status'),'stopped','ng');return}
  setStatus($('#crawl-status'),j.ok?'done ✓':'failed', j.ok?'ok':'ng');
}
async function loadGraph(runId){
  let data;try{data=await (await fetch('/runs/'+runId+'/screenmap.json')).json()}catch(e){return}
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
(function(){
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
})();
// Whether unit `other` is directly connected to `uid` by some edge (either direction).
function isAdjacent(wrap,uid,other){
  if(!other)return false;
  for(const p of wrap.querySelectorAll('.edge'))
    if((p.dataset.a===uid&&p.dataset.b===other)||(p.dataset.b===uid&&p.dataset.a===other))return true;
  return false;
}
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
// Tap a pruned branch to resume exploring it: re-launch the crawl against the SAME run, seeded to
// replay to that screen and perform the pruned op, appending whatever it finds to the live map.
async function resumePruned(src,key){
  if(!crawlRunId){setStatus($('#crawl-status'),'no active run to resume','ng');return}
  if(crawlPoll)crawlPoll.close();
  setBusy($('#crawl-go'),$('#crawl-stop'),true,'Resuming…');
  setStatus($('#crawl-status'),'','run');
  const r=await fetch('/api/crawl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    target:$('#crawl-target').value,udid:crawlPickedUdids()[0]||'booted',  // a resume is a single-branch walk
    maxScreens:parseInt($('#crawl-maxscreens').value,10)||50,maxSteps:parseInt($('#crawl-maxsteps').value,10)||200,
    dismissAlerts:$('#crawl-nodismiss').checked?false:undefined,headed:$('#crawl-headed').checked||undefined,
    runId:crawlRunId,resumeSrc:src,resumeKey:key})});
  const {jobId,runId,error}=await r.json();
  if(error){setStatus($('#crawl-status'),error,'ng');setBusy($('#crawl-go'),$('#crawl-stop'),false);return}
  crawlJobId=jobId;crawlRunId=runId;
  crawlPoll=streamJob(jobId,line=>{
    appendLine($('#crawl-out'),line);
    if(crawlRunId)loadGraph(crawlRunId);
  },crawlDone);
}
// Toggle pruning of duplicate global ops in the plan tree, re-rendering it in place.
(function(){const t=$('#crawl-prune');if(t)t.addEventListener('change',()=>{prunePlan=t.checked;if(crawlGraphData)renderPlan(crawlGraphData)})})();

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
  $('#shotmodal').hidden=false;
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
function closeShot(){$('#shotmodal').hidden=true;$('#shotimg').removeAttribute('src');$('#shothots').innerHTML='';shotFp=null;hideHi()}
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

// iOS-only device UI (simulators, device pickers, erase, alert-dismiss) shows only for an iOS
// backend; web-only UI (the headed/show-browser toggle) shows only for web. The backend is fixed
// per app by config (no UI override), so this follows the selected app's backend (data-backend) —
// picking a web app hides the iOS UI. Applies to Record/Replay/Crawl.
function isIosBackend(v){v=(v||'').trim().toLowerCase();return v===''||v==='idb'||v==='ios'||v==='xcuitest';}
function appBackend(appSel){const a=$(appSel),o=a&&a.selectedOptions&&a.selectedOptions[0];return (o&&o.dataset.backend)||'';}
function syncPlatform(panelSel,appSel){
  const ios=isIosBackend(appBackend(appSel));
  // Inline display wins over the layout rules on .hhead/.sims/.checks/.row (the `hidden`
  // attribute's UA display:none would lose to them); removeProperty restores the CSS value.
  document.querySelectorAll(panelSel+' .iosonly').forEach(el=>{
    if(ios)el.style.removeProperty('display');else el.style.setProperty('display','none','important');
  });
  document.querySelectorAll(panelSel+' .webonly').forEach(el=>{
    if(ios)el.style.setProperty('display','none','important');else el.style.removeProperty('display');
  });
}
function wirePlatform(panelSel,appSel){
  const re=()=>syncPlatform(panelSel,appSel);
  if($(appSel))$(appSel).addEventListener('change',re);  // selecting an app re-evaluates the platform
  re();
}
wirePlatform('#panel-run','#target');
wirePlatform('#panel-record','#rec-target');
wirePlatform('#panel-crawl','#crawl-target');
wirePlatform('#panel-author','#au-target');

// Shared codegen wiring for a view (BE-0137): an emit selector synced to the target's backend, a
// Generate button that POSTs the selected scenario to /api/codegen, and a result panel with copy /
// download. The Author and Replay views both call this — one endpoint, one client behaviour. `ids`
// names the view's elements, `getScenario` returns the scenario path (or "" when none is selected).
// Returns {sync, reset} so the caller can re-pick the emit and drop a stale result on its own events.
function makeCodegen(ids,targetSel,getScenario){
  let result=null;
  const setStat=(msg,cls)=>{const s=$(ids.status);if(s){s.textContent=msg;s.className='status'+(cls?' '+cls:'');}};
  function reset(){$(ids.panel).hidden=true;result=null;}
  function sync(){$(ids.emit).innerHTML=isIosBackend(appBackend(targetSel))
    ?'<option value="xcuitest">XCUITest</option>'
    :'<option value="playwright">Playwright</option>';}
  $(ids.btn).addEventListener('click',async()=>{
    const scenario=getScenario();
    if(!scenario){setStat('Load a scenario first.');return;}
    const emit=$(ids.emit).value;
    $(ids.btn).disabled=true;setStat('Generating '+emit+' code…','run');
    try{
      const r=await fetch('/api/codegen',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({target:$(targetSel).value,scenario:scenario,emit:emit})});
      const d=await r.json();
      if(d.error){setStat(d.error,'ng');return;}
      result=d;$(ids.title).textContent=d.filename;$(ids.code).textContent=d.code;$(ids.panel).hidden=false;
      setStat('Generated '+d.filename,'ok');
    }catch(e){setStat(String(e),'ng');}
    finally{$(ids.btn).disabled=false;}
  });
  $(ids.copy).addEventListener('click',async()=>{
    if(!result)return;
    try{await navigator.clipboard.writeText(result.code);setStat('Copied '+result.filename+' to clipboard','ok');}
    catch(e){setStat('Copy failed: '+e,'ng');}
  });
  $(ids.download).addEventListener('click',()=>{
    if(!result)return;
    const url=URL.createObjectURL(new Blob([result.code],{type:'text/plain'}));
    const a=document.createElement('a');a.href=url;a.download=result.filename;
    // Revoke on the next tick: revoking synchronously after click() can truncate the download in
    // some browsers (Safari) before the blob navigation starts.
    document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),0);
  });
  $(ids.close).addEventListener('click',reset);
  return {sync,reset};
}
const CODEGEN_IDS=pfx=>({emit:'#'+pfx+'-emit',btn:'#'+pfx+'-codegen',panel:'#'+pfx+'-codegen-panel',
  title:'#'+pfx+'-codegen-title',code:'#'+pfx+'-codegen-code',copy:'#'+pfx+'-codegen-copy',
  download:'#'+pfx+'-codegen-download',close:'#'+pfx+'-codegen-close'});

// Replay view: generate from the selected scenario (#scn) + target (#target); status shares #status.
const replayCodegen=makeCodegen({...CODEGEN_IDS('rp'),status:'#status'},'#target',()=>$('#scn').value);

// Resizable panels: each view has gutter bars between its grid columns. Dragging one resizes the
// column to its left via a CSS var on the <main>'s grid-template; widths persist in localStorage.
const SPLIT_KEY='bajutsu-splits';
function restoreSplits(){
  let v={};try{v=JSON.parse(localStorage.getItem(SPLIT_KEY)||'{}')}catch(e){}
  document.querySelectorAll('main .gutter').forEach(g=>{const w=v[g.dataset.var];if(w)g.closest('main').style.setProperty(g.dataset.var,w)});
}
function initSplitters(){
  let drag=null;
  document.querySelectorAll('main .gutter').forEach(g=>g.addEventListener('mousedown',e=>{
    e.preventDefault();
    const row=g.classList.contains('row'),b=g.previousElementSibling.getBoundingClientRect();
    drag={g,main:g.closest('main'),v:g.dataset.var,row,pos:row?e.clientY:e.clientX,size:row?b.height:b.width,min:+g.dataset.min||120,max:+g.dataset.max||900};
    g.classList.add('dragging');document.body.style.userSelect='none';document.body.style.cursor=row?'row-resize':'col-resize';
  }));
  window.addEventListener('mousemove',e=>{
    if(!drag)return;
    drag.main.style.setProperty(drag.v,Math.max(drag.min,Math.min(drag.max,drag.size+(drag.row?e.clientY:e.clientX)-drag.pos))+'px');
  });
  window.addEventListener('mouseup',()=>{
    if(!drag)return;
    drag.g.classList.remove('dragging');document.body.style.userSelect='';document.body.style.cursor='';
    let v={};try{v=JSON.parse(localStorage.getItem(SPLIT_KEY)||'{}')}catch(e){}
    v[drag.v]=drag.main.style.getPropertyValue(drag.v);
    try{localStorage.setItem(SPLIT_KEY,JSON.stringify(v))}catch(e){}
    drag=null;
  });
}
// At the phone tier the persisted desktop column widths and the splitter drags are skipped — the
// single-column stack takes over, so applying saved px widths would only fight the reflow (BE-0072).
if(!NARROW_MQ.matches){restoreSplits();initSplitters();}

// Tiling layout (Record/Replay): drag a panel's grip and drop on another panel's edge to split
// that way (up/down/left/right), or on its center to swap the two. Dividers resize both axes and
// the layout tree persists in localStorage. A node is either a leaf (panel key) or a split
// {d:'row'|'col', k:[node…], s:[size…]}. All three views (Record/Replay/Crawl) tile.
function initTiling(){
  const KEY='bajutsu-tiles';
  const SPECS=[
    {id:'view-replay',def:{d:'row',k:['controls','log','report'],s:[1,1,2]},sel:{controls:'.left',log:'.logpanel',report:'.report'}},
    {id:'view-record',def:{d:'row',k:['controls',{d:'col',k:['log','yaml'],s:[1,1]}],s:[1,2]},sel:{controls:'.left',log:'.rec-stack .logpanel',yaml:'.rec-stack .yamlpanel'}},
    {id:'view-crawl',def:{d:'row',k:['controls','graph',{d:'col',k:['plan','console'],s:[1,1]}],s:[1,2,1]},sel:{controls:'.left',graph:'.crawl-graph-panel',plan:'.crawl-plan-panel',console:'.crawl-console-panel'}},
  ];
  const leaves=n=>typeof n==='string'?[n]:n.k.flatMap(leaves);
  const valid=(t,keys)=>{try{const l=leaves(t);return l.length===keys.length&&new Set(l).size===l.length&&l.every(k=>keys.includes(k));}catch(e){return false;}};
  let saved={};
  try{saved=JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){}
  const views=[];
  let pdrag=null,ind=null;
  const save=()=>{const s={};views.forEach(V=>s[V.spec.id]=V.tree);try{localStorage.setItem(KEY,JSON.stringify(s));}catch(e){}};
  const keyOf=(V,el)=>Object.keys(V.panel).find(k=>V.panel[k]===el);
  function render(V,node){
    if(typeof node==='string'){const el=V.panel[node];el.classList.add('tile-leaf');el.style.height='auto';el.style.minWidth='0';el.style.minHeight='0';return el;}
    const sp=document.createElement('div');sp.className='tile-split tile-'+node.d;
    node.k.forEach((kid,i)=>{
      if(i>0){
        const dv=document.createElement('div');dv.className='tile-divider';
        // A stable data-testid so a scenario can grab one specific divider (dogfood drag-resize):
        // name it by the two leaves it separates when both are panels, else a per-view running index.
        const a=node.k[i-1],b=node.k[i];
        dv.dataset.testid=V.id+'.divider.'+((typeof a==='string'&&typeof b==='string')?a+'-'+b:'n'+(V.dvc++));
        dv.addEventListener('mousedown',e=>startResize(V,e,dv,node,i));sp.appendChild(dv);
      }
      const el=render(V,kid);el.style.flex=(node.s[i]??1)+' 1 0';sp.appendChild(el);
    });
    return sp;
  }
  // Each leaf's weight share (integer %) within its parent split, mirrored into a visually-hidden
  // readout on the panel so a scenario can assert the layout numerically (the web backend reads no
  // geometry, only the accessibility tree). It reflects node.s — the very weights a resize mutates —
  // so a divider drag that wrongly disturbs a non-adjacent panel shows up as a changed readout.
  function shareInto(node,out){
    if(typeof node==='string')return;
    const sum=node.s.reduce((a,b)=>a+(+b||0),0)||1;
    node.k.forEach((kid,i)=>{if(typeof kid==='string')out[kid]=Math.round(100*(+node.s[i]||0)/sum);else shareInto(kid,out);});
  }
  function reflectSizes(V){
    const out={};shareInto(V.tree,out);
    for(const k in V.panel){
      let r=V.panel[k].querySelector(':scope>.tile-size');
      if(!r){r=document.createElement('span');r.className='tile-size sr-only';r.dataset.testid=V.id+'.size.'+k;V.panel[k].appendChild(r);}
      r.textContent=k in out?String(out[k]):'';
    }
  }
  const rebuild=V=>{V.dvc=0;const r=render(V,V.tree);r.classList.add('tile-root');V.view.replaceChildren(r);reflectSizes(V);};
  function startResize(V,e,dv,node,i){
    e.preventDefault();const row=node.d==='row',a=dv.previousElementSibling,b=dv.nextElementSibling;
    const ra=a.getBoundingClientRect(),rb=b.getBoundingClientRect(),tot=row?ra.width+rb.width:ra.height+rb.height,start=row?e.clientX:e.clientY,s0=row?ra.width:ra.height;
    // s holds flex weights, not pixels: redistribute only this pair's combined weight so the other
    // siblings keep their proportions. Mapping pixels→weight by the pair's px↔weight ratio keeps the
    // pair's total weight invariant across moves.
    const w=(node.s[i-1]??1)+(node.s[i]??1);
    dv.classList.add('dragging');document.body.style.userSelect='none';document.body.style.cursor=row?'col-resize':'row-resize';
    const mv=ev=>{const n0=Math.max(80,Math.min(tot-80,s0+(row?ev.clientX:ev.clientY)-start)),wa=w*n0/tot,wb=w-wa;node.s[i-1]=wa;node.s[i]=wb;a.style.flex=wa+' 1 0';b.style.flex=wb+' 1 0';reflectSizes(V);};
    const up=()=>{window.removeEventListener('mousemove',mv);window.removeEventListener('mouseup',up);dv.classList.remove('dragging');document.body.style.userSelect='';document.body.style.cursor='';save();};
    window.addEventListener('mousemove',mv);window.addEventListener('mouseup',up);
  }
  const zdir=z=>(z==='left'||z==='right')?'row':'col';
  function removeLeaf(n,key){
    if(typeof n==='string')return n===key?null:n;
    const k=[],s=[];n.k.forEach((c,i)=>{const r=removeLeaf(c,key);if(r!==null){k.push(r);s.push(n.s[i]??1);}});
    return k.length===0?null:k.length===1?k[0]:{d:n.d,k,s};
  }
  function insertBeside(n,tgt,key,z){
    const dir=zdir(z),before=(z==='left'||z==='top');
    if(typeof n==='string')return n===tgt?{d:dir,k:before?[key,n]:[n,key],s:[1,1]}:n;
    const i=n.k.findIndex(c=>c===tgt);
    if(i>=0){
      if(n.d===dir){const k=n.k.slice(),s=n.s.slice();k.splice(before?i:i+1,0,key);s.splice(before?i:i+1,0,s[i]??1);return {d:n.d,k,s};}
      return {d:n.d,k:n.k.map((c,j)=>j===i?{d:dir,k:before?[key,c]:[c,key],s:[1,1]}:c),s:n.s.slice()};
    }
    return {d:n.d,k:n.k.map(c=>insertBeside(c,tgt,key,z)),s:n.s.slice()};
  }
  const swapKeys=(n,a,b)=>typeof n==='string'?(n===a?b:n===b?a:n):{d:n.d,k:n.k.map(c=>swapKeys(c,a,b)),s:n.s.slice()};
  function normalize(n){
    if(typeof n==='string')return n;
    const k=[],s=[];n.k.forEach((c,i)=>{const x=normalize(c);if(typeof x!=='string'&&x.d===n.d){k.push(...x.k);s.push(...x.s);}else{k.push(x);s.push(n.s[i]??1);}});
    return k.length===1?k[0]:{d:n.d,k,s};
  }
  function showInd(t,z){
    if(!ind){ind=document.createElement('div');ind.className='tile-dropind';document.body.appendChild(ind);}
    const r=t.getBoundingClientRect();let x=r.left,y=r.top,w=r.width,h=r.height;
    if(z==='left')w/=2;else if(z==='right'){x+=w/2;w/=2;}else if(z==='top')h/=2;else if(z==='bottom'){y+=h/2;h/=2;}
    ind.style.cssText=`display:block;left:${x}px;top:${y}px;width:${w}px;height:${h}px`;
  }
  const hideInd=()=>{if(ind)ind.style.display='none';};
  SPECS.forEach(spec=>{
    const view=document.getElementById(spec.id);if(!view)return;
    const panel={};for(const k in spec.sel){const el=view.querySelector(spec.sel[k]);if(el)panel[k]=el;}
    const keys=Object.keys(panel);if(!keys.length)return;
    const V={spec,view,panel,keys,id:spec.id.replace('view-',''),dvc:0,tree:(saved[spec.id]&&valid(saved[spec.id],keys))?saved[spec.id]:spec.def};
    keys.forEach(k=>{
      const g=document.createElement('div');g.className='tile-grip';g.title='drag to move / swap';g.textContent='⠿';
      g.addEventListener('mousedown',e=>{e.preventDefault();pdrag={V,key:k};panel[k].classList.add('tile-dragging');document.body.classList.add('reordering-active');document.body.style.userSelect='none';document.body.style.cursor='grabbing';});
      panel[k].appendChild(g);
    });
    rebuild(V);views.push(V);
  });
  window.addEventListener('mousemove',e=>{
    if(!pdrag)return;
    const el=document.elementFromPoint(e.clientX,e.clientY),t=el&&el.closest('.tile-leaf');
    if(!t||!pdrag.V.view.contains(t)||t===pdrag.V.panel[pdrag.key]){pdrag.t=null;hideInd();return;}
    const r=t.getBoundingClientRect(),zx=(e.clientX-r.left)/r.width,zy=(e.clientY-r.top)/r.height,mn=Math.min(zx,1-zx,zy,1-zy);
    const z=mn>=0.28?'center':mn===zx?'left':mn===1-zx?'right':mn===zy?'top':'bottom';
    pdrag.t=t;pdrag.tkey=keyOf(pdrag.V,t);pdrag.z=z;showInd(t,z);
  });
  window.addEventListener('mouseup',()=>{
    if(!pdrag)return;const V=pdrag.V;
    if(pdrag.t&&pdrag.tkey&&pdrag.tkey!==pdrag.key){
      const bak=JSON.stringify(V.tree);
      try{
        V.tree=pdrag.z==='center'?swapKeys(V.tree,pdrag.key,pdrag.tkey):normalize(insertBeside(removeLeaf(V.tree,pdrag.key),pdrag.tkey,pdrag.key,pdrag.z));
        if(!valid(V.tree,V.keys))V.tree=JSON.parse(bak);
      }catch(err){V.tree=JSON.parse(bak);}
      rebuild(V);save();
    }
    V.panel[pdrag.key].classList.remove('tile-dragging');document.body.classList.remove('reordering-active');document.body.style.userSelect='';document.body.style.cursor='';hideInd();pdrag=null;
  });
}
// Likewise the drag-to-split/swap tiling is a desktop power feature with no touch equivalent and no
// room on a phone; skip it at the narrow tier so the markup stays in its single-column stack form.
if(!NARROW_MQ.matches)initTiling();

// ===========================================================================
// Author tab (BE-0098) — one open scenario, three modes (Capture / Edit / Enrich)
// Unifies the former Capture (BE-0012) and Editor (BE-0013) tabs and the Enrich
// panel (BE-0014). Target / scenario / YAML / steps / Save are shared; a mode
// switcher picks what a screenshot click does. Switching mode never reloads the
// scenario or drops unsaved YAML edits — the mode-specific state is scoped to the
// one open scenario.
// ===========================================================================
(function(){
  let mode='capture';        // capture | edit | enrich
  let auInited=false;
  // Edit / Enrich state (scoped to the open scenario).
  let auSteps=[];            // [{stepId, action, fields, screenshotUrl, elementsUrl}]
  let auIdx=-1;              // currently displayed step index
  let auPath='';             // scenario file path for save
  let auResolvedSel=null;    // last resolved selector from picker
  let enrichResult=null;     // last enrichment response {expect, settle, note}
  // Capture state.
  let capActive=false;
  // Inline validation + schema assistance (BE-0138).
  let auSchema=null;         // scenario JSON Schema, fetched once for completion / hover
  let auDiagnostics=[];      // last /api/lint findings [{line, column, message, severity}]
  let auLintTimer=null;      // debounce handle for live validation
  let auCharW=0;             // measured monospace char width, for caret ↔ pixel math
  const AU_LH=18, AU_PADX=8, AU_PADY=6;  // must match textarea.yaml line-height / padding in serve.css

  // ---- mode switching ----
  function setMode(m){
    mode=m;
    document.querySelectorAll('.modetab').forEach(b=>b.classList.toggle('active',b.dataset.mode===m));
    document.querySelectorAll('#view-author .au-cap').forEach(e=>e.hidden=m!=='capture');
    document.querySelectorAll('#view-author .au-edit').forEach(e=>e.hidden=m!=='edit');
    document.querySelectorAll('#view-author .au-enrich').forEach(e=>e.hidden=m!=='enrich');
    document.querySelectorAll('#view-author .au-loadrow').forEach(e=>e.hidden=m==='capture');
    // The proposal panel belongs to Enrich; leaving the mode hides any open proposal.
    if(m!=='enrich')$('#au-enrich-panel').hidden=true;
    // Capture starts fresh with no saved scenario, so codegen has nothing to export there.
    if(m==='capture'){auCodegenReset();$('#au-codegen').disabled=true;}
  }
  document.querySelectorAll('.modetab').forEach(b=>b.addEventListener('click',()=>setMode(b.dataset.mode)));

  // ---- Capture mode (BE-0012): record actions by clicking on a screenshot ----
  document.querySelectorAll('input[name="au-mode"]').forEach(r=>r.addEventListener('change',()=>{
    $('#au-type-field').hidden=r.value!=='type'||!r.checked;
  }));

  function capMode(){
    const checked=document.querySelector('input[name="au-mode"]:checked');
    return checked?checked.value:'tap';
  }

  $('#au-start').addEventListener('click',async()=>{
    const target=$('#au-target').value;
    if(!target){$('#au-status').textContent='Select a target first.';return;}
    $('#au-status').textContent='Starting capture…';$('#au-status').className='status run';
    try{
      const r=await fetch('/api/capture/start',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({target})});
      const d=await r.json();
      if(!r.ok||d.error){$('#au-status').textContent=d.error||'failed';$('#au-status').className='status ng';return;}
      capActive=true;
      $('#au-start').hidden=true;$('#au-finish').hidden=false;
      $('#au-placeholder').hidden=true;$('#au-screenshot').hidden=false;
      $('#au-screenshot').src='/api/capture/screenshot?t='+Date.now();
      $('#au-status').textContent='Click on the screenshot to capture actions.';$('#au-status').className='status ok';
      $('#au-steplist').innerHTML='';$('#au-step-count').textContent='0 steps';
    }catch(e){$('#au-status').textContent=String(e);$('#au-status').className='status ng';}
  });

  async function capMark(nx,ny){
    if(!capActive)return;
    const m=capMode();
    const body={kind:m,point:[nx,ny]};
    if(m==='type')body.text=$('#au-text').value||'';
    $('#au-status').textContent='Resolving…';$('#au-status').className='status run';
    try{
      const r=await fetch('/api/capture/mark',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(body)});
      const d=await r.json();
      if(d.refused){$('#au-status').textContent='Refused: '+d.refused;$('#au-status').className='status ng';return;}
      if(d.ambiguity){
        const ids=d.ambiguity.map(a=>a.identifier||a.label||'?').join(', ');
        $('#au-status').textContent='Ambiguous: '+ids;$('#au-status').className='status ng';
        return;
      }
      if(d.error){$('#au-status').textContent=d.error;$('#au-status').className='status ng';return;}
      const sel=d.selector;
      const desc=sel.id?('#'+sel.id):(sel.label||'?');
      const li=document.createElement('li');
      li.textContent=m+' '+desc;
      if(m==='type')li.textContent+=' = "'+($('#au-text').value||'')+'"';
      $('#au-steplist').appendChild(li);
      const count=$('#au-steplist').children.length;
      $('#au-step-count').textContent=count+' step'+(count===1?'':'s');
      $('#au-feedback').hidden=false;
      $('#au-rung').textContent=d.rung;$('#au-rung').className='au-rung rung-'+d.rung;
      $('#au-sel').textContent=desc;
      $('#au-screenshot').src='/api/capture/screenshot?t='+Date.now();
      $('#au-status').textContent='Captured: '+m+' '+desc;$('#au-status').className='status ok';
    }catch(e){$('#au-status').textContent=String(e);$('#au-status').className='status ng';}
  }

  $('#au-finish').addEventListener('click',async()=>{
    if(!capActive)return;
    $('#au-status').textContent='Saving…';$('#au-status').className='status run';
    try{
      const r=await fetch('/api/capture/finish',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({target:$('#au-target').value})});
      const d=await r.json();
      capActive=false;
      $('#au-start').hidden=false;$('#au-finish').hidden=true;
      if(d.error){$('#au-status').textContent=d.error;$('#au-status').className='status ng';return;}
      const path=d.path||'(unsaved)';
      $('#au-status').textContent='Saved to '+path;$('#au-status').className='status ok';
      // Flow the captured scenario into Edit mode so it can be refined without a tab switch.
      if(d.path)await auOpenSaved(d.path);
    }catch(e){$('#au-status').textContent=String(e);$('#au-status').className='status ng';}
  });

  // Load a just-captured file into Edit state: refresh the scenario dropdown, select it, load it.
  async function auOpenSaved(path){
    await auLoadScenarios();
    const opt=Array.from($('#au-scenario').options).find(o=>o.value.split('|')[0]===path);
    if(!opt){
      // The saved file didn't surface in the target's scenario list — keep the Finish success
      // visible and let the author open it from Edit, rather than silently loading a stale pick.
      $('#au-status').textContent='Saved to '+path+' — switch to Edit to refine it.';$('#au-status').className='status ok';
      return;
    }
    $('#au-scenario').value=opt.value;
    setMode('edit');
    await auLoad();
  }

  // ---- Edit mode (BE-0013): screenshot picker + structured YAML editing ----
  async function auLoadScenarios(){
    const target=$('#au-target').value;
    let files=[];
    try{files=target?await (await fetch('/api/scenarios?target='+encodeURIComponent(target))).json():[];}catch(e){files=[];}
    const opts=files.map(s=>
      (s.scenarios||[]).map(sc=>`<option value="${esc(s.path)}|${esc(sc.name)}">${esc(s.file)} — ${esc(sc.name)}</option>`)
    ).flat().join('');
    $('#au-scenario').innerHTML=opts||'<option value="">—</option>';
    await auLoadRuns();
  }

  async function auLoadRuns(){
    let runs=[];
    try{runs=await (await fetch('/api/runs')).json();}catch(e){runs=[];}
    const opts=runs.map(r=>{
      const label=r.id+' '+(r.ok?'✓':'✗');
      return `<option value="${esc(r.id)}">${esc(label)}</option>`;
    }).join('');
    $('#au-run').innerHTML=opts||'<option value="">—</option>';
  }

  function auSelectorYaml(sel){
    if(sel.id)return '{ id: '+sel.id+' }';
    if(sel.label&&sel.index!=null)return '{ label: '+sel.label+', index: '+sel.index+' }';
    if(sel.label)return '{ label: '+sel.label+' }';
    return JSON.stringify(sel);
  }

  function auStepLabel(s){
    const a=s.action||'?';
    const f=s.fields||{};
    if(a==='tap'||a==='doubleTap'||a==='longPress'){
      const sel=f.id?('#'+f.id):(f.label||'?');
      return a+' '+sel;
    }
    if(a==='type'){
      const into=f.into||{};
      const sel=into.id?('#'+into.id):(into.label||'?');
      return 'type '+sel+' = "'+(f.text||'')+'"';
    }
    if(a==='wait')return 'wait';
    if(a==='assert')return 'assert';
    return a;
  }

  async function auLoad(){
    const combo=$('#au-scenario').value;
    if(!combo){$('#au-status').textContent='Select a scenario.';return;}
    const [path,scnName]=combo.split('|');
    const target=$('#au-target').value;
    const runId=$('#au-run').value;
    auPath=path;
    $('#au-status').textContent='Loading…';$('#au-status').className='status run';
    try{
      let url='/api/scenario?target='+encodeURIComponent(target)+'&path='+encodeURIComponent(path);
      if(runId)url+='&runId='+encodeURIComponent(runId)+'&scenario='+encodeURIComponent(scnName);
      const r=await fetch(url);
      const d=await r.json();
      if(d.error){$('#au-status').textContent=d.error;$('#au-status').className='status ng';return;}
      $('#au-yaml').value=d.yaml||'';
      $('#au-save').disabled=false;
      auRenderGutter();auLint();
      auSteps=d.steps||[];
      auRenderStepList();
      if(auSteps.length>0){auShowStep(0);}
      else{auIdx=-1;$('#au-placeholder').hidden=false;$('#au-placeholder').textContent=runId?'No steps in this run.':'No run selected — edit YAML directly.';$('#au-screenshot').hidden=true;$('#au-feedback').hidden=true;$('#au-prev').disabled=true;$('#au-next').disabled=true;$('#au-step-label').textContent='No steps';}
      const msg=auSteps.length?auSteps.length+' step'+(auSteps.length===1?'':'s')+' loaded':'YAML loaded (no run selected)';
      $('#au-status').textContent=msg;$('#au-status').className='status ok';
      $('#au-enrich').disabled=false;
      $('#au-codegen').disabled=false;
      $('#au-enrich-panel').hidden=true;
    }catch(e){$('#au-status').textContent=String(e);$('#au-status').className='status ng';}
  }

  function auRenderStepList(){
    const ol=$('#au-steplist');ol.innerHTML='';
    auSteps.forEach((s,i)=>{
      const li=document.createElement('li');
      li.textContent=(i+1)+'. '+auStepLabel(s);
      li.addEventListener('click',()=>auShowStep(i));
      ol.appendChild(li);
    });
    $('#au-step-count').textContent=auSteps.length+' step'+(auSteps.length===1?'':'s');
  }

  function auShowStep(idx){
    if(idx<0||idx>=auSteps.length)return;
    auIdx=idx;
    const s=auSteps[idx];
    document.querySelectorAll('#au-steplist li').forEach((li,i)=>li.classList.toggle('active',i===idx));
    $('#au-prev').disabled=idx===0;
    $('#au-next').disabled=idx===auSteps.length-1;
    $('#au-step-label').textContent='Step '+(idx+1)+' / '+auSteps.length;
    if(s.screenshotUrl){
      $('#au-screenshot').src=s.screenshotUrl;
      $('#au-screenshot').hidden=false;$('#au-placeholder').hidden=true;
    }else{
      $('#au-screenshot').hidden=true;$('#au-placeholder').hidden=false;
      $('#au-placeholder').textContent='No screenshot for this step.';
    }
    $('#au-feedback').hidden=true;
    auResolvedSel=null;
  }

  async function editResolve(nx,ny){
    if(auIdx<0||auIdx>=auSteps.length)return;
    const target=$('#au-target').value;
    const runId=$('#au-run').value;
    const s=auSteps[auIdx];
    $('#au-status').textContent='Resolving…';$('#au-status').className='status run';
    try{
      const r=await fetch('/api/scenario/resolve',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({target:target,runId:runId,stepId:s.stepId,point:[nx,ny]})});
      const d=await r.json();
      if(d.error){$('#au-status').textContent=d.error;$('#au-status').className='status ng';return;}
      if(d.refused){$('#au-status').textContent=d.refused;$('#au-status').className='status ng';$('#au-feedback').hidden=true;auResolvedSel=null;return;}
      if(d.ambiguous){
        $('#au-status').textContent='Ambiguous: '+d.candidates+' elements share this selector. Narrow with within/index.';
        $('#au-status').className='status ng';
      }else{
        $('#au-status').textContent='Resolved — click Apply to update the YAML';$('#au-status').className='status ok';
      }
      auResolvedSel=d.selector;
      const desc=d.selector.id?('#'+d.selector.id):(d.selector.label||'?');
      $('#au-feedback').hidden=false;
      $('#au-rung').textContent=d.rung;$('#au-rung').className='au-rung rung-'+d.rung;
      $('#au-sel').textContent=desc;
    }catch(e){$('#au-status').textContent=String(e);$('#au-status').className='status ng';}
  }

  // Shared screenshot click — routed by the active mode.
  $('#au-screenshot').addEventListener('click',(e)=>{
    const rect=e.target.getBoundingClientRect();
    const nx=(e.clientX-rect.left)/rect.width;
    const ny=(e.clientY-rect.top)/rect.height;
    if(mode==='capture')capMark(nx,ny);
    else if(mode==='edit')editResolve(nx,ny);
    else{$('#au-status').textContent='Clicking the screenshot does nothing in Enrich mode — switch to Capture or Edit to pick elements.';$('#au-status').className='status';}
  });

  // Apply: write resolved selector into YAML at the current step.
  $('#au-apply').addEventListener('click',()=>{
    if(!auResolvedSel||auIdx<0)return;
    const s=auSteps[auIdx];
    const action=s.action||'tap';
    const newSel=auSelectorYaml(auResolvedSel);
    const yaml=$('#au-yaml').value;
    const oldFields=s.fields||{};
    let oldPattern='';
    if(action==='tap'||action==='doubleTap'||action==='longPress'){
      oldPattern=action+':';
    }else if(action==='type'){
      oldPattern='type:';
    }else{
      oldPattern=action+':';
    }
    // Find the step's line in the YAML and replace the selector.
    const lines=yaml.split('\n');
    let stepCount=0;
    for(let i=0;i<lines.length;i++){
      const trimmed=lines[i].trimStart();
      if(trimmed.startsWith('- '+oldPattern)||trimmed.startsWith('- '+action+':')){
        if(stepCount===auIdx){
          const indent=lines[i].match(/^(\s*)/)[1];
          if(action==='type'){
            lines[i]=indent+'- type: { into: '+newSel+', text: '+(oldFields.text||'""')+' }';
          }else{
            lines[i]=indent+'- '+action+': '+newSel;
          }
          break;
        }
        stepCount++;
      }
    }
    $('#au-yaml').value=lines.join('\n');
    auRenderGutter();auLintSoon();
    // Update the in-memory step fields.
    if(action==='type'){
      s.fields={into:auResolvedSel,text:oldFields.text||''};
    }else{
      s.fields=auResolvedSel;
    }
    auRenderStepList();
    document.querySelectorAll('#au-steplist li').forEach((li,j)=>li.classList.toggle('active',j===auIdx));
    $('#au-status').textContent='Applied '+auSelectorYaml(auResolvedSel)+' to step '+(auIdx+1);
    $('#au-status').className='status ok';
  });

  // ---- Inline validation + schema assistance (BE-0138) ----
  // Static and AI-free: /api/lint and /api/schema wrap the same validators the CLI runs.

  function auMeasureChar(){
    // Width of one monospace glyph, for mapping caret column ↔ pixel x.
    const span=document.createElement('span');
    span.style.cssText='position:absolute;visibility:hidden;white-space:pre;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px';
    span.textContent='0'.repeat(20);document.body.appendChild(span);
    auCharW=span.getBoundingClientRect().width/20;span.remove();
  }

  function auGutterSync(){
    $('#au-gutter-inner').style.transform='translateY('+(-$('#au-yaml').scrollTop)+'px)';
  }

  function auRenderGutter(){
    const ta=$('#au-yaml');
    const n=Math.max(1,ta.value.split('\n').length);
    const errLines=new Set(auDiagnostics.filter(d=>d.line).map(d=>d.line));
    let html='';
    for(let i=1;i<=n;i++){html+='<div class="gl'+(errLines.has(i)?' err':'')+'">'+i+'</div>';}
    $('#au-gutter-inner').innerHTML=html;
    auGutterSync();
  }

  function auJumpToLine(line){
    const ta=$('#au-yaml');
    const lines=ta.value.split('\n');
    let start=0;for(let i=0;i<line-1&&i<lines.length;i++){start+=lines[i].length+1;}
    const end=start+(lines[line-1]?lines[line-1].length:0);
    ta.focus();ta.setSelectionRange(start,end);
    ta.scrollTop=Math.max(0,(line-1)*AU_LH-ta.clientHeight/2);
    auGutterSync();
  }

  function auRenderProblems(){
    const box=$('#au-problems');
    if(!auDiagnostics.length){box.hidden=true;box.innerHTML='';return;}
    box.hidden=false;
    box.innerHTML='<div class="au-problems-head">'+auDiagnostics.length+' problem'+(auDiagnostics.length===1?'':'s')+'</div>'+
      auDiagnostics.map((d,i)=>'<div class="au-problem" data-i="'+i+'">'+
        (d.line?'<span class="pl">L'+d.line+(d.column?':'+d.column:'')+'</span>':'<span class="pl pl-none">—</span>')+
        '<span class="pm">'+esc(d.message)+'</span></div>').join('');
    box.querySelectorAll('.au-problem').forEach(el=>{
      el.addEventListener('click',()=>{const d=auDiagnostics[+el.dataset.i];if(d&&d.line)auJumpToLine(d.line);});
    });
  }

  async function auLint(){
    // Fail loudly: if validation is unavailable (network / non-2xx / bad body), surface an
    // unanchored diagnostic rather than clearing findings — a "clean" editor must mean "validated
    // clean", not "couldn't validate".
    try{
      const r=await fetch('/api/lint',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({yaml:$('#au-yaml').value})});
      if(!r.ok)throw new Error('/api/lint returned '+r.status);
      const d=await r.json();
      if(!Array.isArray(d.diagnostics))throw new Error('unexpected response');
      auDiagnostics=d.diagnostics;
    }catch(e){
      auDiagnostics=[{line:null,column:null,message:'Inline validation unavailable ('+e+')',severity:'error'}];
    }
    auRenderGutter();auRenderProblems();
    auAudit();  // re-grade determinism on the same live YAML lint just validated (BE-0145)
  }

  function auLintSoon(){clearTimeout(auLintTimer);auLintTimer=setTimeout(auLint,400);}

  // ---- Determinism audit (BE-0145): surface the static stability score inline ----
  // Read-only, AI-free: /api/audit wraps the same static audit the CLI runs (bajutsu/audit.py). It
  // grades the live YAML, so it rides alongside lint (auLint calls it) rather than on its own timer.
  // Since it fires on every debounced edit, a sequence guard drops a stale response so an older
  // audit can't overwrite (or clear) the badge/findings for newer YAML.
  let auAuditSeq=0;
  async function auAudit(){
    const seq=++auAuditSeq;
    try{
      const r=await fetch('/api/audit',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({yaml:$('#au-yaml').value})});
      if(!r.ok)throw new Error('/api/audit returned '+r.status);
      const d=await r.json();
      if(seq!==auAuditSeq)return;  // a newer edit superseded this response
      if(!d.ok||!Array.isArray(d.reports))throw new Error('unexpected response');
      renderGradeBadge($('#au-grade'),d.reports);
      auRenderFindings(d.reports);
    }catch(e){
      // The audit is advisory, and lint already flags a broken scenario loudly, so a failed audit
      // just clears its badge rather than raising a second alarm — but only if it is still current.
      if(seq!==auAuditSeq)return;
      $('#au-grade').hidden=true;$('#au-audit').hidden=true;
    }
  }

  function auRenderFindings(reports){
    const box=$('#au-audit');
    const multi=reports.length>1;
    const findings=[];
    reports.forEach(r=>(r.findings||[]).forEach(f=>
      findings.push((multi?r.scenario+': ':'')+f.where+' — '+f.detail)));
    if(!findings.length){box.hidden=true;box.innerHTML='';return;}
    box.hidden=false;
    box.innerHTML='<div class="au-audit-head">'+findings.length+' determinism finding'+(findings.length===1?'':'s')+'</div>'+
      findings.map(t=>'<div class="au-finding">'+esc(t)+'</div>').join('');
  }

  // ---- schema-driven completion / hover ----
  let auSchemaKeyMap=null;   // memoized {name: description}; the schema is fetched once and immutable
  function auSchemaKeys(){
    // {name: description} for every property across the schema's defs — the grammar's key set.
    // Cached: auHover calls this on every mousemove, so the deep walk must not repeat per event.
    if(auSchemaKeyMap)return auSchemaKeyMap;
    if(!auSchema)return {};
    const out={};
    (function walk(node){
      if(!node||typeof node!=='object')return;
      if(node.properties){for(const k in node.properties){const v=node.properties[k];if(!(k in out))out[k]=(v&&v.description)||'';}}
      for(const key in node){const v=node[key];if(v&&typeof v==='object')walk(v);}
    })(auSchema);
    auSchemaKeyMap=out;
    return out;
  }

  function auCaretLineCol(){
    const before=$('#au-yaml').value.slice(0,$('#au-yaml').selectionStart).split('\n');
    return {line:before.length,col:before[before.length-1].length};
  }

  let auCompItems=[], auCompSel=0, auCompToken='';
  function auHideComplete(){$('#au-complete').hidden=true;auCompItems=[];}

  function auShowComplete(){
    if(!auCharW)auMeasureChar();
    const ta=$('#au-yaml'), pos=auCaretLineCol();
    const lineText=ta.value.split('\n')[pos.line-1]||'';
    auCompToken=(lineText.slice(0,pos.col).match(/[\w.-]*$/)||[''])[0];
    const keys=auSchemaKeys();
    auCompItems=Object.keys(keys).filter(k=>k.startsWith(auCompToken)&&k!==auCompToken).sort().slice(0,12);
    if(!auCompItems.length){auHideComplete();return;}
    auCompSel=0;
    const box=$('#au-complete');
    // Offsets are relative to .yamledit (the popup's offset parent), so add the textarea's own
    // offset within it — the line-number gutter sits to its left.
    box.style.left=(ta.offsetLeft+Math.max(0,AU_PADX+(pos.col*auCharW)-ta.scrollLeft))+'px';
    box.style.top=(ta.offsetTop+AU_PADY+(pos.line*AU_LH)-ta.scrollTop)+'px';
    auRenderComplete(keys);
    box.hidden=false;
  }

  function auRenderComplete(keys){
    $('#au-complete').innerHTML=auCompItems.map((k,i)=>
      '<div class="ci'+(i===auCompSel?' sel':'')+'" data-k="'+esc(k)+'" title="'+esc(keys[k]||'')+'">'+esc(k)+'</div>').join('');
    $('#au-complete').querySelectorAll('.ci').forEach(el=>{
      el.addEventListener('mousedown',e=>{e.preventDefault();auAcceptComplete(el.dataset.k);});
    });
  }

  function auAcceptComplete(key){
    const ta=$('#au-yaml'), idx=ta.selectionStart;
    const start=idx-auCompToken.length;
    ta.value=ta.value.slice(0,start)+key+ta.value.slice(idx);
    const caret=start+key.length;ta.setSelectionRange(caret,caret);
    auHideComplete();ta.focus();
    $('#au-save').disabled=false;auRenderGutter();auLintSoon();
  }

  function auHover(e){
    const box=$('#au-hover');
    if(!auCharW)auMeasureChar();
    const ta=$('#au-yaml'), rect=ta.getBoundingClientRect();
    const y=e.clientY-rect.top+ta.scrollTop-AU_PADY, x=e.clientX-rect.left+ta.scrollLeft-AU_PADX;
    const lines=ta.value.split('\n'), li=Math.floor(y/AU_LH);
    if(x<0||li<0||li>=lines.length){box.hidden=true;return;}
    const col=Math.round(x/auCharW), text=lines[li];
    const tok=(text.slice(0,col).match(/[\w.-]*$/)||[''])[0]+(text.slice(col).match(/^[\w.-]*/)||[''])[0];
    const keys=auSchemaKeys();
    if(!tok||!(tok in keys)||!keys[tok]){box.hidden=true;return;}
    box.innerHTML='<span class="hk">'+esc(tok)+'</span> — '+esc(keys[tok]);
    // Position within .yamledit (offset parent): add the textarea's offset past the gutter.
    box.style.left=(ta.offsetLeft+Math.min(e.clientX-rect.left+8,ta.clientWidth-40))+'px';
    box.style.top=(ta.offsetTop+e.clientY-rect.top+18)+'px';
    box.hidden=false;
  }

  // Save.
  $('#au-save').addEventListener('click',async()=>{
    if(!auPath){$('#au-status').textContent='Nothing to save yet — capture a flow or load a scenario first.';$('#au-status').className='status ng';return;}
    const target=$('#au-target').value;
    const yaml=$('#au-yaml').value;
    $('#au-save').disabled=true;
    $('#au-status').textContent='Saving…';$('#au-status').className='status run';
    try{
      const r=await fetch('/api/scenario',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({target:target,path:auPath,yaml:yaml})});
      const d=await r.json();
      if(d.error){$('#au-status').textContent=d.error;$('#au-status').className='status ng';}
      else{$('#au-status').textContent='Saved ✓';$('#au-status').className='status ok';}
    }catch(e){$('#au-status').textContent=String(e);$('#au-status').className='status ng';}
    $('#au-save').disabled=false;
  });

  // ---- Enrich mode (BE-0014): propose assertions over the open scenario ----
  function enrichAssertionLabel(a){
    if(a.exists){
      const sel=a.exists.sel||{};
      const id=sel.id?'#'+sel.id:(sel.label||'?');
      return (a.exists.negate?'notExists':'exists')+' '+id;
    }
    if(a.value){
      const sel=a.value.sel||{};
      const id=sel.id?'#'+sel.id:(sel.label||'?');
      return 'value '+id+' equals "'+a.value.equals+'"';
    }
    if(a.label){
      const sel=a.label.sel||{};
      const id=sel.id?'#'+sel.id:(sel.label||'?');
      return 'label '+id+' contains "'+(a.label.contains||'')+'"';
    }
    return JSON.stringify(a);
  }

  function enrichRender(data){
    enrichResult=data;
    const list=$('#au-enrich-list');list.innerHTML='';
    (data.expect||[]).forEach(a=>{
      const li=document.createElement('li');
      li.innerHTML='<span class="enrich-check">✓</span>'+esc(enrichAssertionLabel(a));
      list.appendChild(li);
    });
    if(data.settle){
      const li=document.createElement('li');
      const w=data.settle.wait||{};
      const sel=w['for']||{};
      const id=sel.id?'#'+sel.id:(sel.label||'?');
      li.innerHTML='<span class="enrich-check">⏳</span>settle wait for '+esc(id);
      list.appendChild(li);
    }
    $('#au-enrich-note').textContent=data.note||'';
    $('#au-enrich-panel').hidden=false;
  }

  function enrichAssertionYaml(a,indent){
    const pfx=indent+'- ';
    if(a.exists){
      const sel=a.exists.sel||{};
      const id=sel.id?'{ id: '+sel.id+' }':JSON.stringify(sel);
      if(a.exists.negate)return pfx+'exists: { '+Object.entries(sel).map(([k,v])=>k+': '+JSON.stringify(v)).join(', ')+', negate: true }';
      return pfx+'exists: '+id;
    }
    if(a.value){
      const sel=a.value.sel||{};
      return pfx+'value: { sel: '+JSON.stringify(sel)+', equals: '+JSON.stringify(a.value.equals)+' }';
    }
    if(a.label){
      const sel=a.label.sel||{};
      return pfx+'label: { sel: '+JSON.stringify(sel)+', contains: '+JSON.stringify(a.label.contains)+' }';
    }
    return pfx+JSON.stringify(a);
  }

  function _extractName(trimmed){
    const m=trimmed.match(/^-\s*name:\s*(.+)/);
    if(!m)return null;
    let v=m[1].trim();
    if((v.startsWith('"')&&v.endsWith('"'))||(v.startsWith("'")&&v.endsWith("'")))v=v.slice(1,-1);
    return v;
  }

  function enrichApply(){
    if(!enrichResult)return;
    let yaml=$('#au-yaml').value;
    const combo=$('#au-scenario').value;
    if(!combo)return;
    const scnName=combo.split('|')[1]||'';

    const lines=yaml.split('\n');
    let inScenario=false;
    let stepsLine=-1;
    let stepsEnd=-1;
    let expectStart=-1;
    let expectEnd=-1;
    let scenarioIndent='';
    let itemIndent='';

    for(let i=0;i<lines.length;i++){
      const trimmed=lines[i].trimStart();
      if(trimmed.startsWith('- name:')&&_extractName(trimmed)===scnName){
        inScenario=true;
        scenarioIndent=lines[i].match(/^(\s*)/)[1]+'  ';
        continue;
      }
      if(inScenario&&trimmed.startsWith('- name:')){break;}
      if(inScenario){
        if(trimmed.startsWith('steps:')){
          stepsLine=i;
          for(let j=i+1;j<lines.length;j++){
            const st=lines[j].trimStart();
            if(st.startsWith('- ')&&!st.startsWith('- name:')){
              stepsEnd=j;
              if(!itemIndent)itemIndent=lines[j].match(/^(\s*)/)[1];
            }
            else if(st&&!st.startsWith('- ')&&!st.startsWith('#')){break;}
          }
          if(stepsEnd<0)stepsEnd=stepsLine;
        }
        if(trimmed.startsWith('expect:')){
          expectStart=i;
          for(let j=i+1;j<lines.length;j++){
            const st=lines[j].trimStart();
            if(st.startsWith('- '))expectEnd=j;
            else if(st&&!st.startsWith('- ')&&!st.startsWith('#')){break;}
          }
        }
      }
    }

    if(!itemIndent)itemIndent=scenarioIndent+'  ';

    const newLines=[];
    if(enrichResult.settle){
      const w=enrichResult.settle.wait||{};
      const sel=w['for']||{};
      const id=sel.id?'{ id: '+sel.id+' }':JSON.stringify(sel);
      newLines.push(itemIndent+'- wait: { for: '+id+', timeout: '+(w.timeout||5)+' }');
    }

    const expectLines=(enrichResult.expect||[]).map(a=>enrichAssertionYaml(a,itemIndent));

    if(newLines.length>0&&stepsEnd>=0){
      lines.splice(stepsEnd+1,0,...newLines);
      if(expectStart>=0)expectStart+=newLines.length;
      if(expectEnd>=0)expectEnd+=newLines.length;
    }

    if(expectLines.length>0){
      if(expectStart>=0){
        const removeCount=expectEnd>=expectStart?expectEnd-expectStart:0;
        lines.splice(expectStart+1,removeCount,...expectLines);
      }else{
        const insertAt=stepsEnd>=0?stepsEnd+1+newLines.length:lines.length;
        lines.splice(insertAt,0,scenarioIndent+'expect:',...expectLines);
      }
    }

    $('#au-yaml').value=lines.join('\n');
    auRenderGutter();auLintSoon();
    $('#au-save').disabled=false;
    $('#au-enrich-panel').hidden=true;
    enrichResult=null;
    $('#au-status').textContent='Assertions applied — review and Save';$('#au-status').className='status ok';
  }

  $('#au-enrich').addEventListener('click',async()=>{
    const combo=$('#au-scenario').value;
    if(!combo){$('#au-status').textContent='Load a scenario first.';return;}
    const [path,scnName]=combo.split('|');
    const target=$('#au-target').value;
    $('#au-enrich').disabled=true;
    $('#au-enrich-panel').hidden=true;
    $('#au-status').textContent='Enriching — replaying steps and proposing assertions…';$('#au-status').className='status run';
    try{
      const r=await fetch('/api/enrich',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({target:target,scenario:path,name:scnName})});
      const d=await r.json();
      if(d.error){$('#au-status').textContent=d.error;$('#au-status').className='status ng';return;}
      const count=(d.expect||[]).length;
      $('#au-status').textContent=count+' assertion'+(count===1?'':'s')+' proposed';$('#au-status').className='status ok';
      enrichRender(d);
    }catch(e){$('#au-status').textContent=String(e);$('#au-status').className='status ng';}
    finally{$('#au-enrich').disabled=false;}
  });

  $('#au-enrich-accept').addEventListener('click',enrichApply);
  $('#au-enrich-dismiss').addEventListener('click',()=>{
    $('#au-enrich-panel').hidden=true;enrichResult=null;
    $('#au-status').textContent='Enrichment dismissed';$('#au-status').className='status';
  });

  // ---- codegen (BE-0137): export the scenario as a native test ----
  // Reuse the shared codegen wiring (see makeCodegen); the Author scenario select carries "path|name",
  // and codegen works on the file, so hand it the path. `sync`/`reset` are driven by the Author's own
  // scenario / target / YAML events below.
  const authorCodegen=makeCodegen({...CODEGEN_IDS('au'),status:'#au-status'},'#au-target',
    ()=>{const c=$('#au-scenario').value;return c?c.split('|')[0]:'';});
  const auSyncEmit=authorCodegen.sync, auCodegenReset=authorCodegen.reset;

  // Nav buttons.
  $('#au-prev').addEventListener('click',()=>{if(auIdx>0)auShowStep(auIdx-1);});
  $('#au-next').addEventListener('click',()=>{if(auIdx<auSteps.length-1)auShowStep(auIdx+1);});
  // Load button.
  $('#au-load').addEventListener('click',auLoad);
  // Picking a different scenario invalidates any open generation — drop it so Copy/Download can't
  // export the previous scenario's file.
  $('#au-scenario').addEventListener('change',auCodegenReset);
  // Target change reloads scenarios and re-picks the emit valid for the new backend; any open
  // codegen result is for the old target, so drop it rather than let Copy/Download export a stale file.
  $('#au-target').addEventListener('change',()=>{auLoadScenarios();auSyncEmit();auCodegenReset();});
  // YAML textarea edits enable save and re-validate (BE-0138): gutter updates instantly, lint debounced.
  $('#au-yaml').addEventListener('input',()=>{
    $('#au-save').disabled=false;auRenderGutter();auLintSoon();
    if(!$('#au-complete').hidden)auShowComplete();
    // Editing the YAML makes any generated code stale (codegen ran against the saved scenario).
    auCodegenReset();
  });
  $('#au-yaml').addEventListener('scroll',()=>{auGutterSync();auHideComplete();});
  $('#au-yaml').addEventListener('blur',()=>{auHideComplete();$('#au-hover').hidden=true;});
  $('#au-yaml').addEventListener('mousemove',auHover);
  $('#au-yaml').addEventListener('mouseleave',()=>{$('#au-hover').hidden=true;});
  // Completion: Ctrl/Cmd+Space opens; arrows/Enter/Tab drive it while open; Escape closes.
  $('#au-yaml').addEventListener('keydown',e=>{
    if((e.ctrlKey||e.metaKey)&&e.code==='Space'){e.preventDefault();auShowComplete();return;}
    if($('#au-complete').hidden)return;
    if(e.key==='Escape'){e.preventDefault();auHideComplete();}
    else if(e.key==='ArrowDown'){e.preventDefault();auCompSel=(auCompSel+1)%auCompItems.length;auRenderComplete(auSchemaKeys());}
    else if(e.key==='ArrowUp'){e.preventDefault();auCompSel=(auCompSel-1+auCompItems.length)%auCompItems.length;auRenderComplete(auSchemaKeys());}
    else if(e.key==='Enter'||e.key==='Tab'){e.preventDefault();auAcceptComplete(auCompItems[auCompSel]);}
  });
  // Fetch the scenario schema once, for completion / hover.
  fetch('/api/schema').then(r=>r.ok?r.json():null).then(s=>{auSchema=s;}).catch(()=>{});

  // Called by showView('author') — lazy init: default to Capture mode and load scenarios.
  window.authorInit=function(){
    if(auInited)return;
    auInited=true;
    setMode('capture');
    auLoadScenarios();
    auSyncEmit();
  };
  // Called by loadShared() after targets arrive — re-populate if Author is already visible.
  window.authorRefresh=function(){
    auLoadScenarios();
    auSyncEmit();
  };
})();

initTheme();
loadConfig();
refreshAiAvailability();
loadSims();
loadHistory();
setInterval(loadHistory,4000);
