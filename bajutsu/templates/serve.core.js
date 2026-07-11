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
// A theme id from a display name: lowercased, non-alnum runs hyphenated, edges trimmed. Mirrors the
// server's `_slug` so an exported/uploaded theme's id (its `[data-theme]` selector and filename
// stem) agrees across both sides.
function slugTheme(name){return (name||'').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'');}

// Keep the `[data-theme="custom"]` block in sync with the local draft's tokens. Scoped to the custom
// theme (unlike the editor's `:root` live preview), so it only paints when `custom` is the selected
// theme; tokens the draft omits fall back to the :root/midnight defaults, as the contract promises.
function applyCustomThemeStyle(draft){
  let el=$('#theme-custom');
  if(!el){el=document.createElement('style');el.id='theme-custom';document.head.appendChild(el);}
  const rules=Object.entries(draft&&draft.tokens||{}).filter(([k,v])=>safeThemeToken(k,v)).map(([k,v])=>`${k}:${v};`).join('');
  el.textContent=`[data-theme="custom"]{${rules}}`;
}

// Reflect the local draft as a `custom` entry in the picker: register it in THEMES (so it resolves
// like any theme), inject its `<style>`, and add/refresh its <option>. With no draft, tear all three
// down so a cleared draft leaves no dangling entry.
function surfaceCustomDraft(){
  const sel=$('#theme');
  const opt=sel?sel.querySelector('option[value="custom"]'):null;
  const draft=readCustomDraft();
  if(!draft){
    if(opt)opt.remove();
    const el=$('#theme-custom');if(el)el.remove();
    const i=THEMES.findIndex(x=>x.id==='custom');if(i>=0)THEMES.splice(i,1);
    return;
  }
  const label=draft.name||'custom',kind=draft.kind==='light'?'light':'dark';
  const entry=THEMES.find(x=>x.id==='custom');
  if(entry){entry.name=label;entry.kind=kind;}else{THEMES.push({id:'custom',name:label,kind:kind});}
  applyCustomThemeStyle(draft);
  if(!sel)return;
  // Always route through the optgroup for the current kind; appendChild re-parents an already-attached
  // <option> (keeping its selected state), so editing the draft's kind moves `custom` under the right
  // Dark/Light section header rather than stranding it under the old one.
  const target=sel.querySelector(`optgroup[label="${kind==='light'?'Light':'Dark'}"]`)||sel;
  if(opt){opt.textContent=label;target.appendChild(opt);}
  else{
    const o=document.createElement('option');o.value='custom';o.textContent=label;
    target.appendChild(o);
  }
}

function initTheme(){
  const sel=$('#theme');
  // Register any saved local draft before resolving the active theme, so a previously-selected
  // `custom` pick (persisted in localStorage) both lists in the picker and paints on load.
  surfaceCustomDraft();
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
    // Fade the pane brought to full width (BE-0191 unit 4); a no-op on desktop, under reduced motion,
    // or when the theme opts the enter animation out (--motion-view-enter:none → animationend never fires).
    if(!prefersReducedMotion()&&!motionOff('--motion-view-enter')){
      view.classList.remove('pane-switching');void view.offsetWidth;view.classList.add('pane-switching');
      // End on the pane's own fade — guard e.target so an unrelated descendant animation (e.g. a
      // .running spinner bubbling up) doesn't strip the class early and cut the fade short.
      const done=e=>{if(!e.target.matches('[data-pane]'))return;view.removeEventListener('animationend',done);view.classList.remove('pane-switching');};
      view.addEventListener('animationend',done);
    }
  }));
});

// ---- themable screen transitions (BE-0191 unit 4) ----
// JS only applies semantic state classes; the theme's CSS (--motion-* tokens + keyframes) decides
// what they look like. Everything below is a no-op under prefers-reduced-motion, which is also the
// determinism lever (unit 5): the Playwright backend runs with reduced_motion=reduce, so in the
// dogfood every transition is instant and no condition-wait ever races an animation.
const REDUCED_MOTION=matchMedia('(prefers-reduced-motion: reduce)');
const prefersReducedMotion=()=>REDUCED_MOTION.matches;
// Read a --motion-* animation-name token off <html> (where the theme sets it); '' / 'none' means the
// theme opts that transition out, so it plays instantly.
const motionOff=tok=>{const v=getComputedStyle(document.documentElement).getPropertyValue(tok).trim();return !v||v==='none';};
// Restart-and-play an enter animation: toggling the class off, forcing a reflow, then on again lets
// the same element re-animate on a repeat (a second view switch to the same view). `tok` is the
// --motion-* enter token for this surface; if the theme sets it to `none`, skip immediately — with
// animation-name:none no animationend fires, so the class and its listener would otherwise leak.
function playEnter(el,tok){
  if(prefersReducedMotion()||motionOff(tok))return;
  el.classList.remove('is-entering');void el.offsetWidth;el.classList.add('is-entering');
  const done=e=>{if(e.target!==el)return;el.removeEventListener('animationend',done);el.classList.remove('is-entering');};
  el.addEventListener('animationend',done);
}
// Close a modal with its leave animation, then hide it (and run an optional content-reset once the
// animation is done, so nothing blanks mid-fade). Hidden instantly under reduced motion or when the
// theme sets --motion-modal-leave:none (no animationend would fire) — the dogfood path is the former.
function closeModal(el,cleanup){
  if(el.hidden){if(cleanup)cleanup();return;}
  const finish=()=>{el.classList.remove('is-leaving');el.hidden=true;el._closeAbort=null;if(cleanup)cleanup();};
  if(prefersReducedMotion()||motionOff('--motion-modal-leave')){finish();return;}
  el.classList.remove('is-entering');  // cancel an in-flight enter so the leave starts clean
  el.classList.add('is-leaving');
  // Arm the hide on the modal's own leave animation. An AbortController lets a reopen (the observer
  // below) cancel this pending listener, so a modal reopened mid-close is not hidden by a stale event.
  if(el._closeAbort)el._closeAbort.abort();
  el._closeAbort=new AbortController();
  el.addEventListener('animationend',e=>{if(e.target===el)finish();},{signal:el._closeAbort.signal});
}
// Play the enter animation whenever a modal becomes visible (its `hidden` attribute is removed),
// regardless of which open path unhid it — so the many openFs/openSettings/… sites need no change.
document.querySelectorAll('.modal').forEach(m=>new MutationObserver(muts=>{
  for(const mu of muts){
    if(mu.attributeName==='hidden'&&!m.hidden){
      if(m._closeAbort){m._closeAbort.abort();m._closeAbort=null;}  // reopened mid-close: cancel the pending hide
      m.classList.remove('is-leaving');playEnter(m,'--motion-modal-enter');
    }
  }
}).observe(m,{attributes:true,attributeFilter:['hidden']}));

// ---- top-level Record / Replay / Crawl views ----
function showView(name){
  document.querySelectorAll('.toptab').forEach(t=>t.classList.toggle('active',t.dataset.view===name));
  $('#view-record').hidden=name!=='record';$('#view-replay').hidden=name!=='replay';$('#view-crawl').hidden=name!=='crawl';$('#view-author').hidden=name!=='author';$('#view-stats').hidden=name!=='stats';$('#view-flaky').hidden=name!=='flaky';$('#view-usage').hidden=name!=='usage';$('#view-coverage').hidden=name!=='coverage';
  // The incoming view animates in (enter-only: the outgoing one is hidden instantly, so two sibling
  // views never overlap in the flex column). The picked theme decides the motion via --motion-view-*.
  const shown=$('#view-'+name);if(shown)playEnter(shown,'--motion-view-enter');
  if(name==='replay')loadHistory();
  if(name==='author')authorInit();
  if(name==='stats')loadStats();
  if(name==='flaky')loadFlaky();
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
function closeFs(){closeModal($('#fsmodal'))}
$('#opencfg').addEventListener('click',openFs);
$('#fsclose').addEventListener('click',closeFs);
$('#fsmodal').addEventListener('click',e=>{if(e.target===$('#fsmodal'))closeFs()});

// ---- project hub (BE-0225 unit 4): the header switcher + the projects list ----
// `serve` is a hub over several named config bindings. Activating one rebinds state.config on the
// server; we then reload the config label and the shared target/scenario lists so every tab runs
// against the switched-to config with no restart. Every `serve` implicitly registers its loaded
// config as one project, so the switcher + Projects button stay hidden until a real hub exists (more
// than one project to choose between) — a single-config serve is unchanged. Projects are added/removed
// with the `bajutsu project` CLI (unit 5), not here — this surface switches and inspects them.
let projectsCache=[];
async function loadProjects(){
  const list=await getJSON('/api/projects',[]);
  projectsCache=Array.isArray(list)?list:[];
  const hub=projectsCache.length>1;
  $('#projectsw').hidden=!hub;$('#openprojects').hidden=!hub;
  renderSwitcher();renderProjectsList();
}
function renderSwitcher(){
  $('#projectsw').innerHTML=projectsCache.map(p=>`<option value="${esc(p.name)}"${p.active?' selected':''}>${esc(p.name)}</option>`).join('');
}
// A one-line, human-readable summary of a project's config source for the list.
function projectSourceLabel(source){
  if(!source||typeof source!=='object')return 'unbound';
  const loc=source.locator||{};
  if(source.kind==='git')return 'git: '+[loc.owner,loc.repo].filter(Boolean).join('/')+(loc.ref?('@'+loc.ref):'');
  if(source.kind==='file')return 'file: '+(loc.path||'');
  if(source.kind==='upload')return 'uploaded bundle';
  return source.kind||'unbound';
}
function projectVerdict(run){
  if(!run)return '<span class="prjv none">no runs</span>';
  return `<span class="prjv ${run.ok?'ok':'ng'}">${run.ok?'PASS':'FAIL'} ${run.passed}/${run.total}</span>`;
}
function renderProjectsList(){
  const ul=$('#projectslist');if(!ul)return;
  if(!projectsCache.length){ul.innerHTML='<li class="muted">no projects yet — add one with <code>bajutsu project add</code></li>';return}
  ul.innerHTML=projectsCache.map(p=>`<li class="prjrow" data-testid="projects.row" data-name="${esc(p.name)}"${p.active?' data-active="1"':''}>
    <span class="prjname" data-testid="projects.name">${esc(p.name)}</span>
    <span class="prjsrc">${esc(projectSourceLabel(p.source))}</span>
    ${projectVerdict(p.lastRun)}
    ${p.active?'<span class="prjactive" data-testid="projects.active">active</span>':'<button class="cfgbtn" data-act="run" data-testid="projects.run">Run</button>'}
  </li>`).join('');
  ul.querySelectorAll('button[data-act="run"]').forEach(b=>b.addEventListener('click',()=>switchProject(b.closest('.prjrow').dataset.name,{goReplay:true})));
}
// Activate a project (rebind the live config), then re-sync the config label + shared lists + the
// switcher. A refused switch (e.g. an uploaded bundle with no checkout, a moved file) surfaces the
// server's error and re-syncs the select so it never lies about what is active.
async function switchProject(name,opts){
  const d=await postJSON('/api/projects/'+encodeURIComponent(name)+'/activate',{},{error:'switch failed'});
  if(d.error){alert(d.error);await loadProjects();return}
  await loadConfig();
  await loadProjects();
  if(opts&&opts.goReplay){closeProjects();showView('replay')}
}
function openProjects(){loadProjects();$('#projectsmodal').hidden=false}
function closeProjects(){closeModal($('#projectsmodal'))}
$('#projectsw').addEventListener('change',e=>switchProject(e.target.value));
$('#openprojects').addEventListener('click',openProjects);
$('#projectsclose').addEventListener('click',closeProjects);
$('#projectsmodal').addEventListener('click',e=>{if(e.target===$('#projectsmodal'))closeProjects()});

// ---- view the loaded config: a structured key/value tree (or the raw YAML) + its Git origin ----
function closeCfgView(){closeModal($('#cfgviewmodal'))}
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

// ---- Claude Code OAuth token (BE-0215): the claude-code provider's headless credential. Same
// write-once shape as the API key — masked only, never revealed — held under CLAUDE_CODE_OAUTH_TOKEN.
let ccTokState={set:false,masked:''};
function renderCcTok(){
  const cur=$('#cctokcur'),inp=$('#cctoken');if(!cur||!inp)return;
  if(ccTokState.set){
    cur.innerHTML='Current token: <code>'+esc(ccTokState.masked)+'</code>';
    inp.placeholder='Enter a new token to replace it';
  }else{cur.textContent='No token set yet.';inp.placeholder='sk-ant-oat01-…'}
}
async function loadCcTok(){
  const d=await getJSON('/api/claudecodetoken',{set:false});
  ccTokState={set:!!d.set,masked:d.masked||''};
  renderCcTok();
}
async function postCcTok(value){
  const r=await fetch('/api/claudecodetoken',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({value})});
  return r.json();
}
async function clearCcTok(){
  if(!ccTokState.set){setSettingsStatus('no token to clear','ng');return}
  setSettingsStatus('clearing…','');
  let d;try{d=await postCcTok('')}catch(e){d={error:'request failed'}}
  if(d.error){setSettingsStatus(d.error,'ng');return}
  $('#cctoken').value='';ccTokState={set:false,masked:''};renderCcTok();
  setSettingsStatus('cleared','ok');
}
$('#cctokclear').addEventListener('click',clearCcTok);

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
  if(provider==='claude-code'){  // optional headless credential; blank leaves an interactive login in place (BE-0215)
    const v=$('#cctoken').value.trim();
    if(v){
      let t;try{t=await postCcTok(v)}catch(e){t={error:'request failed'}}
      if(t.error){setSettingsStatus(t.error,'ng');return}
      $('#cctoken').value='';ccTokState={set:true,masked:t.masked||''};renderCcTok();
    }
  }
  // persisted: true = durably saved; false = save failed (won't survive restart); null = session-only
  // (hosted deployment, no durable store). Only false warrants a warning (BE-0184).
  if(d.persisted===false){setSettingsStatus('active for this session, but could not be saved — it will reset on restart','ng');}
  else{setSettingsStatus('saved','ok');}
  refreshAiAvailability();  // a just-saved key / provider can flip the record/crawl gate live
  // The ant sign-in button reads the *server-side* active provider (d.provider==='ant'), which only
  // becomes ant once the save lands; refresh it now so "Signed in ✓" reflects the save without
  // waiting for the modal to reopen or the dropdown to toggle.
  if(provider==='ant')refreshAntLogin();
}
// ---- Settings modal: one panel for the provider + API-key controls ----
function openSettings(){$('#settingsmodal').hidden=false;$('#apikey').value='';$('#cctoken').value='';setSettingsStatus('','');loadKey();loadCcTok();loadProv()}
function closeSettings(){closeModal($('#settingsmodal'))}
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
  // each target carries its primary backend (data-backend) so the UI shows only that platform's device controls
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

// ---- theme editor (BE-0191 unit 6): modal form generated from the token contract ----
// (Re)build the editor form from the token contract. Called on every open — the form's inputs are
// replaced wholesale (innerHTML), so their `input` listeners live on the fresh elements and never
// stack. The modal's *static* buttons are wired once at page load (see the bottom of this file), so
// re-opening the editor cannot duplicate their listeners.
async function initThemeEditor(){
  // A distinct sentinel on fetch failure: getJSON swallows a network error into its fallback, so an
  // empty {colors,transitions} could mean either "server returned nothing" or "request failed". Mark
  // the fallback so a real failure shows the same "contract not available" status a 500 body gets,
  // rather than silently rendering an empty form.
  const contract=await getJSON('/api/themecontract',{error:'unreachable',colors:{},transitions:{}});
  if(contract.error){setStatus($('#themestatus'),'contract not available','ng');return;}

  const htmlParts=[];
  // Reopen onto the saved local draft if one exists, so editing continues where it left off rather
  // than resetting to the contract defaults every open.
  const draft=readCustomDraft();

  // The name seeds the export filename, the `custom` picker entry, and the uploaded theme's id — so
  // the author names the theme here rather than it defaulting to a fixed "custom".
  htmlParts.push('<div class="setsection"><div class="setlabel">Theme</div>');
  htmlParts.push('<label class="keylabel" for="theme-name">name</label>');
  htmlParts.push(`<input type="text" id="theme-name" data-testid="theme.name" class="keyinput" value="${esc(draft&&draft.name||'')}" placeholder="e.g. Ocean">`);
  // Kind decides how the OS-scheme picker matches this theme once dropped in — a real property, not
  // cosmetic — so the author picks it rather than it being hardcoded.
  htmlParts.push('<label class="keylabel" for="theme-kind">kind</label>');
  htmlParts.push('<select id="theme-kind" data-testid="theme.kind" class="keyinput"><option value="dark">dark</option><option value="light">light</option></select>');
  htmlParts.push('</div>');

  // Color token inputs. A native color swatch only when the default is a plain hex, since it silently
  // coerces anything else (e.g. --scrim's rgba(...)) to #000000; a non-hex color (rgba/hsl) gets a
  // text input so its value round-trips untouched.
  if(Object.keys(contract.colors||{}).length>0){
    htmlParts.push('<div class="setsection"><div class="setlabel">Colors</div>');
    for(const[token,meta] of Object.entries(contract.colors||{})){
      const val=meta.default||'';
      htmlParts.push(`<label class="keylabel" for="${esc(token)}">${esc(token)}</label>`);
      if(/^#[0-9a-fA-F]{6}$/.test(val)){
        htmlParts.push(`<input type="color" id="${esc(token)}" value="${esc(val)}" class="theme-color" data-token="${esc(token)}">`);
      }else{
        htmlParts.push(`<input type="text" id="${esc(token)}" value="${esc(val||'#ffffff')}" class="theme-color" data-token="${esc(token)}" placeholder="e.g. rgba(0,0,0,.5)">`);
      }
    }
    htmlParts.push('</div>');
  }

  // Motion token inputs (text: durations, easing, and keyframe names are all free-form CSS values).
  if(Object.keys(contract.transitions||{}).length>0){
    htmlParts.push('<div class="setsection"><div class="setlabel">Motion</div>');
    for(const[token,meta] of Object.entries(contract.transitions||{})){
      const val=meta.default||'';
      const type=meta.type||'unknown';
      const ph=type==='duration'?'e.g. 0.18s':type==='easing'?'e.g. cubic-bezier(.4,0,.2,1)':'keyframe name';
      htmlParts.push(`<label class="keylabel" for="${esc(token)}">${esc(token)}</label>`);
      htmlParts.push(`<input type="text" id="${esc(token)}" value="${esc(val)}" class="theme-motion" data-token="${esc(token)}" placeholder="${ph}">`);
    }
    htmlParts.push('</div>');
  }

  $('#themecontent').innerHTML=htmlParts.join('');
  // Overlay the saved draft's kind + token values onto the freshly built form (which defaulted to the
  // contract), so reopening resumes the draft rather than discarding it.
  if(draft){
    if(draft.kind)$('#theme-kind').value=draft.kind;
    for(const[k,v] of Object.entries(draft.tokens||{})){const el=document.getElementById(k);if(el)el.value=v;}
    applyThemePreview(collectThemeTokens());
  }
  document.querySelectorAll('#themecontent [class^="theme-"]').forEach(el=>el.addEventListener('input',()=>applyThemePreview(collectThemeTokens())));
}

// The saved local draft ({tokens,name,kind}) or null. A parse/quota failure degrades to "no draft"
// rather than throwing — the editor still opens on the contract defaults.
function readCustomDraft(){
  try{const s=localStorage.getItem('bajutsu-custom-theme-draft');return s?JSON.parse(s):null;}catch(e){return null;}
}

// The current form's token values, keyed by token name.
function collectThemeTokens(){
  const tokens={};
  document.querySelectorAll('#themecontent [class^="theme-"]').forEach(el=>{tokens[el.dataset.token]=el.value;});
  return tokens;
}
// The author's chosen kind (defaults to dark if the form isn't built yet).
function currentThemeKind(){const s=$('#theme-kind');return s?s.value:'dark';}

// A token name/value that can't break out of the `:root{ … }` rule it's interpolated into. A theme
// is operator-trusted (drop-in / their own upload — BE-0191), so this isn't a security boundary; it
// stops a malformed *imported* value (a stray `{`/`}`/`;`) from silently corrupting the whole preview
// stylesheet. The token name must be a plain custom property; the value carries no rule-delimiters.
const safeThemeToken=(k,v)=>/^--[\w-]+$/.test(k)&&!/[{};]/.test(v);

// Apply live preview: inject or update a <style> block with the edited token values.
function applyThemePreview(tokens){
  let styleEl=$('#theme-editor-preview');
  if(!styleEl){
    styleEl=document.createElement('style');
    styleEl.id='theme-editor-preview';
    document.head.appendChild(styleEl);
  }
  const rules=Object.entries(tokens).filter(([k,v])=>safeThemeToken(k,v)).map(([k,v])=>`${k}:${v};`).join('');
  styleEl.textContent=`:root{${rules}}`;
}

// The author's chosen name (empty if the form isn't built yet).
function currentThemeName(){const el=$('#theme-name');return el?el.value.trim():'';}

// Save the edited theme to localStorage as a local draft, then surface it as the `custom` picker
// entry and switch to it — so "Save to Local Draft" makes the look immediately selectable.
function saveThemeLocal(){
  const draft={tokens:collectThemeTokens(),name:currentThemeName()||'custom',kind:currentThemeKind()};
  try{localStorage.setItem('bajutsu-custom-theme-draft',JSON.stringify(draft))}catch(e){
    setStatus($('#themestatus'),'failed to save (quota exceeded?)','ng');return;
  }
  surfaceCustomDraft();
  applyTheme('custom',true);
  setStatus($('#themestatus'),'saved to local draft','ok');
}

// Export theme as a CSS file that round-trips with the drop-in format (manifest comment + block).
// The id (its `[data-theme]` selector and filename stem) is the slug of the name — the same id the
// server derives on upload — so an exported file, dropped into --themes, discovers under that id.
function exportTheme(){
  const tokens=collectThemeTokens(),kind=currentThemeKind(),name=currentThemeName()||'custom';
  const id=slugTheme(name)||'custom';
  // Mirror the server's manifest-name guard (upload_theme): strip `*/` and newlines/CR so a name
  // can't close the comment early or introduce its own field line, and emit kind before name so the
  // authoritative kind wins the parser's unanchored first-match `kind:` search.
  const manifest=`/* bajutsu-theme\nkind: ${kind}\nname: ${name.replace(/\*\//g,'').replace(/[\r\n]/g,' ').trim()||id}\n*/\n`;
  const css=`[data-theme="${id}"]{\n${Object.entries(tokens).filter(([k,v])=>safeThemeToken(k,v)).map(([k,v])=>`  ${k}: ${v};`).join('\n')}\n}\n`;
  const blob=new Blob([manifest+css],{type:'text/css'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download=`${id}-theme.css`;a.click();
  URL.revokeObjectURL(url);
}

// Upload the edited theme to the serve instance's --themes dir (BE-0191 unit 6, part 2). The server
// slugs the name into the id, composes the canonical file, and rescans — so it becomes a discoverable
// drop-in on the next load. Only wired when the instance was started with --themes (see below).
async function uploadTheme(){
  const name=currentThemeName();
  if(!name){setStatus($('#themestatus'),'a theme name is required to upload','ng');return;}
  const res=await postJSON('/api/theme',{name:name,kind:currentThemeKind(),tokens:collectThemeTokens()},{error:'upload failed'});
  if(res&&res.ok)setStatus($('#themestatus'),'uploaded'+(res.overwritten?' (overwritten)':'')+' — reload to see it in the picker','ok');
  else setStatus($('#themestatus'),(res&&res.error)||'upload failed','ng');
}

// Import a theme file: parse the [data-theme] block, populate the form, and preview. Values that a
// native color input rejects are reported rather than silently dropped, so an invalid file fails
// loudly instead of appearing to import while leaving those swatches unchanged.
function importThemeFile(){
  const file=$('#themeimport-input').files[0];
  if(!file){return;}
  const reader=new FileReader();
  reader.onerror=()=>setStatus($('#themestatus'),'failed to read file','ng');
  reader.onload=e=>{
    try{
      const match=e.target.result.match(/\[data-theme="[^"]+"\]\s*{([^}]*)}/);
      if(!match){setStatus($('#themestatus'),'invalid theme file (no [data-theme] rule)','ng');return;}
      const tokens={};
      for(const line of match[1].split(';')){
        const idx=line.indexOf(':');
        if(idx>0){const k=line.slice(0,idx).trim();if(k.startsWith('--'))tokens[k]=line.slice(idx+1).trim();}
      }
      const rejected=[],unknown=[];
      for(const[k,v] of Object.entries(tokens)){
        const el=document.getElementById(k);
        if(!el){unknown.push(k);continue;}  // token in the file has no form field (older export or typo)
        el.value=v;
        if(el.value.toLowerCase()!==v.toLowerCase()&&el.type==='color')rejected.push(k);  // the color input coerced/dropped it
      }
      applyThemePreview(collectThemeTokens());
      const msgs=[];
      if(rejected.length)msgs.push('invalid value for '+rejected.join(', '));
      if(unknown.length)msgs.push('unknown token(s): '+unknown.join(', '));
      if(msgs.length)setStatus($('#themestatus'),'imported; '+msgs.join('; '),'ng');
      else setStatus($('#themestatus'),'imported','ok');
    }catch(e){
      setStatus($('#themestatus'),'import failed','ng');
    }
  };
  reader.readAsText(file);
}

// ---- one-time wiring (page load) ----
// The modal's static controls are wired exactly once; only the generated form inputs are rebuilt
// (and re-listened) per open, so reopening the editor never stacks duplicate button listeners.
$('#opentheme').addEventListener('click',()=>{initThemeEditor();$('#thememodal').hidden=false;});
$('#themeclose').addEventListener('click',()=>closeModal($('#thememodal')));
$('#themesave-local').addEventListener('click',saveThemeLocal);
$('#themeexport').addEventListener('click',exportTheme);
$('#themeimport-input').addEventListener('change',importThemeFile);
$('#themeimport').addEventListener('click',()=>$('#themeimport-input').click());
// The "Upload to Server" button ships hidden; reveal + wire it only when the instance was started
// with --themes (there's a directory to write into), signaled by the server-set writable flag.
if(window.__bajutsuThemesWritable){$('#themesave-upload').hidden=false;$('#themesave-upload').addEventListener('click',uploadTheme);}
