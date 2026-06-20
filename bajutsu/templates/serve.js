// ---- token auth (BE-0051): if the server requires a token, requests 401; prompt for it, POST
// /api/login (which sets an HttpOnly session cookie), then reload. No-op on an open server. ----
const _bjFetch=window.fetch.bind(window);
let _bjLoginShown=false;
window.fetch=async(...a)=>{
  const r=await _bjFetch(...a);
  if(r.status===401 && !String(a[0]).includes('/api/login')) _bjLogin();
  return r;
};
function _bjLogin(){
  if(_bjLoginShown)return; _bjLoginShown=true;
  const t=prompt('This bajutsu server requires a token:');
  if(!t){_bjLoginShown=false;return;}
  _bjFetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:t})})
    .then(r=>{if(r.ok){location.reload()}else{_bjLoginShown=false;alert('invalid token')}})
    .catch(()=>{_bjLoginShown=false});
}

const $=s=>document.querySelector(s);
let poll=null,recPoll=null,selectedRun=null,recPath=null,scnFiles=[],apps=[],sims=[];
let recJobId=null,runJobId=null;
// Toggle a run/stop button pair between idle and running (amber + spinner via the .running class).
function setBusy(btn,stop,on,busyLabel){
  btn.classList.toggle('running',on);btn.disabled=on;btn.textContent=on?busyLabel:btn.dataset.idle;
  stop.hidden=!on;stop.disabled=false;stop.textContent='Stop';
}
// Ask the server to abort a running job; polling then sees it finish and resets the UI.
async function cancelJob(id,stop){
  if(!id)return;stop.disabled=true;stop.textContent='Stopping…';
  try{await fetch('/api/jobs/'+id+'/cancel',{method:'POST'})}catch(e){}
}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function setStatus(el,t,c){el.textContent=t;el.className='status '+c}

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

// ---- top-level Record / Replay / Crawl views ----
function showView(name){
  document.querySelectorAll('.toptab').forEach(t=>t.classList.toggle('active',t.dataset.view===name));
  $('#view-record').hidden=name!=='record';$('#view-replay').hidden=name!=='replay';$('#view-crawl').hidden=name!=='crawl';
  if(name==='replay')loadHistory();
}
document.querySelectorAll('.toptab').forEach(t=>t.addEventListener('click',()=>showView(t.dataset.view)));

// ---- config: bound at startup or opened from the UI's file browser ----
async function loadConfig(){
  let c;try{c=await (await fetch('/api/config')).json()}catch(e){c={hasConfig:false}}
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
function openFs(){$('#fsmodal').hidden=false;browseFs('')}
function closeFs(){$('#fsmodal').hidden=true}
$('#opencfg').addEventListener('click',openFs);
$('#fsclose').addEventListener('click',closeFs);
$('#fsmodal').addEventListener('click',e=>{if(e.target===$('#fsmodal'))closeFs()});

// ---- Claude API key: shown redacted; reveal fetches the full value on demand ----
let keyState={set:false,masked:'',full:null,shown:false};
function setSettingsStatus(t,c){const st=$('#setstatus');st.textContent=t;st.className='keystatus '+(c||'')}
function renderKey(){
  const cur=$('#keycur'),inp=$('#apikey');
  if(keyState.set){
    const disp=(keyState.shown&&keyState.full!=null)?keyState.full:keyState.masked;
    cur.innerHTML='Current key: <code>'+esc(disp)+'</code>';
    inp.placeholder='Enter a new key to replace it';
  }else{cur.textContent='No key set yet.';inp.placeholder='sk-ant-…'}
  inp.type=keyState.shown?'text':'password';
  $('#keyreveal').classList.toggle('on',keyState.shown);
}
async function loadKey(){
  let d;try{d=await (await fetch('/api/apikey')).json()}catch(e){d={set:false}}
  keyState={set:!!d.set,masked:d.masked||'',full:null,shown:false};
  renderKey();
}
async function toggleReveal(){
  keyState.shown=!keyState.shown;
  if(keyState.shown&&keyState.set&&keyState.full==null){
    try{const d=await (await fetch('/api/apikey?reveal=1')).json();keyState.full=d.value||''}catch(e){}
  }
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
  $('#apikey').value='';keyState={set:false,masked:'',full:null,shown:false};renderKey();
  setSettingsStatus('cleared','ok');
}
$('#keyreveal').addEventListener('click',toggleReveal);
$('#keyclear').addEventListener('click',clearKey);

// ---- AI provider: Anthropic API (default) or Amazon Bedrock (AWS credentials) ----
function renderProv(){
  const bedrock=$('#provider').value==='bedrock';
  $('#bedrockfields').hidden=!bedrock;       // region + model id (Bedrock only)
  $('#apikeysection').hidden=bedrock;         // Claude API key (Anthropic only)
}
async function loadProv(){
  let d;try{d=await (await fetch('/api/provider')).json()}catch(e){d={provider:'anthropic'}}
  $('#provider').value=(d.provider==='bedrock')?'bedrock':'anthropic';
  $('#bedrock-region').value=d.region||'';
  $('#bedrock-model').value=d.model||'';
  renderProv();
}
// ---- Settings: one Save persists the provider, plus the API key when on the Anthropic path ----
async function saveSettings(){
  const provider=$('#provider').value,body={provider};
  if(provider==='bedrock'){
    body.region=$('#bedrock-region').value.trim();
    body.model=$('#bedrock-model').value.trim();
    if(!body.model){setSettingsStatus('enter a Bedrock model id','ng');return}
  }
  setSettingsStatus('saving…','');
  let d;try{d=await (await fetch('/api/provider',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json()}catch(e){d={error:'request failed'}}
  if(d.error){setSettingsStatus(d.error,'ng');return}
  if(provider==='anthropic'){
    const v=$('#apikey').value.trim();
    if(v){
      let k;try{k=await postKey(v)}catch(e){k={error:'request failed'}}
      if(k.error){setSettingsStatus(k.error,'ng');return}
      $('#apikey').value='';keyState={set:true,masked:k.masked||'',full:null,shown:false};renderKey();
    }
  }
  setSettingsStatus('saved','ok');
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

// ---- shared data: apps, scenarios, simulators (used by both views) ----
async function loadShared(){
  try{apps=await (await fetch('/api/apps')).json()}catch(e){apps=[]}
  const opts=apps.map(a=>`<option>${esc(a)}</option>`).join('');
  $('#app').innerHTML=opts;$('#rec-app').innerHTML=opts;$('#crawl-app').innerHTML=opts;
  await loadScenarios();
}
// Scenarios come from the selected app's configured dir, so reload when the Replay app changes.
async function loadScenarios(){
  const app=$('#app').value;
  try{scnFiles=app?await (await fetch('/api/scenarios?app='+encodeURIComponent(app))).json():[]}catch(e){scnFiles=[]}
  $('#scn').innerHTML=scnFiles.map(s=>`<option value="${esc(s.path)}">${esc(s.file)}</option>`).join('');
  showInfo();
}
$('#app').addEventListener('change',loadScenarios);
async function loadSims(){
  try{sims=await (await fetch('/api/simulators')).json()}catch(e){sims=[]}
  // Replay: multi-select checkboxes (parallel pool).
  const el=$('#sims');
  el.innerHTML=sims.length?sims.map(s=>`<label><input type="checkbox" class="simck" value="${esc(s.udid)}"><span class="dot ${s.booted?'ok':'off'}" title="${s.booted?'booted':'shut down'}"></span><span>${esc(s.name)}</span><span class="rt">${esc(s.runtime)}${s.booted?'':' · off'}</span></label>`).join(''):'<div class="empty">no simulators found</div>';
  el.querySelectorAll('.simck').forEach(c=>c.addEventListener('change',onSimChange));
  // Record + Crawl: single-device dropdown ("booted" = whatever is already up). Crawl explores
  // one app instance breadth-first, so it picks a single device rather than a parallel pool.
  const single='<option value="booted">booted (already up)</option>'+sims.map(s=>`<option value="${esc(s.udid)}">${esc(s.name)} · ${esc(s.runtime)}${s.booted?'':' · off'}</option>`).join('');
  $('#rec-device').innerHTML=single;$('#crawl-device').innerHTML=single;
}

// ---- Record: author a scenario from a goal ----
$('#rec-simrefresh').addEventListener('click',loadSims);
$('#rec-go').addEventListener('click',async()=>{
  const goal=$('#rec-goal').value.trim();
  if(!goal){setStatus($('#rec-status'),'enter a goal first','ng');return}
  if(recPoll)clearInterval(recPoll);
  setBusy($('#rec-go'),$('#rec-stop'),true,'Authoring…');$('#rec-out').textContent='';
  $('#rec-yaml').value='';$('#rec-save').disabled=true;$('#rec-yamlinfo').textContent='';recPath=null;
  setStatus($('#rec-status'),'','run');
  const r=await fetch('/api/record',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    goal,app:$('#rec-app').value,agent:$('#rec-agent').value,backend:$('#rec-backend').value.trim(),
    udid:$('#rec-device').value||'booted',name:$('#rec-name').value.trim()||undefined,
    erase:$('#rec-erase').checked,dismissAlerts:$('#rec-nodismiss').checked?false:undefined})});
  const {jobId,path,error}=await r.json();
  if(error){setStatus($('#rec-status'),error,'ng');setBusy($('#rec-go'),$('#rec-stop'),false);return}
  recPath=path;recJobId=jobId;
  recPoll=setInterval(()=>recCheck(jobId),1000);recCheck(jobId);
});
$('#rec-stop').addEventListener('click',()=>cancelJob(recJobId,$('#rec-stop')));
async function recCheck(id){
  const j=await (await fetch('/api/jobs/'+id)).json();
  $('#rec-out').textContent=(j.lines||[]).join('\n');$('#rec-out').scrollTop=$('#rec-out').scrollHeight;
  if(j.status==='running')return;
  clearInterval(recPoll);recPoll=null;recJobId=null;setBusy($('#rec-go'),$('#rec-stop'),false);
  if(j.cancelled){setStatus($('#rec-status'),'cancelled','ng');return}
  setStatus($('#rec-status'),j.ok?'authored ✓':'failed', j.ok?'ok':'ng');
  if(j.ok&&(j.outPath||recPath)){await loadGenerated(j.outPath||recPath);loadScenarios();}
}
async function loadGenerated(path){
  recPath=path;
  try{
    const d=await (await fetch('/api/scenario?app='+encodeURIComponent($('#rec-app').value)+'&path='+encodeURIComponent(path))).json();
    if(d.yaml!=null){$('#rec-yaml').value=d.yaml;$('#rec-save').disabled=false;
      $('#rec-yamlinfo').textContent=path.split('/').pop();}
  }catch(e){}
}
$('#rec-save').addEventListener('click',async()=>{
  if(!recPath)return;
  $('#rec-save').disabled=true;$('#rec-save').textContent='Saving…';
  const r=await fetch('/api/scenario',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({app:$('#rec-app').value,path:recPath,yaml:$('#rec-yaml').value})});
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
$('#scn').addEventListener('change',showInfo);
function pickedUdids(){return [...$('#sims').querySelectorAll('.simck:checked')].map(c=>c.value)}
function onSimChange(){const n=pickedUdids().length;if(n>0)$('#workers').value=n}
$('#simrefresh').addEventListener('click',loadSims);
$('#go').addEventListener('click',async()=>{
  if(poll)clearInterval(poll);
  setBusy($('#go'),$('#stop'),true,'Running…');$('#out').textContent='';
  setStatus($('#status'),'','run');
  const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    scenario:$('#scn').value,app:$('#app').value,backend:$('#backend').value.trim(),udid:pickedUdids().join(',')||'booted',
    workers:parseInt($('#workers').value,10)||1,
    erase:$('#erasedev').checked||undefined,dismissAlerts:$('#nodismiss').checked?false:undefined})});
  const {jobId,error}=await r.json();
  if(error){setStatus($('#status'),error,'ng');setBusy($('#go'),$('#stop'),false);return}
  runJobId=jobId;
  poll=setInterval(()=>check(jobId),1000);check(jobId);
});
$('#stop').addEventListener('click',()=>cancelJob(runJobId,$('#stop')));
async function check(id){
  const j=await (await fetch('/api/jobs/'+id)).json();
  $('#out').textContent=(j.lines||[]).join('\n');$('#out').scrollTop=$('#out').scrollHeight;
  if(j.status==='running')return;  // the Run button (amber + spinner) shows the running state
  clearInterval(poll);poll=null;runJobId=null;setBusy($('#go'),$('#stop'),false);
  if(j.cancelled){setStatus($('#status'),'cancelled','ng');loadHistory();return}
  setStatus($('#status'),j.ok?'PASS':'FAIL', j.ok?'ok':'ng');
  if(j.runId)setReport(j.runId);
  loadHistory();
}
function setReport(id){selectedRun=id;$('#report').innerHTML=`<iframe src="/runs/${id}/report.html"></iframe>`}
async function loadHistory(){
  let runs;try{runs=await (await fetch('/api/runs')).json()}catch(e){return}
  const tab=$('#histtab');if(tab)tab.textContent='History'+(runs.length?` (${runs.length})`:'');
  const ul=$('#history');
  if(!runs.length){ul.innerHTML='<li class="muted">no runs yet</li>';return}
  ul.innerHTML=runs.map(r=>`<li data-id="${r.id}"${r.id===selectedRun?' class="sel"':''}><span class="dot ${r.ok?'ok':'ng'}"></span><span class="hid">${r.id}</span><span class="hsum">${r.passed}/${r.total}${r.scenarios.length?' · '+r.scenarios.join(', '):''}</span></li>`).join('');
  ul.querySelectorAll('li[data-id]').forEach(li=>li.addEventListener('click',()=>{setReport(li.dataset.id);ul.querySelectorAll('li').forEach(x=>x.classList.remove('sel'));li.classList.add('sel')}));
}
$('#refresh').addEventListener('click',loadHistory);
function showTab(name){
  document.querySelectorAll('#view-replay .tab').forEach(t=>t.classList.toggle('active',t.dataset.tab===name));
  $('#panel-run').hidden=name!=='run';$('#panel-history').hidden=name!=='history';
  if(name==='history')loadHistory();
}
document.querySelectorAll('#view-replay .tab').forEach(t=>t.addEventListener('click',()=>showTab(t.dataset.tab)));

// ---- Crawl: explore the app and watch the screen map grow live ----
let crawlPoll=null,crawlJobId=null,crawlRunId=null;
$('#crawl-simrefresh').addEventListener('click',loadSims);
$('#crawl-go').addEventListener('click',async()=>{
  if(crawlPoll)clearInterval(crawlPoll);
  setBusy($('#crawl-go'),$('#crawl-stop'),true,'Crawling…');
  $('#crawl-out').textContent='';$('#crawl-counts').textContent='';
  $('#crawl-graph').innerHTML='<div class="empty">Launching the app and reaching the first screen…</div>';
  setStatus($('#crawl-status'),'','run');
  const r=await fetch('/api/crawl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    app:$('#crawl-app').value,agent:$('#crawl-agent').value,backend:$('#crawl-backend').value.trim(),udid:$('#crawl-device').value||'booted',
    maxScreens:parseInt($('#crawl-maxscreens').value,10)||50,maxSteps:parseInt($('#crawl-maxsteps').value,10)||200,
    erase:$('#crawl-erase').checked,
    dismissAlerts:$('#crawl-nodismiss').checked?false:undefined})});
  const {jobId,runId,error}=await r.json();
  if(error){setStatus($('#crawl-status'),error,'ng');setBusy($('#crawl-go'),$('#crawl-stop'),false);return}
  crawlJobId=jobId;crawlRunId=runId;
  crawlPoll=setInterval(()=>crawlCheck(jobId),1000);crawlCheck(jobId);
});
$('#crawl-stop').addEventListener('click',()=>cancelJob(crawlJobId,$('#crawl-stop')));
async function crawlCheck(id){
  const j=await (await fetch('/api/jobs/'+id)).json();
  $('#crawl-out').textContent=(j.lines||[]).join('\n');$('#crawl-out').scrollTop=$('#crawl-out').scrollHeight;
  if(crawlRunId)await loadGraph(crawlRunId);  // poll the streamed screenmap.json and redraw
  if(j.status==='running')return;
  clearInterval(crawlPoll);crawlPoll=null;crawlJobId=null;setBusy($('#crawl-go'),$('#crawl-stop'),false);
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
  const NW=176,NH=290,COLW=250,ROWH=NH+30,PAD=24;
  const pos=new Map();let maxRows=1;
  layers.forEach((layer,d)=>{if(!layer)return;maxRows=Math.max(maxRows,layer.length);layer.forEach((u,i)=>pos.set(u.id,{x:PAD+d*COLW,y:PAD+i*ROWH}))});
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
  const box=$('#crawl-graph');let drag=null,moved=false;
  box.addEventListener('wheel',e=>{if(!$('.graphwrap'))return;e.preventDefault();zoomBy(e.deltaY<0?1.1:1/1.1,e.clientX,e.clientY)},{passive:false});
  box.addEventListener('mousedown',e=>{if(!$('.graphwrap'))return;drag={x:e.clientX,y:e.clientY,ox:gview.x,oy:gview.y};moved=false;box.classList.add('panning')});
  window.addEventListener('mousemove',e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>3)moved=true;gview.x=drag.ox+dx;gview.y=drag.oy+dy;applyView()});
  window.addEventListener('mouseup',()=>{if(drag){drag=null;box.classList.remove('panning')}});
  // A click that wasn't a drag either expands/collapses a group or opens a screen's lightbox.
  box.addEventListener('click',e=>{if(moved){moved=false;return}
    const col=e.target.closest('.gcollapse');
    if(col){expandedGroups.delete(col.dataset.group);redrawGraph();return}
    const grp=e.target.closest('.ggroup');
    if(grp){expandedGroups.add(grp.dataset.group);redrawGraph();return}
    const node=e.target.closest('.gnode');if(node&&node.dataset.fp)openShot(node.dataset.fp)});
  // Hovering a node lights up only the edges touching it and dims the rest, so a busy web of lines
  // becomes readable on demand. Edges carry data-a/data-b (their endpoint unit ids); the node carries
  // data-uid. Skipped while dragging so a pan doesn't flicker the highlight.
  box.addEventListener('mouseover',e=>{if(drag)return;const n=e.target.closest('.gnode');if(!n||!n.dataset.uid)return;
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
  if(crawlPoll)clearInterval(crawlPoll);
  setBusy($('#crawl-go'),$('#crawl-stop'),true,'Resuming…');
  setStatus($('#crawl-status'),'','run');
  const r=await fetch('/api/crawl',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    app:$('#crawl-app').value,agent:$('#crawl-agent').value,backend:$('#crawl-backend').value.trim(),udid:$('#crawl-device').value||'booted',
    maxScreens:parseInt($('#crawl-maxscreens').value,10)||50,maxSteps:parseInt($('#crawl-maxsteps').value,10)||200,
    dismissAlerts:$('#crawl-nodismiss').checked?false:undefined,
    runId:crawlRunId,resumeSrc:src,resumeKey:key})});
  const {jobId,runId,error}=await r.json();
  if(error){setStatus($('#crawl-status'),error,'ng');setBusy($('#crawl-go'),$('#crawl-stop'),false);return}
  crawlJobId=jobId;crawlRunId=runId;
  crawlPoll=setInterval(()=>crawlCheck(jobId),1000);crawlCheck(jobId);
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

initTheme();
loadConfig();
loadSims();
loadHistory();
setInterval(loadHistory,4000);
