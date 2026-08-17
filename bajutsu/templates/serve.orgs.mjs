// serve.orgs.mjs — the admin's Orgs page: create, retire, and re-member a tenant (BE-0375).
//
// A serve.*.mjs section module (BE-0247 ES-module split), modelled on serve.projects.mjs. Where
// that page manages what a tenant tests, this one manages the tenant itself: which GitHub login or
// GitHub organization signs in as this org, and which flat GitHub Team its editors belong to. All
// four endpoints behind it are admin-only and need a database, so a viewer/editor and a
// database-less serve both get a non-list answer from `/api/orgs` — which is exactly what hides the
// tab, without the page having to be told a role. The body only defines; the entry module
// (serve.author.mjs) calls initOrgsView() once every section has evaluated.
import {$, esc, postJSON, setStatus, getJSON} from './serve.core.mjs';

// The last list `/api/orgs` returned, so the edit form can prefill from what the server actually
// holds rather than from what the row happens to render.
let orgsCache = [];

// A comma/whitespace-separated field to a clean list, and back. The API takes arrays; a text input
// is what an admin can paste a roster into.
function parseList(raw) {
  return raw.split(/[\s,]+/).map(s => s.trim()).filter(Boolean);
}

function orgRow(o) {
  const disabled = o.projectCount > 0;
  const removeTitle = disabled
    ? `deregister this org's ${o.projectCount} project(s) first`
    : 'retire this org — it stops admitting sign-ins; its history is kept';
  return `<li class="prjrow orgrow" data-testid="orgs.row" data-slug="${esc(o.slug)}">
    <span class="prjname" data-testid="orgs.slug">${esc(o.slug)}</span>
    <span class="prjsrc" data-testid="orgs.summary">${o.name && o.name !== o.slug ? esc(o.name) + ' · ' : ''}${o.members.length} member(s) · ${o.githubOrgs.length} GitHub org(s) · ${o.editorTeam ? esc(o.editorTeam) : 'no editor Team'} · ${o.projectCount} project(s)</span>
    <button class="cfgbtn" data-act="edit" data-testid="orgs.edit">Membership</button>
    <button class="cfgbtn prjremove" data-act="remove" data-testid="orgs.remove" title="${esc(removeTitle)}"${disabled ? ' disabled' : ''}>Delete</button>
  </li>`;
}

// The per-org membership form, revealed by the row's Membership button. All three fields are
// replaced as one unit — the same granularity a config edit had — so the form always shows the
// whole roster rather than offering per-entry adds that could interleave.
function membershipForm(o) {
  return `<div class="gitsrc orgedit" data-testid="orgs.form" data-slug="${esc(o.slug)}">
    <label class="gitsrclbl" for="orgs-members">Membership of "${esc(o.slug)}" — replaces all three fields</label>
    <input type="text" id="orgs-members" data-testid="orgs.members" placeholder="member GitHub logins (comma-separated)" value="${esc(o.members.join(', '))}">
    <input type="text" id="orgs-github-orgs" data-testid="orgs.github-orgs" placeholder="GitHub organizations (comma-separated)" value="${esc(o.githubOrgs.join(', '))}">
    <input type="text" id="orgs-editor-team" data-testid="orgs.editor-team" placeholder="editor Team, e.g. acme-gh/scenario-maintainers" value="${esc(o.editorTeam || '')}">
    <button class="cfgbtn" data-act="save" data-testid="orgs.save">Save</button>
    <button class="cfgbtn" data-act="cancel" data-testid="orgs.cancel">Cancel</button>
  </div>`;
}

function renderOrgsView() {
  const host = $('#orgs-host');
  if (!host) return;
  if (!orgsCache.length) {
    host.innerHTML = '<div class="empty" data-testid="orgs.empty">No orgs yet — create one above.</div>';
    return;
  }
  host.innerHTML = '<ul class="fslist prjlist" data-testid="orgs.list">' + orgsCache.map(orgRow).join('') + '</ul>';
  host.querySelectorAll('button[data-act="edit"]').forEach(b =>
    b.addEventListener('click', () => openMembership(b.closest('.orgrow').dataset.slug)));
  host.querySelectorAll('button[data-act="remove"]').forEach(b =>
    b.addEventListener('click', () => deleteOrg(b.closest('.orgrow').dataset.slug)));
}

function openMembership(slug) {
  const org = orgsCache.find(o => o.slug === slug);
  if (!org) return;
  // One form at a time: its input ids are singletons, so a second open form would make every
  // `saveMembership` read the *first* form's fields and write one org's roster onto another.
  // Re-rendering first closes any open form; it also rebuilds the host, so the row is queried after.
  renderOrgsView();
  const row = $('#orgs-host').querySelector(`.orgrow[data-slug="${CSS.escape(slug)}"]`);
  row.insertAdjacentHTML('afterend', `<li class="prjrow">${membershipForm(org)}</li>`);
  const form = row.nextElementSibling.querySelector('.orgedit');
  form.querySelector('button[data-act="save"]').addEventListener('click', () => saveMembership(slug));
  form.querySelector('button[data-act="cancel"]').addEventListener('click', () => renderOrgsView());
}

// Fetch the roster. A non-array answer means this deployment or this user cannot administer orgs
// (no database, or not an admin), which is also what decides whether the tab is offered at all —
// the server stays the single authority on both, and the page never guesses a role.
async function loadOrgs() {
  const list = await getJSON('/api/orgs', null);
  const allowed = Array.isArray(list);
  orgsCache = allowed ? list : [];
  const tab = document.querySelector('.toptab[data-view="orgs"]');
  if (tab) tab.hidden = !allowed;
  renderOrgsView();
}

// Create an org. Its membership starts empty, so it admits nobody until the form above is filled —
// stated inline, because "created but nobody can sign in" would otherwise read as a bug.
async function addOrg() {
  const err = $('#orgs-error');
  err.hidden = true;
  const slug = $('#orgs-add-slug').value.trim();
  if (!slug) { err.textContent = 'Enter an org slug.'; err.hidden = false; return; }
  const name = $('#orgs-add-name').value.trim();
  const d = await postJSON('/api/orgs', {slug, name}, {error: 'request failed'});
  if (d && d.error) { err.textContent = d.error; err.hidden = false; return; }
  $('#orgs-add-slug').value = '';
  $('#orgs-add-name').value = '';
  setStatus($('#orgs-add-status'), `created "${slug}" — it admits nobody until you set its membership`, 'ok');
  await loadOrgs();
}

async function saveMembership(slug) {
  const err = $('#orgs-error');
  err.hidden = true;
  const body = {
    members: parseList($('#orgs-members').value),
    githubOrgs: parseList($('#orgs-github-orgs').value),
    editorTeam: $('#orgs-editor-team').value.trim(),
  };
  const d = await postJSON('/api/orgs/' + encodeURIComponent(slug) + '/membership', body, {error: 'request failed'});
  if (d && d.error) { err.textContent = d.error; err.hidden = false; return; }
  setStatus($('#orgs-add-status'), `updated "${slug}" — it takes effect on each member's next sign-in`, 'ok');
  await loadOrgs();
}

// Retire an org after confirmation. A soft delete: it stops admitting sign-ins, signs out whoever
// is already signed in as it, and leaves the list — but its runs and audit entries stay queryable
// and its slug stays taken. Say all four, since each is a surprise if discovered afterwards.
async function deleteOrg(slug) {
  const err = $('#orgs-error');
  if (!window.confirm(`Delete org "${slug}"? Anyone signed in as it is signed out immediately, and nobody will be able to sign in as it again. Its runs and audit history are kept, and its slug stays reserved — it cannot be re-created under the same name.`)) return;
  err.hidden = true;
  let d;
  try {
    const r = await fetch('/api/orgs/' + encodeURIComponent(slug), {method: 'DELETE'});
    d = await r.json();
  } catch (e) {
    d = {error: 'request failed'};
  }
  if (d && d.error) { err.textContent = d.error; err.hidden = false; }
  await loadOrgs();
}

// Wire the static controls once. Called by the entry module's boot after every section evaluates.
function initOrgsView() {
  $('#orgs-add-submit').addEventListener('click', addOrg);
  $('#orgs-add-slug').addEventListener('keydown', e => { if (e.key === 'Enter') addOrg(); });
  $('#orgs-refresh').addEventListener('click', loadOrgs);
}

export {loadOrgs, initOrgsView};
