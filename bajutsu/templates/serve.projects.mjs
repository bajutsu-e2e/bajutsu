// serve.projects.mjs — the hub's Projects page: list projects and add / remove / switch them.
//
// A serve.*.mjs section module (BE-0247 ES-module split). The top-level Projects view BE-0275
// promotes out of the retired BE-0225 modal: it is the hub's home, and its Add form is how a
// single-config serve grows into a hub from the UI (the hosted topology has no CLI). The body only
// defines; the entry module (serve.author.mjs) calls initProjectsView() once every section has
// evaluated. It reuses the shared cache + switch/label/credential helpers from serve.core.mjs — a
// safe import cycle: core calls renderProjectsView() at runtime, and this module reads core's live
// bindings (projectsCache, fsSourceEnabled) at runtime, never at module-evaluation time.
import {
  $, esc, postJSON, setStatus, projectsCache, switchProject,
  storeGitCred, loadProjects, fsSourceEnabled,
} from './serve.core.mjs';

// A one-line, human-readable summary of a project's config source for the list.
function projectSourceLabel(source) {
  if (!source || typeof source !== 'object') return 'unbound';
  const loc = source.locator || {};
  if (source.kind === 'git') return 'git: ' + [loc.owner, loc.repo].filter(Boolean).join('/') + (loc.ref ? ('@' + loc.ref) : '');
  if (source.kind === 'file') return 'file: ' + (loc.path || '');
  if (source.kind === 'upload') return 'uploaded bundle';
  return source.kind || 'unbound';
}

function projectVerdict(run) {
  if (!run) return '<span class="prjv none">no runs</span>';
  return `<span class="prjv ${run.ok ? 'ok' : 'ng'}">${run.ok ? 'PASS' : 'FAIL'} ${run.passed}/${run.total}</span>`;
}

// Render the list core just re-fetched into the page host. Each row shows name / source / latest
// verdict; a non-active row gets a Switch action (rebind the live config, then land on Replay — the
// retired modal's per-row behaviour) and every row a Remove control.
function renderProjectsView() {
  const host = $('#projects-host');
  if (!host) return;
  if (!projectsCache.length) {
    host.innerHTML = '<div class="empty" data-testid="projects.empty">No projects yet — add one above.</div>';
    return;
  }
  host.innerHTML = '<ul class="fslist prjlist" data-testid="projects.list">' + projectsCache.map(p => `<li class="prjrow" data-testid="projects.row" data-name="${esc(p.name)}"${p.active ? ' data-active="1"' : ''}>
    <span class="prjname" data-testid="projects.name">${esc(p.name)}</span>
    <span class="prjsrc">${esc(projectSourceLabel(p.source))}</span>
    ${projectVerdict(p.lastRun)}
    ${p.active ? '<span class="prjactive" data-testid="projects.active">active</span>' : '<button class="cfgbtn" data-act="switch" data-testid="projects.switch">Switch</button>'}
    <button class="cfgbtn prjremove" data-act="remove" data-testid="projects.remove" title="deregister this project">Remove</button>
  </li>`).join('') + '</ul>';
  host.querySelectorAll('button[data-act="switch"]').forEach(b =>
    b.addEventListener('click', () => switchProject(b.closest('.prjrow').dataset.name, { goReplay: true })));
  host.querySelectorAll('button[data-act="remove"]').forEach(b =>
    b.addEventListener('click', () => removeProject(b.closest('.prjrow').dataset.name)));
}

// Deregister a project after confirmation — the binding is removed, its run history retained (the
// BE-0225 contract). A failure surfaces inline (fail loudly), then re-fetch so the page reflects the
// server's post-delete state rather than guessing.
async function removeProject(name) {
  const err = $('#projects-error');
  if (!window.confirm(`Remove project "${name}"? Its run history is kept; only the config binding is removed.`)) return;
  err.hidden = true;
  let d;
  try {
    const r = await fetch('/api/projects/' + encodeURIComponent(name), { method: 'DELETE' });
    d = await r.json();
  } catch (e) {
    d = { error: 'request failed' };
  }
  if (d && d.error) { err.textContent = d.error; err.hidden = false; }
  await loadProjects();
}

// Add a project from a config source. Mirrors `bajutsu project add`: a name + one source string (a
// Git spec `github:owner/repo[@ref][:path]`, or a local path when the server allows the fs source).
// An optional private-repo credential is stored write-once first (like the config launcher's Git
// picker), then POST /api/projects normalizes the string server-side. Re-adding an existing name
// rebinds it (idempotent by name); a server rejection (bad spec, allowlist refusal) shows inline.
async function addProject() {
  const err = $('#projects-error');
  err.hidden = true;
  const name = $('#projects-add-name').value.trim();
  const spec = $('#projects-add-source').value.trim();
  if (!name) { err.textContent = 'Enter a project name.'; err.hidden = false; return; }
  if (!spec) {
    err.textContent = 'Enter a config source: a Git spec (github:owner/repo[@ref][:path])'
      + (fsSourceEnabled ? ' or a local path.' : ' — a local path is not allowed on this server.');
    err.hidden = false;
    return;
  }
  const cred = $('#projects-add-cred').value.trim();
  if (cred && !await storeGitCred(cred, err)) return;  // stores write-once; shows its own error on failure
  const existed = projectsCache.some(p => p.name === name);
  const d = await postJSON('/api/projects', { name, sourceSpec: spec }, { error: 'request failed' });
  if (d && d.error) { err.textContent = d.error; err.hidden = false; return; }
  $('#projects-add-name').value = '';
  $('#projects-add-source').value = '';
  $('#projects-add-cred').value = '';
  setStatus($('#projects-add-status'), existed ? `rebound "${name}"` : `added "${name}"`, 'ok');
  await loadProjects();
}

// Wire the static controls once. Called by the entry module's boot after every section evaluates.
function initProjectsView() {
  $('#projects-add-submit').addEventListener('click', addProject);
  $('#projects-add-source').addEventListener('keydown', e => { if (e.key === 'Enter') addProject(); });
  $('#projects-refresh').addEventListener('click', loadProjects);
}

export { renderProjectsView, initProjectsView };
