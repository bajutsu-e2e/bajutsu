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
function setKeyStatus(t,c){const st=$('#keystatus');st.textContent=t;st.className='keystatus '+(c||'')}
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
async function saveKey(){
  const inp=$('#apikey'),v=inp.value.trim();
  if(!v){setKeyStatus('enter a key, or use Clear to remove it','ng');return}
  setKeyStatus('saving…','');
  let d;try{d=await postKey(v)}catch(e){d={error:'request failed'}}
  if(d.error){setKeyStatus(d.error,'ng');return}
  inp.value='';keyState={set:true,masked:d.masked||'',full:null,shown:false};renderKey();
  setKeyStatus('saved','ok');
}
async function clearKey(){
  if(!keyState.set){setKeyStatus('no key to clear','ng');return}
  setKeyStatus('clearing…','');
  let d;try{d=await postKey('')}catch(e){d={error:'request failed'}}
  if(d.error){setKeyStatus(d.error,'ng');return}
  $('#apikey').value='';keyState={set:false,masked:'',full:null,shown:false};renderKey();
  setKeyStatus('cleared','ok');
}
function openKeyModal(){$('#keymodal').hidden=false;$('#apikey').value='';setKeyStatus('','');loadKey()}
function closeKeyModal(){$('#keymodal').hidden=true}
$('#openkey').addEventListener('click',openKeyModal);
$('#keyclose').addEventListener('click',closeKeyModal);
$('#keymodal').addEventListener('click',e=>{if(e.target===$('#keymodal'))closeKeyModal()});
$('#keyreveal').addEventListener('click',toggleReveal);
$('#keysave').addEventListener('click',saveKey);
$('#keyclear').addEventListener('click',clearKey);
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
    app:$('#crawl-app').value,backend:$('#crawl-backend').value.trim(),udid:$('#crawl-device').value||'booted',
    maxScreens:parseInt($('#crawl-maxscreens').value,10)||50,maxSteps:parseInt($('#crawl-maxsteps').value,10)||200,
    guide:$('#crawl-guide').value,erase:$('#crawl-erase').checked,
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
}
// Lay the screen map out as a BFS-layered graph: screens become columns by depth, transitions
// curved edges with arrowheads. Each node shows the screen's screenshot (captured to
// runs/<id>/screens/<fingerprint>.png) above a short fingerprint label; click a node to enlarge
// it and see where it goes next. Edges are one SVG layer; nodes are HTML <img> tiles on top (an
// HTML <img> loads reliably, where an SVG <image> set via innerHTML does not). The view (zoom /
// pan) and the data are kept across the per-poll re-render so nodes stay put as the map grows.
let crawlGraphData=null,crawlGraphRunId=null;
const gview={x:0,y:0,k:1};  // pan (px) + zoom (scale), applied as a transform on the graph layer
function shotURL(runId,fp){return `/runs/${encodeURIComponent(runId)}/screens/${encodeURIComponent(fp)}.png`}
function renderGraph(data,runId){
  crawlGraphData=data;crawlGraphRunId=runId;
  const nodes=data.nodes||[],edges=data.edges||[],crashes=data.crashes||[];
  $('#crawl-counts').textContent=`${nodes.length} screens · ${edges.length} transitions · ${crashes.length} crashes`;
  const box=$('#crawl-graph');
  if(!nodes.length){box.innerHTML='<div class="empty">Reaching the first screen…</div>';return}
  const idx=new Map(nodes.map(n=>[n.fingerprint,n]));
  const incoming=new Set(edges.map(e=>e.dst));
  const adj=new Map(nodes.map(n=>[n.fingerprint,[]]));
  edges.forEach(e=>{if(adj.has(e.src)&&idx.has(e.dst))adj.get(e.src).push(e.dst)});
  // Depth = BFS distance from a root (a screen nothing leads into; fall back to the first node).
  const depth=new Map();
  let roots=nodes.filter(n=>!incoming.has(n.fingerprint)).map(n=>n.fingerprint);
  if(!roots.length)roots=[nodes[0].fingerprint];
  const q=[...roots];roots.forEach(r=>depth.set(r,0));
  while(q.length){const f=q.shift(),d=depth.get(f);(adj.get(f)||[]).forEach(t=>{if(!depth.has(t)){depth.set(t,d+1);q.push(t)}})}
  nodes.forEach(n=>{if(!depth.has(n.fingerprint))depth.set(n.fingerprint,0)});
  const layers=[];nodes.forEach(n=>{const d=depth.get(n.fingerprint);(layers[d]||(layers[d]=[])).push(n)});
  // Node = a screenshot tile (IMGH tall) + a label strip; sized for the portrait shot.
  const NW=120,IMGH=150,NH=IMGH+26,COLW=190,ROWH=NH+30,PAD=24;
  const pos=new Map();let maxRows=1;
  layers.forEach((layer,d)=>{if(!layer)return;maxRows=Math.max(maxRows,layer.length);layer.forEach((n,i)=>pos.set(n.fingerprint,{x:PAD+d*COLW,y:PAD+i*ROWH}))});
  const W=PAD*2+(layers.length-1)*COLW+NW,H=PAD*2+(maxRows-1)*ROWH+NH;
  // Edge layer (SVG), sized to the same coordinate space the node tiles are positioned in.
  let svg=`<svg class="graphsvg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">`;
  svg+=`<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="var(--mut)"/></marker></defs>`;
  const seen=new Set();
  edges.forEach(e=>{const k=e.src+'>'+e.dst;if(seen.has(k))return;seen.add(k);
    const a=pos.get(e.src),b=pos.get(e.dst);if(!a||!b)return;
    if(e.src===e.dst){const x=a.x+NW,y=a.y+NH/2;svg+=`<path class="edge selfloop" d="M${x},${y-8} C${x+34},${y-26} ${x+34},${y+26} ${x},${y+8}" marker-end="url(#arrow)"/>`;return}
    const x1=a.x+NW,y1=a.y+NH/2,x2=b.x,y2=b.y+NH/2,mx=(x1+x2)/2;
    svg+=`<path class="edge" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}" marker-end="url(#arrow)"/>`;
  });
  svg+='</svg>';
  // Node tiles (HTML, absolutely positioned), each a real <img> so the screenshot reliably loads.
  let tiles='';
  nodes.forEach(n=>{const p=pos.get(n.fingerprint),cls='gnode'+(n.kind==='structural'?' structural':'');
    const sub=(n.ids&&n.ids.length?n.ids.length+' ids':'no ids')+' · '+((n.actions||[]).length)+' actions';
    tiles+=`<div class="${cls}" data-fp="${esc(n.fingerprint)}" style="left:${p.x}px;top:${p.y}px;width:${NW}px;height:${NH}px" title="${esc(n.fingerprint.slice(0,7))} — ${esc(sub)}">`+
      `<img class="gshot" src="${shotURL(runId,n.fingerprint)}" alt="" loading="lazy" onerror="this.classList.add('missing')">`+
      `<div class="glabel">${esc(n.fingerprint.slice(0,7))}${n.kind==='structural'?' ~':''}</div></div>`;
  });
  box.innerHTML=`<div class="graphwrap" style="width:${W}px;height:${H}px">${svg}${tiles}</div>`;
  applyView();
}
// ---- zoom / pan: a transform on the graph layer, re-applied after each re-render ----
function applyView(){const w=$('.graphwrap');if(w)w.style.transform=`translate(${gview.x}px,${gview.y}px) scale(${gview.k})`}
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
  // A click that wasn't a drag opens the node's lightbox.
  box.addEventListener('click',e=>{if(moved){moved=false;return}const node=e.target.closest('.gnode');if(node&&node.dataset.fp)openShot(node.dataset.fp)});
})();
$('#crawl-zoomin').addEventListener('click',()=>{const r=$('#crawl-graph').getBoundingClientRect();zoomBy(1.2,r.left+r.width/2,r.top+r.height/2)});
$('#crawl-zoomout').addEventListener('click',()=>{const r=$('#crawl-graph').getBoundingClientRect();zoomBy(1/1.2,r.left+r.width/2,r.top+r.height/2)});
$('#crawl-zoomreset').addEventListener('click',resetView);

// ---- lightbox: enlarge a screen's shot and step through the transitions (before / after) ----
let shotFp=null;  // the screen currently shown, so prev/next can walk the graph from it
// One row per transition: the action and the other screen's thumbnail, clickable to step there.
function transitionRows(list,field){
  return list.map(e=>{const fp=e[field];
    return `<button class="nextrow" data-fp="${esc(fp)}">`+
      `<img src="${shotURL(crawlGraphRunId,fp)}" alt="" onerror="this.style.visibility='hidden'">`+
      `<span class="nxtxt"><span class="nxa">${esc(e.action)}</span>`+
      `<span class="nxf">${field==='dst'?'→':'←'} ${esc(fp.slice(0,7))}${fp===shotFp?' (self)':''}</span></span></button>`;
  }).join('');
}
function openShot(fp){
  if(!crawlGraphData)return;
  const nodes=crawlGraphData.nodes||[],edges=crawlGraphData.edges||[];
  const node=nodes.find(n=>n.fingerprint===fp);if(!node)return;
  shotFp=fp;
  $('#shotimg').src=shotURL(crawlGraphRunId,fp);
  $('#shottitle').textContent=`${fp.slice(0,7)}${node.kind==='structural'?' (structural)':''} · ${(node.ids||[]).length} ids · ${(node.actions||[]).length} actions`;
  const out=edges.filter(e=>e.src===fp),inc=edges.filter(e=>e.dst===fp);
  // Arrows step to the first transition before / after this screen (the lists below pick a specific one).
  $('#shotprev').disabled=!inc.length;$('#shotfwd').disabled=!out.length;
  let h=`<div class="nexthd${out.length?'':' muted'}">Goes to ${out.length} screen(s) →</div>`+transitionRows(out,'dst');
  h+=`<div class="nexthd${inc.length?'':' muted'}">← Comes from ${inc.length} screen(s)</div>`+transitionRows(inc,'src');
  $('#shotnext').innerHTML=h;
  $('#shotmodal').hidden=false;
}
// Step to the screen after (a transition out of) or before (a transition into) the current one.
function shotStep(dir){
  if(!crawlGraphData||shotFp==null)return;
  const edges=crawlGraphData.edges||[];
  const e=dir==='fwd'?edges.find(x=>x.src===shotFp&&x.dst!==shotFp):edges.find(x=>x.dst===shotFp&&x.src!==shotFp);
  if(e)openShot(dir==='fwd'?e.dst:e.src);
}
function closeShot(){$('#shotmodal').hidden=true;$('#shotimg').removeAttribute('src');shotFp=null}
$('#shotmodal').addEventListener('click',e=>{if(e.target===$('#shotmodal')||e.target===$('#shotclose'))closeShot()});
$('#shotnext').addEventListener('click',e=>{const b=e.target.closest('.nextrow');if(b&&b.dataset.fp)openShot(b.dataset.fp)});
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
