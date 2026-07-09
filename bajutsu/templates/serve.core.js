// serve.core.js — shared helpers, global state, config, and Settings for the serve Web UI.
//
// One of the serve.*.js section files (BE-0202): handler.py concatenates them in a fixed order
// (core → panels → crawl → author) into a single inlined <script>, so they share one global scope
// with no build step and no modules — exactly as when this was one file. Order matters: this file
// loads first and defines the helpers (`$`, setBusy, streamJob, startJob, …) and state the panel
// files use, so keep cross-file references pointing backward.
//
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
let recJobId=null,runJobId=null,triageJobId=null;
let recRunPoll=null,recRunJobId=null;  // running the just-authored scenario from the Record tab
let recReportShow=null,recReportHide=null;  // set by the tiler: add/remove the Run-result pane
// Whether Claude is reachable (from /api/provider). Gates the opt-in AI toggle on triage the same
// way it gates record/crawl; heuristic triage never needs it. Kept in sync by refreshAiAvailability.
let aiAvailable=false;
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
function streamJob(id,onLog,onDone,onHuman){
  const es=new EventSource('/api/jobs/'+id+'/events');
  es.addEventListener('log',e=>onLog(e.data));
  // A "needs human" turn (BE-0179): the paused record's serialized request. Only record wires
  // onHuman; run/crawl never emit it.
  es.addEventListener('human-request',e=>{if(onHuman)onHuman(JSON.parse(e.data))});
  es.addEventListener('done',e=>{es.close();onDone(JSON.parse(e.data))});
  return es;
}
function appendLine(el,line){el.textContent+=(el.textContent?'\n':'')+line;el.scrollTop=el.scrollHeight}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function setStatus(el,t,c){el.textContent=t;el.className='status '+c}
// Fetch JSON, resolving to `fallback` on any network/parse failure — every panel degrades this
// way, so the try/catch lives here once instead of at each call site.
async function getJSON(url,fallback){try{return await (await fetch(url)).json()}catch(e){return fallback}}
async function postJSON(url,body,fallback){
  try{return await (await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json()}
  catch(e){return fallback}
}
// Shared skeleton for the run / record / crawl "start" buttons (BE-0202): close any live stream,
// flip the button busy, POST the request, and on a clean {jobId} hand the stream to streamJob. The
// per-panel pane clearing stays at each call site (done before calling this — it varies by panel).
// On any failure — a network drop, a non-JSON body, an {error} response, or a missing jobId — it
// resets the button, reports via setStatus, and returns null (failing loudly rather than leaving the
// button stuck spinning, since it now fronts all three start buttons). On success it runs
// onStart(data) (so the caller can stash its jobId/runId/path) and returns the EventSource for the
// caller to hold for a later restart/cancel.
//   o: {prev, btn, stop, busyLabel, status, url, body, onStart, onLog, onDone, onHuman}
async function startJob(o){
  if(o.prev)o.prev.close();
  setBusy(o.btn,o.stop,true,o.busyLabel);
  setStatus(o.status,'','run');
  const fail=msg=>{setStatus(o.status,msg,'ng');setBusy(o.btn,o.stop,false);return null;};
  let data;
  try{
    const r=await fetch(o.url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o.body)});
    data=await r.json();
  }catch(e){return fail('request failed');}  // network drop or non-JSON body
  if(data.error)return fail(data.error);
  if(!data.jobId)return fail('no job started');  // a non-2xx without an {error} field still can't stream
  if(o.onStart)o.onStart(data);
  return streamJob(data.jobId,o.onLog,o.onDone,o.onHuman);
}

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

// ---- doctor readiness (BE-0148): shells out to the same checks the CLI `doctor` runs ----
// Two halves from POST /api/doctor: the runnability checks (each required tool present? a
// Simulator booted?) and the current screen's convention score (Ready/Partial/Blocked with the
// per-id gaps). Read-only and AI-free — it never gates a run. Shared by the Record and Replay
// panels; a sequence guard drops a stale response so a slow check can't overwrite a newer one.
const DR_GRADE_CLASS={Ready:'ready',Partial:'partial',Blocked:'blocked'};
function renderDoctorChecks(box,checks){
  box.innerHTML=(checks||[]).map(c=>
    `<div class="dr-check-line ${c.ok?'ok':'ng'}">${c.ok?'✓':'✗'} ${esc(c.name)}: ${esc(c.detail)}</div>`).join('');
}
function renderDoctorScore(el,score){
  if(!score){el.hidden=true;return;}
  el.hidden=false;
  el.textContent=score.grade+' · '+Math.round(score.idCoverage*100)+'% id coverage';
  el.className='grade-badge '+(DR_GRADE_CLASS[score.grade]||'');
}
// The score's "what to fix" list: unnamed controls, off-namespace ids, and duplicate ids.
function doctorFindings(score){
  const out=[];
  if(score.noActionable)out.push('no actionable elements — is the app on the expected screen and loaded?');
  (score.missingId||[]).forEach(m=>out.push('missing id: '+(m.label||'(no label)')+' ['+(m.traits||[]).join(', ')+']'));
  (score.offNamespace||[]).forEach(i=>out.push('off-namespace id: '+i));
  (score.duplicates||[]).forEach(i=>out.push('duplicate id: '+i));
  return out;
}
function wireDoctor(ids,getTarget){
  const btn=$(ids.btn);
  if(!btn)return;
  const status=$(ids.status),badge=$(ids.badge),checks=$(ids.checks),findings=$(ids.findings);
  let seq=0;
  btn.addEventListener('click',async()=>{
    const target=getTarget();
    if(!target){setStatus(status,'pick a target first','ng');return;}
    const my=++seq;
    setStatus(status,'checking…','run');badge.hidden=true;checks.innerHTML='';findings.hidden=true;
    try{
      const r=await fetch('/api/doctor',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({target})});
      const d=await r.json();
      if(my!==seq)return;  // a newer check superseded this one
      if(!r.ok){setStatus(status,d.error||('doctor failed ('+r.status+')'),'ng');return;}
      renderDoctorChecks(checks,d.checks);
      renderDoctorScore(badge,d.score);
      const f=d.score?doctorFindings(d.score):[];
      if(f.length){findings.hidden=false;
        findings.innerHTML='<div class="au-audit-head">'+f.length+' readiness finding'+(f.length===1?'':'s')+'</div>'+
          f.map(t=>'<div class="au-finding">'+esc(t)+'</div>').join('');}
      else findings.hidden=true;
      setStatus(status,d.ok?'environment ready ✓':'environment not ready',d.ok?'ok':'ng');
    }catch(e){if(my===seq)setStatus(status,'doctor request failed','ng');}
  });
}

// ---- theme picker (BE-0191 unit 3; theme token blocks live in serve.themes.css) ----
// A <select> of every registered theme (window.__bajutsuThemes, built-in + drop-in), replacing the
// old two-state toggle. Default follows the OS (or the configured ui.default_theme) and updates
// live; an explicit pick persists in localStorage until the OS changes. The pre-paint inline script
// (serve.html.j2) already applied the resolved theme before first paint — here we only seed the
// widget's shown value from it and wire the change/OS handlers.
const SYS_MQ=matchMedia('(prefers-color-scheme: light)');
const THEMES=Array.isArray(window.__bajutsuThemes)?window.__bajutsuThemes:[];
function themeExists(t){return THEMES.some(x=>x.id===t)}
function systemTheme(){
  // The OS scheme maps to whichever registered theme declares that kind first, falling back to the
  // built-in pair so a registry that dropped one kind still resolves.
  const want=SYS_MQ.matches?'light':'dark';
  const hit=THEMES.find(x=>x.kind===want);
  return hit?hit.id:(SYS_MQ.matches?'daylight':'midnight');
}
function currentTheme(){
  return document.documentElement.getAttribute('data-theme')
    ||localStorage.getItem('bajutsu-theme')
    ||window.__bajutsuDefaultTheme
    ||systemTheme();
}
function applyTheme(t,persist){
  document.documentElement.setAttribute('data-theme',t);
  try{if(persist)localStorage.setItem('bajutsu-theme',t)}catch(e){}
  const sel=$('#theme');if(sel&&sel.value!==t&&themeExists(t))sel.value=t;
}
function initTheme(){
  const sel=$('#theme');
  applyTheme(currentTheme(),false);
  // An explicit pick wins and is remembered until the OS scheme changes.
  sel.addEventListener('change',()=>applyTheme(sel.value,true));
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
  $('#view-record').hidden=name!=='record';$('#view-replay').hidden=name!=='replay';$('#view-crawl').hidden=name!=='crawl';$('#view-author').hidden=name!=='author';$('#view-stats').hidden=name!=='stats';$('#view-usage').hidden=name!=='usage';$('#view-coverage').hidden=name!=='coverage';
  if(name==='replay')loadHistory();
  if(name==='author')authorInit();
  if(name==='stats')loadStats();
  if(name==='usage')loadUsage();
  if(name==='coverage')coverageInit();
}
document.querySelectorAll('.toptab').forEach(t=>t.addEventListener('click',()=>showView(t.dataset.view)));

// ---- config: bound at startup or opened from the UI's file browser ----
// Whether the file-browser source is offered — a hosted deployment omits `fs` from configSources
// (BE-0108), so we hide that block and never call browseFs. Git + Upload are always offered.
let fsSourceEnabled=true;
async function loadConfig(){
  const c=await getJSON('/api/config',{hasConfig:false});
  fsSourceEnabled=!c.configSources||c.configSources.includes('fs');
  $('#fssrc').hidden=!fsSourceEnabled;
  setCfgName(c.hasConfig?c.config:'no config bound — open one →',c.hasConfig);
  if(c.hasConfig){await loadShared()}else{openFs()}
}
// Set the nav's config-name label and reveal the "View" button only when a config is actually bound.
function setCfgName(text,hasConfig){$('#cfgname').textContent=text;$('#viewcfg').hidden=!hasConfig}
// Browse the server's --root for a config.yml. Paths returned by /api/fs are absolute and the
// server re-validates every one against --root, so clicking can never escape the browse ceiling.
async function browseFs(dir){
  const d=await getJSON('/api/fs'+(dir?('?dir='+encodeURIComponent(dir)):''),{error:'failed'});
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

// ---- view the loaded config: a structured key/value tree (or the raw YAML) + its Git origin ----
function closeCfgView(){$('#cfgviewmodal').hidden=true}
// Toggle between the collapsible structured tree and the raw YAML text.
function cfgViewMode(raw){
  $('#cfgviewtree').hidden=raw;$('#cfgviewbody').hidden=!raw;
  $('#cfgview-raw').classList.toggle('active',raw);
  $('#cfgview-structured').classList.toggle('active',!raw);
}
// Is `v` a container (object or array) that gets a collapsible node, vs a scalar leaf?
function cfgIsContainer(v){return v!==null&&typeof v==='object'}
function cfgScalar(v){return v===null?'null':(typeof v==='string'?v:String(v))}
function cfgScalarClass(v){return v===null?'nul':(typeof v==='boolean'?'bool':(typeof v==='number'?'num':'str'))}
// Build one node: a scalar row, or a <details> whose summary is the key and body its children. The
// first two levels open by default so `defaults`/`targets` are visible without a click; deeper nests
// stay collapsed. `key` is null for a scalar array item (shown as a bare value).
function cfgNode(key,val,depth){
  if(!cfgIsContainer(val)){
    const row=document.createElement('div');row.className='cfgrow';
    if(key!==null){const k=document.createElement('span');k.className='cfgkey';k.textContent=key;row.appendChild(k);}
    const v=document.createElement('span');v.className='cfgval '+cfgScalarClass(val);v.textContent=cfgScalar(val);row.appendChild(v);
    return row;
  }
  const arr=Array.isArray(val);
  const entries=arr?val.map((v,i)=>[i,v]):Object.entries(val);
  const det=document.createElement('details');det.className='cfgdet';if(depth<2)det.open=true;
  const sum=document.createElement('summary');
  const label=document.createElement('span');label.className='cfgkey';label.textContent=(key!==null?key:(arr?'list':'config'));sum.appendChild(label);
  const meta=document.createElement('span');meta.className='cfgmeta';meta.textContent=arr?('['+entries.length+']'):('{'+entries.length+'}');sum.appendChild(meta);
  det.appendChild(sum);
  const kids=document.createElement('div');kids.className='cfgchildren';
  // An array's scalar items show as bare values (key null); its object items keep their index so
  // repeated `{config}` rows stay distinguishable.
  for(const [k,v] of entries)kids.appendChild(cfgNode(arr?(cfgIsContainer(v)?k:null):k,v,depth+1));
  det.appendChild(kids);
  return det;
}
async function openCfgView(){
  const d=await getJSON('/api/config/content',{error:'request failed'});
  const prov=$('#cfgprov'),tree=$('#cfgviewtree');
  tree.textContent='';
  if(d.error){prov.hidden=true;$('#cfgviewpath').textContent='';$('#cfgviewbody').textContent=d.error;$('#cfgview-structured').disabled=true;cfgViewMode(true);$('#cfgviewmodal').hidden=false;return}
  const p=d.provenance;
  if(p){
    // A Git source: show which commit was materialized (ref → resolved sha), not the opaque cache path.
    prov.innerHTML='From Git: <code>'+esc(p.host+'/'+p.owner+'/'+p.repo)+'</code> @ <code>'+esc(p.ref)+'</code> &rarr; <code>'+esc((p.sha||'').slice(0,12))+'</code>';
    prov.hidden=false;
  }else{prov.hidden=true}
  $('#cfgviewpath').textContent=d.config||'';
  $('#cfgviewbody').textContent=d.content||'';
  // A parseable config gets the structured tree by default; an unparseable one falls back to raw.
  const hasTree=d.parsed!==null&&d.parsed!==undefined;
  if(hasTree)tree.appendChild(cfgNode(null,d.parsed,0));
  $('#cfgview-structured').disabled=!hasTree;
  cfgViewMode(!hasTree);
  $('#cfgviewmodal').hidden=false;
}
$('#viewcfg').addEventListener('click',openCfgView);
$('#cfgviewclose').addEventListener('click',closeCfgView);
$('#cfgview-structured').addEventListener('click',()=>cfgViewMode(false));
$('#cfgview-raw').addEventListener('click',()=>cfgViewMode(true));
$('#cfgviewmodal').addEventListener('click',e=>{if(e.target===$('#cfgviewmodal'))closeCfgView()});

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
  const d=await getJSON('/api/apikey',{set:false});
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

// ---- AI provider: Anthropic API (Claude API key), Amazon Bedrock (AWS creds), Anthropic CLI (ant, OAuth), or Claude Code CLI (claude, subscription) ----
// Each provider's remembered model/effort/region, keyed by provider name (BE-0183); loadProv fills
// it from /api/provider's `providers` map so a dropdown change swaps to that provider's own values
// instead of leaving the previous provider's sitting in what looks like a shared textbox.
let provState={};
// Show only the selected provider's own config block — nothing until one is explicitly picked — and
// swap the model/effort/region fields to the selected provider's remembered values (BE-0183).
function renderProv(){
  const v=$('#provider').value;
  $('#apikeysection').hidden=v!=='api-key';        // the Claude API key is the api-key provider's config
  $('#bedrockfields').hidden=v!=='bedrock';        // region + model id
  $('#antfields').hidden=v!=='ant';                // ant CLI prerequisites (OAuth sign-in button)
  $('#claudecodefields').hidden=v!=='claude-code'; // claude CLI prerequisites (subscription sign-in note)
  const s=provState[v]||{};
  if(v==='bedrock'){$('#bedrock-region').value=s.region||'';$('#bedrock-model').value=s.model||'';}
  else{$('#ai-model').value=s.model||'';}          // the non-Bedrock providers share the general model field
  $('#ai-effort').value=s.effort||'';
  if(v==='ant')refreshAntLogin();                  // reflect the CLI's current sign-in state on the button
}
// ---- ant CLI SSO sign-in (BE-0175): start `ant auth login` from the Web UI instead of a terminal.
// Local serve only (the server refuses it when hosted). The button state comes from /api/provider —
// the same reachability the record/crawl gate reads — so "Signed in ✓" and the gate never disagree.
function setAntStatus(t,c){const st=$('#ant-login-status');if(st){st.textContent=t;st.className='keystatus '+(c||'')}}
async function refreshAntLogin(){
  const btn=$('#ant-login');if(!btn)return;
  const d=await getJSON('/api/provider',{});
  // claudeGap is provider-specific only once `ant` is the active provider; before Save we can't tell
  // "signed in" apart, so the button stays actionable and the login endpoint reports a missing CLI.
  const signedIn=d.provider==='ant'&&d.claudeAvailable===true;
  btn.hidden=false;btn.disabled=signedIn;
  btn.textContent=signedIn?'Signed in ✓':'Sign in with SSO';
  setAntStatus(signedIn?'signed in':'', signedIn?'ok':'');
}
// Each click bumps the generation; a poll loop bails the moment a newer click supersedes it, so a
// stale attempt (the operator abandoned the browser flow and clicked again) can't clobber the UI or
// leave two loops running. The button stays clickable throughout — clicking while a sign-in is still
// waiting restarts it (the server terminates the stuck CLI and spawns a fresh one).
let antLoginGen=0;
async function antLogin(){
  const myGen=++antLoginGen;
  const btn=$('#ant-login');
  setAntStatus('starting sign-in…','');
  const d=await postJSON('/api/ant/login',{},{error:'request failed'});
  if(myGen!==antLoginGen)return;                    // a newer click already took over
  if(d.error){await refreshAntLogin();setAntStatus(d.error,'ng');return}  // restore button, then show why
  if(btn)btn.textContent='Retry sign-in';           // keep it enabled so a stuck flow can be restarted
  setAntStatus('complete the sign-in in your browser…','');
  for(let i=0;i<200;i++){                            // ~5 min ceiling (matches the CLI's callback timeout)
    await new Promise(r=>setTimeout(r,1500));
    if(myGen!==antLoginGen)return;                   // superseded — stop polling, let the newer loop drive
    const s=await getJSON('/api/ant/login',{state:'error',detail:'status check failed'});
    if(s.state==='running')continue;
    if(s.state==='ok'){
      // Align the server's active provider to `ant` so the record/crawl gate reflects the new
      // credential, then re-read availability and the button (which restores its label).
      try{await fetch('/api/provider',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:'ant'})})}catch(e){}
      if($('#provider').value==='ant')setSettingsStatus('signed in — provider set to ant','ok');
      refreshAiAvailability();refreshAntLogin();
    }else{
      await refreshAntLogin();                       // restore the button, then surface why it failed
      setAntStatus(s.detail||'sign-in did not complete','ng');
    }
    return;
  }
  await refreshAntLogin();
  setAntStatus('sign-in timed out — try again','ng');
}
async function loadProv(){
  // Explicit selection: don't pre-select a provider from the server's (env-derived) default —
  // the user must consciously pick one, so the #provider placeholder stays until they do. The
  // per-provider map is cached so renderProv can pre-fill each provider's own values on pick (BE-0183).
  const d=await getJSON('/api/provider',{});
  provState=d.providers||{};
  $('#ai-language').value=d.language||'auto';  // AI output language (BE-0188); blank env = auto
  renderProv();
}
// ---- Claude reachability (BE-0101): the record/crawl surfaces degrade gracefully when Claude
// can't be reached — the tabs read disabled and each view shows an inline explanation naming what
// is missing with a pointer to Settings, instead of only failing on click. Flips live as soon as a
// key is saved / a provider is picked (saveSettings re-runs this). Availability is data from
// /api/provider (claudeAvailable / claudeHint), so the three surfaces never disagree.
async function refreshAiAvailability(){
  const d=await getJSON('/api/provider',{});
  const ok=d.claudeAvailable!==false, hint=d.claudeHint||'set an API key, configure Bedrock, or sign in with `ant auth login`.';
  aiAvailable=ok;  // the triage panel's opt-in Claude toggle reads this (BE-0147)
  const ta=$('#triage-ai');if(ta)ta.disabled=!ok;  // reflect live if a triage panel is open
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
  // The reasoning effort applies to any provider that supports it; the general model override applies
  // to the non-Bedrock providers (Bedrock keeps its own prefixed id). Blank clears either.
  body.effort=$('#ai-effort').value;
  body.language=$('#ai-language').value;  // AI output language (BE-0188); auto clears the override
  if(provider==='bedrock'){
    body.region=$('#bedrock-region').value.trim();
    body.model=$('#bedrock-model').value.trim();
    if(!body.model){setSettingsStatus('enter a Bedrock model id','ng');return}
  }else{
    body.aiModel=$('#ai-model').value.trim();
  }
  setSettingsStatus('saving…','');
  const d=await postJSON('/api/provider',body,{error:'request failed'});
  if(d.error){setSettingsStatus(d.error,'ng');return}
  // Remember this provider's just-saved values locally so switching away and back keeps them (BE-0183),
  // matching the per-provider slot the server now holds.
  provState[provider]={model:body.model||body.aiModel||'',effort:body.effort||'',region:body.region||''};
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
  // The ant sign-in button reads the *server-side* active provider (d.provider==='ant'), which only
  // becomes ant once the save lands; refresh it now so "Signed in ✓" reflects the save without
  // waiting for the modal to reopen or the dropdown to toggle.
  if(provider==='ant')refreshAntLogin();
}
// ---- Settings modal: one panel for the provider + API-key controls ----
function openSettings(){$('#settingsmodal').hidden=false;$('#apikey').value='';setSettingsStatus('','');loadKey();loadProv()}
function closeSettings(){$('#settingsmodal').hidden=true}
$('#opensettings').addEventListener('click',openSettings);
$('#settingsclose').addEventListener('click',closeSettings);
$('#settingsmodal').addEventListener('click',e=>{if(e.target===$('#settingsmodal'))closeSettings()});
$('#provider').addEventListener('change',renderProv);
$('#settingssave').addEventListener('click',saveSettings);
$('#ant-login').addEventListener('click',antLogin);
async function chooseConfig(path){
  const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
  const d=await r.json();
  if(d.error){$('#fslist').innerHTML='<li class="muted">'+esc(d.error)+'</li>';return}
  setCfgName(d.config,true);closeFs();await loadShared();
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
  setCfgName(d.config,true);closeFs();await loadShared();
}
$('#gitload').addEventListener('click',chooseGitConfig);
$('#gitspec').addEventListener('keydown',e=>{if(e.key==='Enter')chooseGitConfig()});

// ---- shared data: targets, scenarios, simulators (used by both views) ----
async function loadShared(){
  targets=await getJSON('/api/targets',[]);
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
  scnFiles=target?await getJSON('/api/scenarios?target='+encodeURIComponent(target),[]):[];
  $('#scn').innerHTML=scnFiles.map(s=>`<option value="${esc(s.path)}">${esc(s.file)}</option>`).join('');
  showInfo();replayAudit();
}
$('#target').addEventListener('change',()=>{loadScenarios();replayCodegen.sync();replayCodegen.reset();});
async function loadSims(){
  sims=await getJSON('/api/simulators',[]);
  // One checklist template for both multi-select pickers — Replay and Crawl differ only in the
  // checkbox class and change handler.
  const simChecklist=(el,cls,onChange)=>{
    el.innerHTML=sims.length?sims.map(s=>`<label><input type="checkbox" class="${cls}" value="${esc(s.udid)}"><span class="dot ${s.booted?'ok':'off'}" title="${s.booted?'booted':'shut down'}"></span><span>${esc(s.name)}</span><span class="rt">${esc(s.runtime)}${s.booted?'':' · off'}</span></label>`).join(''):'<div class="empty">no simulators found</div>';
    el.querySelectorAll('.'+cls).forEach(c=>c.addEventListener('change',onChange));
  };
  // Replay: multi-select checkboxes (parallel pool).
  simChecklist($('#sims'),'simck',onSimChange);
  // Crawl: multi-select checkboxes too (a parallel pool sharing one screen map — BE-0064).
  simChecklist($('#crawl-sims'),'crawl-simck',onCrawlSimChange);
  // Record: single-device dropdown ("booted" = whatever is already up).
  const single='<option value="booted">booted (already up)</option>'+sims.map(s=>`<option value="${esc(s.udid)}">${esc(s.name)} · ${esc(s.runtime)}${s.booted?'':' · off'}</option>`).join('');
  $('#rec-device').innerHTML=single;
}

