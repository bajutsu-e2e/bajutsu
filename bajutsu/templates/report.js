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
  // Lightbox: click a step thumbnail to view it full-size, then ← / → walk through
  // every screenshot in the run (across scenarios). Esc or a backdrop click closes.
  var lb = document.getElementById('lb');
  var lbImg = lb && lb.querySelector('img');
  var lbCap = lb && lb.querySelector('.lb-cap');
  var lbIndex = -1;
  function lbShots(){ return Array.prototype.slice.call(document.querySelectorAll('img.shot')); }
  function lbShow(i){
    var arr = lbShots(); if(!arr.length || !lbImg) return;
    lbIndex = (i % arr.length + arr.length) % arr.length;   // wrap around
    var s = arr[lbIndex];
    lbImg.src = s.getAttribute('src');
    if(lbCap){
      var scn = s.closest('.scn'), row = s.closest('tr');
      var name = scn && scn.querySelector('.sname') ? scn.querySelector('.sname').textContent : '';
      var step = row && row.querySelector('td') ? row.querySelector('td').textContent : '';
      lbCap.textContent = name + (step !== '' ? '  ·  step ' + step : '') + '   ' + (lbIndex+1) + ' / ' + arr.length;
    }
    lb.classList.add('open');
  }
  function lbClose(){ if(!lb) return; lb.classList.remove('open'); if(lbImg) lbImg.removeAttribute('src'); lbIndex = -1; }
  window.openLightbox = function(src){
    var arr = lbShots(), i = 0;
    for(var k=0;k<arr.length;k++){ if(arr[k].getAttribute('src') === src){ i = k; break; } }
    lbShow(i);
  };
  if(lb){
    lb.addEventListener('click', function(e){ if(e.target === lb) lbClose(); });  // backdrop only
    var prev = lb.querySelector('.lb-prev'), next = lb.querySelector('.lb-next');
    if(prev) prev.addEventListener('click', function(){ lbShow(lbIndex - 1); });
    if(next) next.addEventListener('click', function(){ lbShow(lbIndex + 1); });
  }
  document.addEventListener('keydown', function(e){
    if(!lb || !lb.classList.contains('open')) return;
    if(e.key === 'Escape') lbClose();
    else if(e.key === 'ArrowLeft') lbShow(lbIndex - 1);
    else if(e.key === 'ArrowRight') lbShow(lbIndex + 1);
  });
  // Custom player chrome: a slim bar below the recording (play/pause, scrubber, time),
  // so the controls never overlay the video frame the way the native HTML5 controls do.
  function fmtT(t){
    if(!isFinite(t) || t < 0) t = 0;
    var m = Math.floor(t / 60), s = Math.floor(t % 60);
    return m + ':' + (s < 10 ? '0' : '') + s;
  }
  document.querySelectorAll('.player').forEach(function(p){
    var v = p.querySelector('video'), btn = p.querySelector('.vplay');
    var seek = p.querySelector('.vseek'), time = p.querySelector('.vtime');
    if(!v || !btn || !seek || !time) return;
    function paint(){ btn.textContent = v.paused ? '▶' : '❚❚'; }
    function clock(){ time.textContent = fmtT(v.currentTime) + ' / ' + fmtT(v.duration); }
    function meta(){ if(isFinite(v.duration)) seek.max = v.duration; clock(); }
    function toggle(){ if(v.paused) v.play(); else v.pause(); }
    btn.addEventListener('click', toggle);
    v.addEventListener('click', toggle);   // clicking the frame itself plays/pauses
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
  // Sync each scenario's recording with its step rows: click a step to seek there,
  // and highlight the step whose time window the playhead is in.
  document.querySelectorAll('.scn').forEach(function(scn){
    var v = scn.querySelector('video'); if(!v) return;
    var rows = Array.prototype.slice.call(scn.querySelectorAll('tr.srow'));
    if(!rows.length) return;
    rows.forEach(function(r){
      r.addEventListener('click', function(e){
        if(e.target.closest('a')) return;                 // links (element tree) work normally
        var shot = e.target.closest('.shot');
        if(shot){ openLightbox(shot.getAttribute('src')); return; }
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
    });
  });
})();
