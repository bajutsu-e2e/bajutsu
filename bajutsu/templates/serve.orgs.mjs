// serve.orgs.mjs — the admin's Orgs page: create, retire, and re-member a tenant (BE-0375).
//
// A serve.*.mjs section module (BE-0247 ES-module split), modelled on serve.projects.mjs. Where
// that page manages what a tenant tests, this one manages the tenant itself: which GitHub login,
// GitHub organization, or flat GitHub Team signs in as this org, and which Team its editors belong
// to. All
// four endpoints behind it are admin-only and need a database, so a viewer/editor and a
// database-less serve both get a non-list answer from `/api/orgs` — which is exactly what hides the
// tab, without the page having to be told a role. The body only defines; the entry module
// (serve.author.mjs) calls initOrgsView() once every section has evaluated.
import {$, esc, postJSON, setStatus, getJSON, unavailableReason} from './serve.core.mjs';

// The last list `/api/orgs` returned, so the edit form can prefill from what the server actually
// holds rather than from what the row happens to render.
let orgsCache = [];

// A comma/whitespace-separated field to a clean list, and back. The API takes arrays; a text input
// is what an admin can paste a roster into.
function parseList(raw) {
  return raw.split(/[\s,]+/).map(s => s.trim()).filter(Boolean);
}

function orgRow(o) {
  // The sign-in fallback is listed — an admin admitted by the admin-Team bypass is sitting in it,
  // and hiding that would hide where their own runs and secrets land — but it is not a tenant: the
  // server refuses to create, re-member, or retire it, so offer no control that can only answer 409.
  const disabled = o.reserved || o.projectCount > 0;
  const removeTitle = o.reserved
    ? 'the sign-in fallback cannot be retired — an unmatched sign-in keeps resolving to it'
    : disabled
      ? `deregister this org's ${o.projectCount} project(s) first`
      : 'retire this org — it stops admitting sign-ins; its history is kept';
  const editTitle = o.reserved
    ? 'the sign-in fallback has no membership to edit — giving it one would make it a tenant'
    : 'replace this org\'s members, GitHub organizations, GitHub Teams, and editor Teams';
  return `<li class="prjrow orgrow" data-testid="orgs.row" data-slug="${esc(o.slug)}"${o.reserved ? ' data-reserved="1"' : ''}>
    <span class="prjname" data-testid="orgs.slug">${esc(o.slug)}</span>
    <span class="prjsrc" data-testid="orgs.summary">${o.reserved ? 'sign-in fallback · ' : ''}${o.name && o.name !== o.slug ? esc(o.name) + ' · ' : ''}${o.members.length} member(s) · ${o.githubOrgs.length} GitHub org(s) · ${o.githubTeams.length} Team(s) · ${o.editorTeams.length} editor Team(s) · ${o.projectCount} project(s)</span>
    <button class="cfgbtn" data-act="edit" data-testid="orgs.edit" title="${esc(editTitle)}"${o.reserved ? ' disabled' : ''}>Membership</button>
    <button class="cfgbtn prjremove" data-act="remove" data-testid="orgs.remove" title="${esc(removeTitle)}"${disabled ? ' disabled' : ''}>Delete</button>
  </li>`;
}

// The per-org membership form, revealed by the row's Membership button. All four fields are
// replaced as one unit — the same granularity a config edit had — so the form always shows the
// whole roster rather than offering per-entry adds that could interleave.
function membershipForm(o) {
  return `<div class="gitsrc orgedit" data-testid="orgs.form" data-slug="${esc(o.slug)}">
    <label class="gitsrclbl" for="orgs-members">Membership of "${esc(o.slug)}" — replaces all four fields</label>
    <input type="text" id="orgs-members" data-testid="orgs.members" placeholder="member GitHub logins (comma-separated)" value="${esc(o.members.join(', '))}">
    <input type="text" id="orgs-github-orgs" data-testid="orgs.github-orgs" placeholder="GitHub organizations (comma-separated)" value="${esc(o.githubOrgs.join(', '))}">
    <input type="text" id="orgs-github-teams" data-testid="orgs.github-teams" placeholder="GitHub Teams that may sign in, e.g. acme-gh/qa (comma-separated)" value="${esc(o.githubTeams.join(', '))}">
    <input type="text" id="orgs-editor-teams" data-testid="orgs.editor-teams" placeholder="editor Teams, e.g. acme-gh/scenario-maintainers (comma-separated) — their members may sign in too" value="${esc(o.editorTeams.join(', '))}">
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

// Fetch the roster — but only where there is one to fetch. The boot read says whether this
// deployment and this caller can administer orgs at all, so a database-less serve is never asked
// and never answers the 400 that used to sit in the console on every load (#1721). Past that gate a
// non-array answer still hides the tab: the flag was read at boot and a role can change under it,
// and the server, not the page, stays the authority on what this caller may see.
async function loadOrgs() {
  const tab = document.querySelector('.toptab[data-view="orgs"]');
  const blocked = unavailableReason('orgs');
  const list = blocked ? null : await getJSON('/api/orgs', null);
  const allowed = Array.isArray(list);
  orgsCache = allowed ? list : [];
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
    githubTeams: parseList($('#orgs-github-teams').value),
    editorTeams: parseList($('#orgs-editor-teams').value),
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
