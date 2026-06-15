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

// ---- theme picker (matching CSS-variable blocks live in serve.themes.css) ----
// Add a theme: add a [data-theme="…"] block in serve.themes.css, then add one entry
// here — the <select> is rendered from THEMES, so it shows up automatically.
const THEMES=[{value:'midnight',label:'Midnight (dark)'},{value:'daylight',label:'Daylight (light)'}];
function currentTheme(){
  return document.documentElement.getAttribute('data-theme')
    ||localStorage.getItem('bajutsu-theme')
    ||(matchMedia('(prefers-color-scheme: light)').matches?'daylight':'midnight');
}
function applyTheme(t){
  document.documentElement.setAttribute('data-theme',t);
  try{localStorage.setItem('bajutsu-theme',t)}catch(e){}
  const sel=$('#theme');if(sel)sel.value=t;
}
function initTheme(){
  const sel=$('#theme');
  sel.innerHTML=THEMES.map(t=>`<option value="${esc(t.value)}">${esc(t.label)}</option>`).join('');
  applyTheme(currentTheme());
  sel.addEventListener('change',()=>applyTheme(sel.value));
}

// ---- top-level Record / Replay views ----
function showView(name){
  document.querySelectorAll('.toptab').forEach(t=>t.classList.toggle('active',t.dataset.view===name));
  $('#view-record').hidden=name!=='record';$('#view-replay').hidden=name!=='replay';
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
  $('#app').innerHTML=opts;$('#rec-app').innerHTML=opts;
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
  // Record: single-device dropdown ("booted" = whatever is already up).
  $('#rec-device').innerHTML='<option value="booted">booted (already up)</option>'+sims.map(s=>`<option value="${esc(s.udid)}">${esc(s.name)} · ${esc(s.runtime)}${s.booted?'':' · off'}</option>`).join('');
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

initTheme();
loadConfig();
loadSims();
loadHistory();
setInterval(loadHistory,4000);
