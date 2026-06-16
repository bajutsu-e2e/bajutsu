(function(){
  function esc(s){ return s.replace(/[&<>]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]; }); }
  document.addEventListener('click', function(e){
    var t = e.target.closest('.tab'); if(!t) return;
    var scn = t.closest('.scn'), name = t.getAttribute('data-tab');
    scn.querySelectorAll('.tab').forEach(function(b){ b.classList.toggle('active', b===t); });
    scn.querySelectorAll('.panel').forEach(function(p){ p.classList.toggle('active', p.getAttribute('data-panel')===name); });
  });
  // A network request/response row expands its full settings table in the row below.
  document.addEventListener('click', function(e){
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
    document.querySelectorAll('.vapprove').forEach(function(b){ b.hidden = true; });
  }
  document.addEventListener('click', function(e){
    var b = e.target.closest('.vapprove'); if(!b || b.disabled) return;
    var runId = document.body.getAttribute('data-run-id');
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
  document.querySelectorAll('.vcmp').forEach(initComparator);
  // Rich / YAML toggle within the merged Result tab.
  document.addEventListener('click', function(e){
    var t = e.target.closest('.vt'); if(!t) return;
    var panel = t.closest('.panel'), view = t.getAttribute('data-view');
    panel.querySelectorAll('.vt').forEach(function(b){ b.classList.toggle('active', b===t); });
    panel.querySelectorAll('.view').forEach(function(v){
      v.classList.toggle('active', v.classList.contains('view-'+view));
    });
  });
  document.addEventListener('input', function(e){
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
    document.querySelectorAll('details.scn').forEach(function(d){
      d.style.display = (cb.checked && d.getAttribute('data-ok')==='true') ? 'none' : '';
    });
  };
  window.toggleAll = function(open){
    document.querySelectorAll('details.scn').forEach(function(d){ d.open = open; });
  };
  // Element viewer: clicking a step's screenshot (or its "tree" button) opens that step's
  // captured accessibility elements in an overlay — embedded inline, so it works offline
  // (no new tab). The step's own info is shown above the element table, and ← / → walk
  // through every step's elements across the run.
  var tv = document.getElementById('tv');
  var tvBody = tv && tv.querySelector('.tv-body');
  var tvStep = tv && tv.querySelector('.tv-step');
  var tvInput = tv && tv.querySelector('.tvfilter');
  var tvCount = tv && tv.querySelector('.tvcount');
  var tvPrev = tv && tv.querySelector('.tv-prev');
  var tvNext = tv && tv.querySelector('.tv-next');
  // Every step "view" cell carrying embedded element data, in document order — the walk
  // order for the ← / → keys.
  var tvHosts = Array.prototype.slice.call(document.querySelectorAll('td.ev')).filter(function(td){
    return td.querySelector('template.treedata');
  });
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
  function tvClose(){ if(tv){ tv.classList.remove('open'); if(tvBody) tvBody.innerHTML = ''; tvIndex = -1; } }
  function tvCanGo(delta){ var i = tvIndex + delta; return tvIndex >= 0 && i >= 0 && i < tvHosts.length; }
  function tvGo(delta){ if(tvCanGo(delta)) tvOpen(tvHosts[tvIndex + delta]); }
  function tvUpdateNav(){
    if(tvPrev) tvPrev.disabled = !tvCanGo(-1);
    if(tvNext) tvNext.disabled = !tvCanGo(1);
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
    var at = cells[4] ? cells[4].textContent.trim() : '';
    if(at){ var a = document.createElement('span'); a.className = 'tv-stepat muted'; a.textContent = at; tvStep.appendChild(a); }
    tvStep.hidden = false;
  }
  function tvOpen(host){
    if(!host || !tv || !tvBody) return;
    var tpl = host.querySelector('template.treedata'); if(!tpl) return;
    tvIndex = tvHosts.indexOf(host);
    tvBuildStep(host);
    tvBody.innerHTML = '';
    // Show the step's screenshot beside its elements so the two can be read together;
    // hovering an element row highlights its frame on the shot (tv-hl overlay).
    var shot = host.querySelector('img.shot'), imEl = null;
    if(shot){
      var sd = document.createElement('div'); sd.className = 'tv-shot';
      var frame = document.createElement('div'); frame.className = 'tv-shotframe';
      imEl = document.createElement('img'); imEl.alt = 'step screenshot';
      imEl.src = shot.getAttribute('src');
      var hl = document.createElement('div'); hl.className = 'tv-hl'; hl.hidden = true;
      frame.appendChild(imEl); frame.appendChild(hl); sd.appendChild(frame); tvBody.appendChild(sd);
    }
    var tree = document.createElement('div'); tree.className = 'tv-tree';
    tree.innerHTML = tpl.innerHTML;
    tree.addEventListener('mouseover', function(e){ var tr = e.target.closest('tr.tvrow'); if(tr) tvHighlight(tr); });
    tree.addEventListener('mouseleave', tvUnhighlight);
    tvBody.appendChild(tree);
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
    tvUpdateNav();
    tvBody.scrollTop = 0;
    tv.classList.add('open');
  }
  document.addEventListener('click', function(e){
    var b = e.target.closest('.treebtn'); if(!b) return;
    tvOpen(b.closest('td.ev') || b.parentNode);
  });
  if(tv){
    tv.addEventListener('click', function(e){ if(e.target === tv) tvClose(); });  // backdrop only
    var tvX = tv.querySelector('.tv-close'); if(tvX) tvX.addEventListener('click', tvClose);
    if(tvPrev) tvPrev.addEventListener('click', function(){ tvGo(-1); });
    if(tvNext) tvNext.addEventListener('click', function(){ tvGo(1); });
    if(tvInput) tvInput.addEventListener('input', function(){ tvFilter(this.value); });
    document.addEventListener('keydown', function(e){
      if(!tv.classList.contains('open')) return;
      if(e.key === 'Escape'){ tvClose(); return; }
      // While typing in the filter, let ← / → move the text cursor instead of navigating.
      if(document.activeElement === tvInput && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) return;
      if(e.key === 'ArrowLeft' && tvCanGo(-1)){ e.preventDefault(); tvGo(-1); }
      else if(e.key === 'ArrowRight' && tvCanGo(1)){ e.preventDefault(); tvGo(1); }
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
    var player = scn.querySelector('.player'), wrap = scn.querySelector('.rich-wrap');
    if(!wrap) return;
    if(!player){ wrap.style.maxHeight = ''; return; }
    var pr = player.getBoundingClientRect();
    if(pr.height < 80){ wrap.style.maxHeight = ''; return; }   // collapsed / metadata not loaded yet
    var h = pr.bottom - wrap.getBoundingClientRect().top;
    wrap.style.maxHeight = h > 120 ? h + 'px' : '';
  }
  function syncAllHeights(){ document.querySelectorAll('.scn').forEach(syncResultHeight); }
  window.addEventListener('resize', syncAllHeights);
  document.querySelectorAll('details.scn').forEach(function(d){
    d.addEventListener('toggle', function(){ if(d.open) syncResultHeight(d); });
  });
  document.querySelectorAll('.player').forEach(function(p){
    var v = p.querySelector('video'), btn = p.querySelector('.vplay');
    var seek = p.querySelector('.vseek'), time = p.querySelector('.vtime');
    var marks = p.querySelector('.vmarks'), scn = p.closest('.scn');
    if(!v || !btn || !seek || !time) return;
    function paint(){ btn.textContent = v.paused ? '▶' : '❚❚'; }
    function clock(){ time.textContent = fmtT(v.currentTime) + ' / ' + fmtT(v.duration); }
    function ticks(){
      // One tick per executed step, placed at its recording offset (data-t seconds).
      // Each carries a hover bubble (step number + time) and seeks there on click.
      if(!marks || !scn || !isFinite(v.duration) || v.duration <= 0) return;
      var html = '';
      scn.querySelectorAll('tr.srow[data-t]').forEach(function(r){
        var t = parseFloat(r.getAttribute('data-t')); if(isNaN(t)) return;
        var pct = Math.max(0, Math.min(100, t / v.duration * 100));
        var td = r.querySelector('td'), num = td ? td.textContent.trim() : '';
        html += '<span class="vmark" data-t="' + t + '" style="left:' + pct.toFixed(3) + '%">'
          + '<span class="vmtip">Step ' + esc(num) + ' · ' + fmtT(t) + '</span></span>';
      });
      marks.innerHTML = html;
    }
    function meta(){ if(isFinite(v.duration)) seek.max = v.duration; clock(); ticks(); if(scn) syncResultHeight(scn); }
    function toggle(){ if(v.paused) v.play(); else v.pause(); }
    btn.addEventListener('click', toggle);
    v.addEventListener('click', toggle);   // clicking the frame itself plays/pauses
    if(marks) marks.addEventListener('click', function(e){   // clicking a tick seeks to that step
      var m = e.target.closest('.vmark'); if(!m) return;
      var t = parseFloat(m.getAttribute('data-t')); if(!isNaN(t)) v.currentTime = t;
    });
    v.addEventListener('play', paint);
    v.addEventListener('pause', paint);
    v.addEventListener('loadedmetadata', meta);
    v.addEventListener('timeupdate', function(){
      if(!seek.matches(':active')) seek.value = v.currentTime;   // don't fight an active drag
      clock();
    });
    seek.addEventListener('input', function(){ v.currentTime = parseFloat(seek.value); });
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
  document.querySelectorAll('.scn').forEach(function(scn){
    var v = scn.querySelector('video'); if(!v) return;
    var rows = Array.prototype.slice.call(scn.querySelectorAll('tr.srow'));
    if(!rows.length) return;
    var box = scn.querySelector('.rich-scroll'), lastCur = null;
    rows.forEach(function(r){
      r.addEventListener('click', function(e){
        if(e.target.closest('a') || e.target.closest('.treebtn')) return;  // links / tree button handled elsewhere
        var shot = e.target.closest('.shot');
        if(shot){ tvOpen(shot.closest('td.ev')); return; }   // screenshot opens the element viewer
        var t = parseFloat(r.getAttribute('data-t'));
        // Seek only: keep playing if already playing, stay paused if paused.
        if(!isNaN(t)){ v.currentTime = t; }
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
})();
