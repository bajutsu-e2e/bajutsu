(function(){
  // ROOT is the document when this report is its own page, or the shadow root when the serve Web UI
  // embeds it inline (window.__bajutsuReportRoot). Queries and delegated listeners resolve against
  // ROOT so they work inside the embed (shadow-DOM event retargeting hides inner targets from the
  // host document) and unchanged on the standalone page (ROOT === document).
  var ROOT = (window.__bajutsuReportRoot && window.__bajutsuReportRoot.querySelectorAll)
    ? window.__bajutsuReportRoot : document;
  function esc(s){ return s.replace(/[&<>]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]; }); }
  ROOT.addEventListener('click', function(e){
    var t = e.target.closest('.tab'); if(!t) return;
    var scn = t.closest('.scn'), name = t.getAttribute('data-tab');
    scn.querySelectorAll('.tab').forEach(function(b){ b.classList.toggle('active', b===t); });
    scn.querySelectorAll('.panel').forEach(function(p){ p.classList.toggle('active', p.getAttribute('data-panel')===name); });
  });
  // A network request/response row expands its full settings table in the row below.
  ROOT.addEventListener('click', function(e){
    var row = e.target.closest('tr.xrow'); if(!row) return;
    var det = row.nextElementSibling;
    if(det && det.classList.contains('nxdetail')){
      if(det.hasAttribute('hidden')){ det.removeAttribute('hidden'); row.classList.add('open'); }
      else { det.setAttribute('hidden',''); row.classList.remove('open'); }
    }
  });
  // Visual-regression baseline approval. Only works when the report is served (so the
  // POST can reach the bajutsu serve endpoint); a report opened from disk hides the button.
  if (location.protocol === 'file:') {
    ROOT.querySelectorAll('.vapprove').forEach(function(b){ b.hidden = true; });
  }
  ROOT.addEventListener('click', function(e){
    var b = e.target.closest('.vapprove'); if(!b || b.disabled) return;
    var runId = (ROOT.querySelector('[data-run-id]') || document.body).getAttribute('data-run-id');
    var sid = b.getAttribute('data-sid'), baseline = b.getAttribute('data-baseline');
    if(!runId || !sid || !baseline) return;
    b.disabled = true; var label = b.textContent; b.textContent = 'Approving…';
    fetch('/api/approve', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({runId: runId, sid: sid, baseline: baseline})})
      .then(function(r){ return r.json(); })
      .then(function(d){
        if(d && d.ok){ b.textContent = 'Approved ✓'; b.classList.add('done'); }
        else { b.textContent = (d && d.error) ? ('Failed: '+d.error) : 'Failed'; b.disabled = false; }
      })
      .catch(function(){ b.textContent = label; b.disabled = false; });
  });
  // Visual-regression comparator: swipe / onion / mix-blend (+ the precomputed pixel diff).
  // The mode lives as a class on the widget; the range means "wipe position" (swipe) or
  // "actual opacity" (onion); blend/diff need no slider. The handle is draggable in swipe.
  function initComparator(c){
    var stage = c.querySelector('.vcmp-stage'),
        over = c.querySelector('.vcmp-over'),
        range = c.querySelector('.vcmp-range');
    function mode(){ var m = c.className.match(/mode-(\w+)/); return m ? m[1] : 'swipe'; }
    function setMode(m){
      c.className = 'vcmp mode-' + m;
      over.style.opacity = (m === 'onion') ? (range.value / 100) : '';
      range.style.display = (m === 'swipe' || m === 'onion') ? '' : 'none';
      if(m === 'swipe') c.style.setProperty('--p', range.value + '%');
    }
    c.querySelectorAll('.vcmp-mode').forEach(function(b){
      b.addEventListener('click', function(){
        c.querySelectorAll('.vcmp-mode').forEach(function(x){ x.classList.toggle('active', x === b); });
        setMode(b.getAttribute('data-mode'));
      });
    });
    range.addEventListener('input', function(){
      var m = mode();
      if(m === 'swipe') c.style.setProperty('--p', range.value + '%');
      else if(m === 'onion') over.style.opacity = range.value / 100;
    });
    function wipeTo(e){
      var r = stage.getBoundingClientRect();
      var p = Math.max(0, Math.min(100, (e.clientX - r.left) / r.width * 100));
      range.value = p; c.style.setProperty('--p', p + '%');
    }
    var dragging = false;
    stage.addEventListener('pointerdown', function(e){ if(mode() !== 'swipe') return; dragging = true; wipeTo(e); e.preventDefault(); });
    window.addEventListener('pointermove', function(e){ if(dragging) wipeTo(e); });
    window.addEventListener('pointerup', function(){ dragging = false; });
    setMode('swipe');
  }
  ROOT.querySelectorAll('.vcmp').forEach(initComparator);
  // Rich / YAML toggle within the merged Result tab.
  ROOT.addEventListener('click', function(e){
    var t = e.target.closest('.vt'); if(!t) return;
    var panel = t.closest('.panel'), view = t.getAttribute('data-view');
    panel.querySelectorAll('.vt').forEach(function(b){ b.classList.toggle('active', b===t); });
    panel.querySelectorAll('.view').forEach(function(v){
      v.classList.toggle('active', v.classList.contains('view-'+view));
    });
  });
  ROOT.addEventListener('input', function(e){
    if(!e.target.classList.contains('logfilter')) return;
    var panel = e.target.closest('.panel'), ql = e.target.value.toLowerCase(), n = 0;
    panel.querySelectorAll('.log .ln').forEach(function(l){
      var raw = l.getAttribute('data-raw');
      if(raw === null){ raw = l.textContent; l.setAttribute('data-raw', raw); }
      if(!ql){ l.textContent = raw; l.classList.remove('hide'); n++; return; }
      if(raw.toLowerCase().indexOf(ql) === -1){ l.classList.add('hide'); l.textContent = raw; return; }
      l.classList.remove('hide'); n++;
      // Rebuild the line with each match wrapped in <mark> (highlight).
      var html = '', low = raw.toLowerCase(), i = 0, j;
      while((j = low.indexOf(ql, i)) !== -1){
        html += esc(raw.slice(i, j)) + '<mark>' + esc(raw.slice(j, j + ql.length)) + '</mark>';
        i = j + ql.length;
      }
      l.innerHTML = html + esc(raw.slice(i));
    });
    var cnt = panel.querySelector('.logcount'); if(cnt) cnt.textContent = n + ' lines';
  });
  window.onlyFailures = function(cb){
    ROOT.querySelectorAll('details.scn').forEach(function(d){
      d.style.display = (cb.checked && d.getAttribute('data-ok')==='true') ? 'none' : '';
    });
  };
  window.toggleAll = function(open){
    ROOT.querySelectorAll('details.scn').forEach(function(d){ d.open = open; });
  };
  // Element viewer: clicking a step's screenshot (or its "tree" button) opens that step's
  // captured accessibility elements in an overlay — embedded inline, so it works offline
  // (no new tab). The step's own info is shown above the element table; ◀ / ▶ (and the
  // ← / → keys) walk the steps of the *current scenario only*, wrapping around at the ends.
  var tv = ROOT.getElementById('tv');
  var tvBody = tv && tv.querySelector('.tv-body');
  var tvStep = tv && tv.querySelector('.tv-step');
  var tvInput = tv && tv.querySelector('.tvfilter');
  var tvCount = tv && tv.querySelector('.tvcount');
  // The step "view" cells with embedded element data within one scenario, in document order.
  function tvScopeFor(host){
    var scn = host.closest('details.scn') || ROOT;
    return Array.prototype.slice.call(scn.querySelectorAll('td.ev')).filter(function(td){
      return td.querySelector('template.treedata');
    });
  }
  var tvScope = [];   // hosts in the currently shown scenario — the loop the arrows walk
  var tvIndex = -1;
  // Screen extent (points) of the currently shown step, used to map an element's frame
  // onto the screenshot. Seeded from the element bounding box, refined from the shot's
  // real pixel size (see tvOpen) so a long scrolling list doesn't distort the mapping.
  var tvScreenW = NaN, tvScreenH = NaN;
  function tvHighlight(tr){
    var hl = tvBody && tvBody.querySelector('.tv-hl'); if(!hl) return;
    var x = parseFloat(tr.getAttribute('data-x')), y = parseFloat(tr.getAttribute('data-y'));
    var w = parseFloat(tr.getAttribute('data-w')), h = parseFloat(tr.getAttribute('data-h'));
    if(!(tvScreenW > 0) || !(tvScreenH > 0) || isNaN(x) || isNaN(y)){ hl.hidden = true; return; }
    hl.style.left = (x / tvScreenW * 100) + '%';
    hl.style.top = (y / tvScreenH * 100) + '%';
    hl.style.width = Math.max(0, w / tvScreenW * 100) + '%';
    hl.style.height = Math.max(0, h / tvScreenH * 100) + '%';
    hl.hidden = false;
  }
  function tvUnhighlight(){ var hl = tvBody && tvBody.querySelector('.tv-hl'); if(hl) hl.hidden = true; }
  function tvFilter(q){
    if(!tvBody) return;
    tvUnhighlight();
    q = q.toLowerCase(); var n = 0;
    tvBody.querySelectorAll('tbody tr').forEach(function(r){
      var hit = !q || r.textContent.toLowerCase().indexOf(q) !== -1;
      r.style.display = hit ? '' : 'none'; if(hit) n++;
    });
    if(tvCount) tvCount.textContent = n + (n === 1 ? ' element' : ' elements');
  }
  function tvClose(){ if(tv){ tv.classList.remove('open'); if(tvBody) tvBody.innerHTML = ''; tvScope = []; tvIndex = -1; } }
  // Step by ±1 within the scenario, wrapping at the ends (no-op for a single-step scenario).
  function tvGo(delta){
    if(tvScope.length < 2) return;
    tvOpen(tvScope[(tvIndex + delta + tvScope.length) % tvScope.length]);
  }
  // The step-info band above the element table: step number, result/action badges and
  // the tokenized detail, cloned from the step's own row.
  function tvBuildStep(host){
    if(!tvStep) return;
    tvStep.innerHTML = '';
    var row = host.closest('tr.srow');
    if(!row){ tvStep.hidden = true; return; }
    var cells = row.children;
    var num = cells[0] ? cells[0].textContent.trim() : '';
    if(num){ var n = document.createElement('span'); n.className = 'tv-stepnum'; n.textContent = 'step ' + num; tvStep.appendChild(n); }
    var rb = cells[1] && cells[1].querySelector('.exst'); if(rb) tvStep.appendChild(rb.cloneNode(true));
    var ab = cells[2] && cells[2].querySelector('.act'); if(ab) tvStep.appendChild(ab.cloneNode(true));
    if(cells[3]){ var d = document.createElement('span'); d.className = 'tv-stepdesc'; d.innerHTML = cells[3].innerHTML; tvStep.appendChild(d); }
    // The `at` cell can hold two buttons (start + end, no separator between them) for a step
    // with a visible duration — take the start button's own text so the band shows one instant,
    // not "1.5s→2.6s" run together. Bare text (network/skip rows) still falls through as-is.
    var atCell = cells[4], atJump = atCell && atCell.querySelector('.stepjump');
    var at = atJump ? atJump.textContent.trim() : (atCell ? atCell.textContent.trim() : '');
    if(at){ var a = document.createElement('span'); a.className = 'tv-stepat muted'; a.textContent = at; tvStep.appendChild(a); }
    tvStep.hidden = false;
  }
  function tvOpen(host){
    if(!host || !tv || !tvBody) return;
    var tpl = host.querySelector('template.treedata'); if(!tpl) return;
    tvScope = tvScopeFor(host);
    tvIndex = tvScope.indexOf(host);
    tvBuildStep(host);
    tvBody.innerHTML = '';
    // Show the step's screenshot beside its elements so the two can be read together;
    // hovering an element row highlights its frame on the shot (tv-hl overlay). The screenshot
    // stays fixed; only the element list scrolls, with the ◀ / ▶ controls pinned beneath it.
    var shot = host.querySelector('img.shot'), imEl = null;
    var sd = document.createElement('div'); sd.className = 'tv-shot';
    if(shot){
      var frame = document.createElement('div'); frame.className = 'tv-shotframe';
      imEl = document.createElement('img'); imEl.alt = 'step screenshot';
      imEl.src = shot.getAttribute('src');
      imEl.style.cursor = 'zoom-in';  // click the viewer's screenshot to enlarge it full-screen
      imEl.addEventListener('click', function(e){ e.stopPropagation(); openImg(imEl.getAttribute('src')); });
      var hl = document.createElement('div'); hl.className = 'tv-hl'; hl.hidden = true;
      frame.appendChild(imEl); frame.appendChild(hl); sd.appendChild(frame);
    }
    tvBody.appendChild(sd);
    // Element list (scrolls internally) with the step-nav bar pinned below it.
    var main = document.createElement('div'); main.className = 'tv-main';
    var tree = document.createElement('div'); tree.className = 'tv-tree';
    tree.innerHTML = tpl.innerHTML;
    tree.addEventListener('mouseover', function(e){ var tr = e.target.closest('tr.tvrow'); if(tr) tvHighlight(tr); });
    tree.addEventListener('mouseleave', tvUnhighlight);
    main.appendChild(tree);
    var nav = document.createElement('div'); nav.className = 'tv-treenav';
    function navBtn(cls, glyph, label, delta){
      var btn = document.createElement('button'); btn.type = 'button';
      btn.className = 'tv-nav ' + cls; btn.textContent = glyph;
      btn.setAttribute('aria-label', label); btn.title = label + ' (' + (delta < 0 ? '←' : '→') + ')';
      btn.disabled = tvScope.length < 2;  // nothing to loop through in a single-step scenario
      btn.addEventListener('click', function(){ tvGo(delta); });
      return btn;
    }
    nav.appendChild(navBtn('tv-prev', '◀', 'previous step', -1));
    // current position / total steps in this scenario, between the arrows
    var pos = document.createElement('span'); pos.className = 'tv-pos';
    pos.textContent = (tvIndex + 1) + '/' + tvScope.length;
    nav.appendChild(pos);
    nav.appendChild(navBtn('tv-next', '▶', 'next step', 1));
    main.appendChild(nav);
    tvBody.appendChild(main);
    // Seed the screen extent from the element bounding box, then refine: derive the
    // device scale from the width (which rarely scrolls) and recompute the height.
    var tbl = tree.querySelector('.tvtbl');
    tvScreenW = tbl ? parseFloat(tbl.getAttribute('data-sw')) : NaN;
    tvScreenH = tbl ? parseFloat(tbl.getAttribute('data-sh')) : NaN;
    if(imEl){
      var refine = function(){
        if(imEl.naturalWidth > 0 && tvScreenW > 0){
          var scale = Math.max(1, Math.round(imEl.naturalWidth / tvScreenW));
          tvScreenW = imEl.naturalWidth / scale;
          tvScreenH = imEl.naturalHeight / scale;
        }
      };
      if(imEl.complete) refine(); else imEl.addEventListener('load', refine);
    }
    if(tvInput) tvInput.value = '';
    tvFilter('');
    tree.scrollTop = 0;
    tv.classList.add('open');
  }
  // The "tree" button or a step screenshot opens the element viewer (the step's screenshot beside
  // its elements, with ◀ / ▶ and ← / → to walk the scenario's steps).
  ROOT.addEventListener('click', function(e){
    var b = e.target.closest('.treebtn') || e.target.closest('.shot'); if(!b) return;
    tvOpen(b.closest('td.ev') || b.parentNode);
  });
  if(tv){
    tv.addEventListener('click', function(e){ if(e.target === tv) tvClose(); });  // backdrop only
    var tvX = tv.querySelector('.tv-close'); if(tvX) tvX.addEventListener('click', tvClose);
    if(tvInput) tvInput.addEventListener('input', function(){ tvFilter(this.value); });
    document.addEventListener('keydown', function(e){
      if(!tv.classList.contains('open')) return;
      if(imgz && imgz.classList.contains('open')) return;  // the enlarged screenshot handles its own keys
      if(e.key === 'Escape'){ tvClose(); return; }
      // While typing in the filter, let ← / → move the text cursor instead of navigating.
      if(ROOT.activeElement === tvInput && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) return;
      if(e.key === 'ArrowLeft'){ e.preventDefault(); tvGo(-1); }
      else if(e.key === 'ArrowRight'){ e.preventDefault(); tvGo(1); }
    });
  }
  // Inside the element viewer, clicking the screenshot enlarges it full-screen (a plain lightbox).
  // ← / → walk the scenario's steps' screenshots: they drive the viewer underneath (tvGo) and the
  // lightbox mirrors its screenshot, so the two stay in sync and closing it lands on that step.
  // The backdrop or Esc closes it.
  var imgz = ROOT.getElementById('imgz'), imgzImg = imgz && imgz.querySelector('img');
  function openImg(src){ if(imgz && imgzImg && src){ imgzImg.src = src; imgz.classList.add('open'); } }
  function closeImg(){ if(imgz){ imgz.classList.remove('open'); if(imgzImg) imgzImg.removeAttribute('src'); } }
  function imgzSync(){ var im = ROOT.querySelector('#tv .tv-shot img'); if(im && imgzImg) imgzImg.src = im.getAttribute('src'); }
  if(imgz){
    imgz.addEventListener('click', closeImg);
    document.addEventListener('keydown', function(e){
      if(!imgz.classList.contains('open')) return;
      if(e.key === 'Escape'){ closeImg(); return; }
      if(e.key === 'ArrowLeft'){ e.preventDefault(); tvGo(-1); imgzSync(); }
      else if(e.key === 'ArrowRight'){ e.preventDefault(); tvGo(1); imgzSync(); }
    });
  }
  // Custom player chrome: a slim bar below the recording (play/pause, scrubber, time),
  // so the controls never overlay the video frame the way the native HTML5 controls do.
  function fmtT(t){
    if(!isFinite(t) || t < 0) t = 0;
    var m = Math.floor(t / 60), s = Math.floor(t % 60);
    return m + ':' + (s < 10 ? '0' : '') + s;
  }
  // Bound the Result view to the recording's height so the steps list scrolls within its
  // own container (instead of pushing the page) and ends level with the player; the
  // expectations footer then pins to the bottom of that bound. Cleared when there is no
  // recording or the card is collapsed (so the layout falls back to its natural flow).
  function syncResultHeight(scn){
    // `.players` (BE-0428) wraps every video a multi-target scenario shows, stacked — bound to its
    // combined height, not just the first one's, so the steps list doesn't overflow past a second
    // or third player underneath it.
    var player = scn.querySelector('.players') || scn.querySelector('.player');
    var wrap = scn.querySelector('.rich-wrap');
    if(!wrap) return;
    if(!player){ wrap.style.maxHeight = ''; return; }
    var pr = player.getBoundingClientRect();
    if(pr.height < 80){ wrap.style.maxHeight = ''; return; }   // collapsed / metadata not loaded yet
    var h = pr.bottom - wrap.getBoundingClientRect().top;
    wrap.style.maxHeight = h > 120 ? h + 'px' : '';
  }
  function syncAllHeights(){ ROOT.querySelectorAll('.scn').forEach(syncResultHeight); }
  window.addEventListener('resize', syncAllHeights);
  ROOT.querySelectorAll('details.scn').forEach(function(d){
    d.addEventListener('toggle', function(){ if(d.open) syncResultHeight(d); });
  });
  // Every scenario's videos share one group here (BE-0428): keyed by the `.scn` element, so a
  // click, seek, or play/pause on any one of a multi-target scenario's recordings can find its
  // siblings and follow — the group is looked up live inside each player's own listeners below,
  // not captured at setup time, so it already holds every player by the time any of them fires.
  var scnGroups = new Map();
  ROOT.querySelectorAll('.player').forEach(function(p){
    var v = p.querySelector('video'), btn = p.querySelector('.vplay');
    var seek = p.querySelector('.vseek'), time = p.querySelector('.vtime');
    var marks = p.querySelector('.vmarks'), segs = p.querySelector('.vsegs'), scn = p.closest('.scn');
    var knob = p.querySelector('.vknob');
    // This player's own declared target ("" for the primary/single-target case) and how many
    // seconds ahead of the *first* video in this scenario its own recording started — never the
    // raw wall-clock instant itself (the server deliberately never sends one; see `_videos` in
    // panels.py). `syncSiblings` below turns that small delta into "the same moment, on a sibling
    // video's own timeline" (BE-0428).
    var target = p.getAttribute('data-target') || '';
    var offset = parseFloat(p.getAttribute('data-offset')); if(isNaN(offset)) offset = 0;
    // This player's own rows: every `tr.srow[data-t]` naming the same target, primary included (a
    // single-target scenario's rows all carry `data-target=""`, matching its one player). Scoping
    // ticks/bands/highlighting to these — instead of every row in the scenario — is what keeps a
    // second target's steps off the first target's scrubber and vice versa.
    function myRows(){
      return Array.prototype.slice.call(scn ? scn.querySelectorAll('tr.srow[data-t]') : []).filter(
        function(r){ return (r.getAttribute('data-target') || '') === target; }
      );
    }
    if(scn){
      var group = scnGroups.get(scn);
      if(!group){ group = []; scnGroups.set(scn, group); }
      group.push({v: v, offset: offset});
    }
    function moveKnob(){
      if(!knob || !isFinite(v.duration) || v.duration <= 0) return;
      knob.style.left = Math.max(0, Math.min(100, v.currentTime / v.duration * 100)) + '%';
    }
    if(!v || !btn || !seek || !time) return;
    function paint(){ btn.textContent = v.paused ? '▶' : '❚❚'; }
    function clock(){ time.textContent = fmtT(v.currentTime) + ' / ' + fmtT(v.duration); }
    // Carry this video's play/pause state and playhead onto every sibling recording in the same
    // scenario (BE-0428): every player's own `offset` (server-computed, BE-0428's `_videos`) is
    // relative to the same first video, so subtracting this one's own offset and adding a
    // sibling's converts "this instant" into "the same moment, on the sibling's own timeline" —
    // never by way of a raw wall-clock instant (see the `offset` field's own comment above).
    //
    // `v._synced` guards against exactly the feedback loop that clamping otherwise creates: two
    // recordings rarely span the same wall-clock range (one target's steps often finish before the
    // other's start), so a moment past a sibling's own end/start clamps to its boundary — a value
    // that does *not* map back to the instant that produced it. Without the guard, that sibling's
    // own `seeked` would call this function again, "correct" *this* video from the very position
    // the viewer just set, and the two would fight over it. Marking a sibling `_synced` right before
    // giving it a new position or play state means its own resulting event finds the flag, clears
    // it, and returns without propagating — the sibling's echo is absorbed, not relayed.
    function syncSiblings(){
      if(v._synced){ v._synced = false; return; }
      if(!scn) return;
      var group = scnGroups.get(scn);
      if(!group || group.length < 2) return;
      var refTime = v.currentTime - offset;
      var playing = !v.paused;
      group.forEach(function(sib){
        if(sib.v === v) return;
        var t = refTime + sib.offset;
        t = isFinite(sib.v.duration) && sib.v.duration > 0 ? Math.max(0, Math.min(sib.v.duration, t)) : Math.max(0, t);
        if(Math.abs(sib.v.currentTime - t) > 0.08){ sib.v._synced = true; sib.v.currentTime = t; }
        if(playing && sib.v.paused){ sib.v._synced = true; sib.v.play().catch(function(){}); }
        else if(!playing && !sib.v.paused){ sib.v._synced = true; sib.v.pause(); }
      });
    }
    function bands(){
      // The `before` band runs from the recording's start to the first main step (or, lacking
      // one, its own last step); the `after` band runs from its first step to the recording's
      // end — an approximation (steps mark starts, not phase boundaries) good enough to show
      // roughly where setup/teardown sit relative to the scenario's own steps.
      if(!segs || !scn || !isFinite(v.duration) || v.duration <= 0) return;
      var before = [], main = [], after = [];
      myRows().forEach(function(r){
        var t = parseFloat(r.getAttribute('data-t')); if(isNaN(t)) return;
        var phase = r.getAttribute('data-phase');
        (phase === 'before' ? before : phase === 'after' ? after : main).push(t);
      });
      var html = '';
      // Decorative only (pointer-events:none, see report.css) — no title, since a band is never
      // a hit-test target for the browser to hang a tooltip on.
      function band(cls, from, to){
        if(to == null || from == null || to <= from) return;
        var l = Math.max(0, Math.min(100, from / v.duration * 100));
        var w = Math.max(0, Math.min(100 - l, (to - from) / v.duration * 100));
        html += '<span class="vseg ' + cls + '" style="left:' + l.toFixed(3) + '%;width:' + w.toFixed(3) + '%"></span>';
      }
      if(before.length) band('vseg-before', 0, main.length ? main[0] : before[before.length - 1]);
      if(after.length) band('vseg-after', after[0], v.duration);
      segs.innerHTML = html;
    }
    function ticks(){
      // One mark per executed step, placed at its recording offset (data-t seconds). A step
      // whose end reads differently from its start (data-t-end, the same threshold rows.py used
      // to decide whether to show a separate jump button) draws as a short bar spanning the two
      // instead of a single line, so the start/end an action took is visible on the scrubber
      // itself, not only in the step row's own jump buttons. Each carries a hover bubble (step
      // number + time, or time range) and seeks to the start on click.
      if(!marks || !scn || !isFinite(v.duration) || v.duration <= 0) return;
      var html = '';
      myRows().forEach(function(r){
        var t = parseFloat(r.getAttribute('data-t')); if(isNaN(t)) return;
        var endAttr = r.getAttribute('data-t-end');
        var tEnd = endAttr !== null ? parseFloat(endAttr) : NaN;
        var pct = Math.max(0, Math.min(100, t / v.duration * 100));
        var td = r.querySelector('td'), num = td ? td.textContent.trim() : '';
        if(!isNaN(tEnd) && tEnd > t){
          var pctEnd = Math.max(0, Math.min(100, tEnd / v.duration * 100));
          var w = Math.max(0.5, pctEnd - pct);   // floor so a short step's bar stays visible/clickable
          html += '<span class="vmark vmark-range" data-t="' + t + '" data-t-end="' + tEnd
            + '" style="left:' + pct.toFixed(3) + '%;width:' + w.toFixed(3) + '%">'
            + '<span class="vmtip">Step ' + esc(num) + ' · ' + fmtT(t) + '–' + fmtT(tEnd) + '</span></span>';
        } else {
          html += '<span class="vmark" data-t="' + t + '" style="left:' + pct.toFixed(3) + '%">'
            + '<span class="vmtip">Step ' + esc(num) + ' · ' + fmtT(t) + '</span></span>';
        }
      });
      marks.innerHTML = html;
    }
    function meta(){ if(isFinite(v.duration)) seek.max = v.duration; clock(); ticks(); bands(); moveKnob(); if(scn) syncResultHeight(scn); }
    function toggle(){ if(v.paused) v.play(); else v.pause(); }
    btn.addEventListener('click', toggle);
    v.addEventListener('click', toggle);   // clicking the frame itself plays/pauses
    if(marks){
      // A range bar can span a meaningful chunk of the track, and it captures the pointer (it
      // needs to, to be clickable) — so without this, starting a drag from on top of one would
      // do nothing instead of scrubbing, unlike everywhere else on the bar. `pointerdown` here
      // takes over the drag ourselves: it tracks the pointer across the *whole* seekbar (the same
      // 7px inset `.vmarks`/`.vsegs`/`.vknobwrap` all share) until release, exactly like dragging
      // the native thumb would. A plain click (no movement) leaves `moved` false and falls
      // through to the precise per-mark seek below instead.
      var moved = false;
      function timeFromClientX(clientX){
        // `marks`' own box *is* the inset track (report.css gives .vmarks/.vsegs/.vknobwrap the
        // same left:7px;right:7px) — read that geometry instead of re-deriving it from the 7px
        // literal + .vseekwrap, which only happens to match today because the input is its sole
        // laid-out child.
        var rect = marks.getBoundingClientRect();
        var usable = Math.max(1, rect.width);
        var frac = Math.max(0, Math.min(1, (clientX - rect.left) / usable));
        return frac * v.duration;
      }
      function applyTime(t){
        v.currentTime = t;
        if(!seek.matches(':active')) seek.value = t;
        moveKnob();
        syncSiblings();
      }
      marks.addEventListener('pointerdown', function(e){
        var m = e.target.closest('.vmark');
        if(!m || e.button !== 0 || !isFinite(v.duration) || v.duration <= 0) return;
        e.preventDefault();   // no text selection while dragging — focus is restored explicitly below
        // A mark sits on top of (and so intercepts clicks meant for) the native input beneath —
        // needed for it to be clickable/draggable at all — which also means that input never
        // gets focus this way, and arrow-key stepping after interacting with a mark would
        // otherwise be unreachable. Focus it ourselves to keep that native affordance working.
        seek.focus();
        moved = false;
        var startX = e.clientX;
        applyTime(timeFromClientX(e.clientX));
        function onMove(ev){
          if(Math.abs(ev.clientX - startX) > 2) moved = true;
          applyTime(timeFromClientX(ev.clientX));
        }
        // `pointerup` isn't guaranteed: the browser can take over a touch/pen gesture mid-drag
        // (this page scrolls, so a mostly-vertical one started on a mark ends in `pointercancel`
        // instead) and a mouse released outside the window drops it too. Either dangling listener
        // left `onMove` running forever, scrubbing on every later pointer move on the page.
        function onUp(){
          document.removeEventListener('pointermove', onMove);
          document.removeEventListener('pointerup', onUp);
          document.removeEventListener('pointercancel', onUp);
        }
        document.addEventListener('pointermove', onMove);
        document.addEventListener('pointerup', onUp);
        document.addEventListener('pointercancel', onUp);
      });
      marks.addEventListener('click', function(e){
        if(moved){ moved = false; return; }   // this click just ended a drag; already seeked
        // A point tick has one instant to seek to. A range bar has two — its exact start and end
        // — plus everything between, so a fixed-width hit zone at each edge (not a fraction of
        // the bar's own width, which would shrink to nothing on a short step) snaps a click there
        // to the exact boundary; a click elsewhere in the bar interpolates to the time under it,
        // rather than always snapping to the start regardless of where the bar itself was clicked.
        var m = e.target.closest('.vmark'); if(!m) return;
        var t = parseFloat(m.getAttribute('data-t')); if(isNaN(t)) return;
        var endAttr = m.getAttribute('data-t-end');
        var tEnd = endAttr !== null ? parseFloat(endAttr) : NaN;
        if(!isNaN(tEnd) && tEnd > t){
          var rect = m.getBoundingClientRect(), x = e.clientX - rect.left;
          var EDGE = Math.min(6, rect.width / 2);   // a sub-12px bar splits at its midpoint instead
          if(x <= EDGE){ /* exact start */ }
          else if(x >= rect.width - EDGE){ t = tEnd; }
          else{
            var span = Math.max(1, rect.width - 2 * EDGE);
            var frac = Math.max(0, Math.min(1, (x - EDGE) / span));
            t = t + frac * (tEnd - t);
          }
        }
        applyTime(t);
      });
    }
    v.addEventListener('play', paint);
    v.addEventListener('pause', paint);
    // BE-0428: play and seek propagate to every sibling recording in the same scenario, once, at
    // the moment they happen — never on every `timeupdate` tick. Two recordings are almost never
    // the same length (one target's steps often finish well before the other's), so pinning them
    // to march in lockstep the whole time would mean whichever is shorter keeps yanking the other
    // back to a clamped boundary for as long as it plays, and would then stop it outright at its
    // own end (see the `pause` handler below) — a sibling that starts in the right place is left to
    // play on at its own native rate after that, exactly like an ordinary unsynced video would.
    v.addEventListener('play', syncSiblings);
    v.addEventListener('pause', function(){
      // The shorter recording reaches `pause` via its own natural end (`v.ended`) before a longer
      // sibling does — that must not stop the sibling early. Only a real pause (the button, or the
      // frame click, both call `v.pause()` directly with `ended` still false) propagates.
      if(v.ended) return;
      syncSiblings();
    });
    v.addEventListener('seeked', syncSiblings);
    v.addEventListener('loadedmetadata', meta);
    v.addEventListener('timeupdate', function(){
      if(!seek.matches(':active')) seek.value = v.currentTime;   // don't fight an active drag
      clock();
      moveKnob();
    });
    seek.addEventListener('input', function(){ v.currentTime = parseFloat(seek.value); moveKnob(); syncSiblings(); });
    paint(); meta();   // handle the case where metadata is already cached (event won't fire)
  });
  // Sync each scenario's recording with its step rows: click a step to seek there (or
  // click its screenshot to open the element viewer), and highlight the step whose time
  // window the playhead is in — scrolling it into view within the bounded steps list.
  function scrollIntoBox(box, row){
    if(!box || !row) return;
    var cr = box.getBoundingClientRect(), rr = row.getBoundingClientRect();
    if(cr.height <= 0) return;   // result view not visible (another tab is active)
    if(rr.top < cr.top) box.scrollTop -= (cr.top - rr.top) + 8;
    else if(rr.bottom > cr.bottom) box.scrollTop += (rr.bottom - cr.bottom) + 8;
  }
  ROOT.querySelectorAll('.scn').forEach(function(scn){
    var box = scn.querySelector('.rich-scroll');
    // One pass per player rather than per scenario (BE-0428): each recording only ever seeks
    // itself and only ever highlights its own target's rows — a click on a web step never moves
    // the iOS recording's playhead, only (via `syncSiblings` above) follows it there. A row's own
    // target ("" included) is what ties it to the one player it belongs to.
    scn.querySelectorAll('.player').forEach(function(p){
      var v = p.querySelector('video'); if(!v) return;
      var target = p.getAttribute('data-target') || '';
      var rows = Array.prototype.slice.call(scn.querySelectorAll('tr.srow[data-target]')).filter(
        function(r){ return r.getAttribute('data-target') === target; }
      );
      if(!rows.length) return;
      var lastCur = null;
      rows.forEach(function(r){
        r.addEventListener('click', function(e){
          // links / tree button / screenshot / the step's own jump buttons handled elsewhere
          // (a jump button seeks to its own instant instead of the row's default start).
          if(e.target.closest('a') || e.target.closest('.treebtn') || e.target.closest('.shot') || e.target.closest('.stepjump')) return;
          var t = parseFloat(r.getAttribute('data-t'));
          // Seek only: keep playing if already playing, stay paused if paused.
          if(!isNaN(t)){ v.currentTime = t; }
        });
        // A step's own start/end jump buttons (its `before`/`after` moment) — stop the click from
        // also firing the row handler above, which would otherwise re-seek to the row's start right
        // after the end button just seeked past it. Scoped to this player's own rows, same as the
        // row click above: a jump button always belongs to the row it is drawn inside of.
        r.querySelectorAll('.stepjump').forEach(function(btn){
          btn.addEventListener('click', function(e){
            e.stopPropagation();
            var t = parseFloat(btn.getAttribute('data-t'));
            if(!isNaN(t)){ v.currentTime = t; }
          });
        });
      });
      v.addEventListener('timeupdate', function(){
        var ct = v.currentTime + 0.001, cur = null;
        for(var i=0;i<rows.length;i++){
          var t = parseFloat(rows[i].getAttribute('data-t'));
          if(!isNaN(t) && t <= ct) cur = rows[i];
        }
        rows.forEach(function(r){ r.classList.toggle('playing', r===cur); });
        if(cur !== lastCur){ lastCur = cur; if(cur) scrollIntoBox(box, cur); }
      });
    });
  });
})();
