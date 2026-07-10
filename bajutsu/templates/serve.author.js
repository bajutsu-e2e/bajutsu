// serve.author.js — cross-panel layout wiring (platform sync, codegen, tiling), the Author tab,
// and the boot sequence. A serve.*.js section file (BE-0202); see serve.core.js's header for the
// concatenation contract. Loads last, so the trailing init calls run once every section is defined.

// Device UI is platform-specific: iOS controls (simulators, device pickers, erase, alert-dismiss)
// show only for an iOS backend, web controls (the headed/show-browser toggle) only for web. The
// backend is fixed per app by config (no UI override), so each panel follows the selected app's
// backend (data-backend) and shows only that platform's controls. Applies to Record/Replay/Crawl.
function isIosBackend(v){v=(v||'').trim().toLowerCase();return v===''||v==='idb'||v==='ios'||v==='xcuitest';}
function isAndroidBackend(v){v=(v||'').trim().toLowerCase();return v==='adb'||v==='android'||v==='uiautomator';}
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

// Readiness panels (BE-0148): the Replay and Record forms each check the selected target.
wireDoctor({btn:'#dr-check',status:'#dr-status',badge:'#dr-grade',checks:'#dr-checks',findings:'#dr-findings'},
  ()=>$('#target').value);
wireDoctor({btn:'#recdr-check',status:'#recdr-status',badge:'#recdr-grade',checks:'#recdr-checks',findings:'#recdr-findings'},
  ()=>$('#rec-target').value);

// Shared codegen wiring for a view (BE-0137): an emit selector synced to the target's backend, a
// Generate button that POSTs the selected scenario to /api/codegen, and a result panel with copy /
// download. The Author and Replay views both call this — one endpoint, one client behaviour. `ids`
// names the view's elements, `getScenario` returns the scenario path (or "" when none is selected).
// Returns {sync, reset} so the caller can re-pick the emit and drop a stale result on its own events.
function makeCodegen(ids,targetSel,getScenario){
  let result=null;
  const setStat=(msg,cls)=>{const s=$(ids.status);if(s){s.textContent=msg;s.className='status'+(cls?' '+cls:'');}};
  function reset(){$(ids.panel).hidden=true;result=null;}
  function sync(){const b=appBackend(targetSel);$(ids.emit).innerHTML=isIosBackend(b)
    ?'<option value="xcuitest">XCUITest</option>'
    :isAndroidBackend(b)?'<option value="uiautomator">UI Automator</option>'
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
    {id:'view-record',def:{d:'row',k:['controls',{d:'col',k:['log','yaml'],s:[1,1]}],s:[1,2]},sel:{controls:'.left',log:'.rec-stack .logpanel',yaml:'.rec-stack .yamlpanel',report:'.rec-stack .rec-report-panel'},optional:['report']},
    {id:'view-crawl',def:{d:'row',k:['controls','graph',{d:'col',k:['plan','console'],s:[1,1]}],s:[1,2,1]},sel:{controls:'.left',graph:'.crawl-graph-panel',plan:'.crawl-plan-panel',console:'.crawl-console-panel'}},
  ];
  const leaves=n=>typeof n==='string'?[n]:n.k.flatMap(leaves);
  // A tree is valid when its leaves are unique, all known panels, and include every REQUIRED panel.
  // Optional panels (e.g. Record's Run-result) may be absent — so hiding one keeps the tree valid.
  const valid=(t,keys,optional=[])=>{try{const l=leaves(t);const req=keys.filter(k=>!optional.includes(k));return new Set(l).size===l.length&&l.every(k=>keys.includes(k))&&req.every(k=>l.includes(k));}catch(e){return false;}};
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
  const rebuild=V=>{
    V.dvc=0;
    // Double-buffered pane reconstruction (BE-0191 unit 4): render() *moves* the live panel nodes into
    // the new root, so to animate the old layout out we keep a deep clone of it — a visual ghost held
    // absolute over the view — while the rebuilt root animates in, then drop the ghost. The ghost's
    // data-testid attributes are stripped so the primary selector ladder can't match it. What actually
    // keeps a run deterministic, though, is the reduced-motion guard: with reduced_motion=reduce forced
    // by the Playwright backend the ghost is never created and this collapses to a plain replaceChildren
    // with no duplicate DOM at all. It is also skipped when the theme opts out (--motion-view-leave:none).
    const old=V.view.querySelector(':scope>.tile-root');
    const leave=getComputedStyle(document.documentElement).getPropertyValue('--motion-view-leave').trim();
    const ghost=(old&&!prefersReducedMotion()&&leave&&leave!=='none')?old.cloneNode(true):null;
    const r=render(V,V.tree);r.classList.add('tile-root');
    V.view.replaceChildren(r);
    if(ghost){
      // Strip data-testid AND id so the ghost can't be reached by the selector ladder, by
      // document.getElementById, or by aria-* idrefs / <label for> while the leave animation plays.
      ghost.classList.add('is-leaving');ghost.removeAttribute('data-testid');ghost.removeAttribute('id');
      ghost.querySelectorAll('[data-testid],[id]').forEach(n=>{n.removeAttribute('data-testid');n.removeAttribute('id');});
      // Match on e.target so a descendant's animationend (e.g. a .running spinner bubbling up) doesn't
      // tear the listener down early — the root's own leave/enter animation is the one that ends it.
      const drop=e=>{if(e.target!==ghost)return;ghost.removeEventListener('animationend',drop);ghost.remove();};
      ghost.addEventListener('animationend',drop);
      V.view.appendChild(ghost);
      r.classList.add('is-entering');
      const done=e=>{if(e.target!==r)return;r.removeEventListener('animationend',done);r.classList.remove('is-entering');};
      r.addEventListener('animationend',done);
    }
    reflectSizes(V);
  };
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
    const optional=spec.optional||[];
    const V={spec,view,panel,keys,optional,id:spec.id.replace('view-',''),dvc:0,tree:(saved[spec.id]&&valid(saved[spec.id],keys,optional))?saved[spec.id]:spec.def};
    keys.forEach(k=>{
      const g=document.createElement('div');g.className='tile-grip';g.title='drag to move / swap';g.textContent='⠿';
      g.addEventListener('mousedown',e=>{e.preventDefault();pdrag={V,key:k};panel[k].classList.add('tile-dragging');document.body.classList.add('reordering-active');document.body.style.userSelect='none';document.body.style.cursor='grabbing';});
      panel[k].appendChild(g);
    });
    rebuild(V);views.push(V);
  });
  // Expose add/remove of Record's optional Run-result pane, so the run handlers can show it on Run
  // and its X can dismiss it — using the same tiling machinery (insert/remove leaf) as a drag would.
  const recV=views.find(v=>v.spec.id==='view-record');
  if(recV){
    const inTree=()=>leaves(recV.tree).includes('report');
    recReportShow=()=>{recV.panel.report.hidden=false;if(!inTree()){recV.tree=insertBeside(recV.tree,'yaml','report','bottom');rebuild(recV);}};
    recReportHide=()=>{if(inTree()){recV.tree=removeLeaf(recV.tree,'report')||recV.tree;rebuild(recV);}recV.panel.report.hidden=true;};
  }
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
        if(!valid(V.tree,V.keys,V.optional))V.tree=JSON.parse(bak);
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
    const files=target?await getJSON('/api/scenarios?target='+encodeURIComponent(target),[]):[];
    const opts=files.map(s=>
      (s.scenarios||[]).map(sc=>`<option value="${esc(s.path)}|${esc(sc.name)}">${esc(s.file)} — ${esc(sc.name)}</option>`)
    ).flat().join('');
    $('#au-scenario').innerHTML=opts||'<option value="">—</option>';
    await auLoadRuns();
  }

  async function auLoadRuns(){
    const runs=await getJSON('/api/runs',[]);
    const opts=runs.map(r=>{
      const label=r.id+' '+(r.ok?'✓':'✗');
      return `<option value="${esc(r.id)}">${esc(label)}</option>`;
    }).join('');
    $('#au-run').innerHTML=opts||'<option value="">—</option>';
  }

  function auSelectorYaml(sel){
    // JSON.stringify the id/label so a selector containing YAML-significant characters (a ':' or '#')
    // still emits a valid double-quoted scalar — a JSON string is a valid YAML double-quoted string.
    if(sel.id)return '{ id: '+JSON.stringify(sel.id)+' }';
    if(sel.label&&sel.index!=null)return '{ label: '+JSON.stringify(sel.label)+', index: '+sel.index+' }';
    if(sel.label)return '{ label: '+JSON.stringify(sel.label)+' }';
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
      const id=sel.id?'{ id: '+JSON.stringify(sel.id)+' }':JSON.stringify(sel);
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
      const id=sel.id?'{ id: '+JSON.stringify(sel.id)+' }':JSON.stringify(sel);
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
