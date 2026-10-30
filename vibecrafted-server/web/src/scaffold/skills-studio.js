(function () {
  var STATUS_RE = /`?\[([ xX~!?])\]`?/g;
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function normalizeMark(m) { return m === 'X' ? 'x' : m; }
  // Skills are documents, not Scaffold trackers: status markers stay inert.
  function statusChip(mark) { return '[' + esc(mark) + ']'; }
  /* SHARED_MARKDOWN */
  /* SKILL_TEXT_BUFFER */
  var room = document.querySelector('[data-skills-room]');
  if (!room || room.dataset.ready) return;
  room.dataset.ready = '1';
  var search = room.querySelector('[data-skill-search]');
  if (search) search.addEventListener('input', function () {
    var query = search.value.toLowerCase();
    room.querySelectorAll('[data-skill-name]').forEach(function (tab) {
      tab.hidden = !tab.dataset.skillName.toLowerCase().includes(query);
    });
  });
  var form = room.querySelector('form.editor-form');
  if (!form) return;
  var text = form.querySelector('textarea');
  var version = form.querySelector('input[name=expected_sha256]');
  var rich = form.querySelector('.rich-pane');
  var edit = room.querySelector('[data-skill-edit]');
  var status = room.querySelector('[data-save-status]');
  var activation = room.querySelector('[data-activation]');
  var mode = room.querySelector('[data-skill-mode]');
  var chars = room.querySelector('[data-skill-chars]');
  var lines = room.querySelector('[data-skill-lines]');
  // HTML parsing strips an initial textarea newline, and textarea.value
  // normalizes CRLF/CR. Restore from the escaped JSON and retain original
  // newline bytes separately from the browser's normalized editing value.
  var snapshotElement = document.getElementById('vc-skills-data');
  var snapshot;
  try { snapshot = JSON.parse(snapshotElement.textContent); } catch (_) { return; }
  if (!snapshot || typeof snapshot.text !== 'string') return;
  var buffer = createSkillBuffer(snapshot.text);
  text.value = snapshot.text;
  var baseline = text.value;
  var baselineRaw = snapshot.text;
  var pendingInput = null;
  var pastedText = null;
  var saving = false;
  var guardEntry = false;
  var leaving = false;
  function changed() { return buffer.failed() || text.value !== baseline || buffer.raw() !== baselineRaw; }
  function dirty() { return changed() || saving; }
  function confirmLeave() { return window.confirm('Discard unsaved changes to this skill?'); }
  function stats() {
    chars.textContent = Array.from(text.value).length + ' chars';
    lines.textContent = (text.value ? text.value.split('\n').length : 0) + ' lines';
  }
  function setMode(editing) {
    rich.hidden = editing;
    text.hidden = !editing;
    if (!editing) rich.innerHTML = mdToHtml(text.value);
    if (edit) { edit.textContent = editing ? 'Save' : 'Edit'; edit.dataset.editing = editing ? '1' : ''; }
    mode.textContent = editing ? 'edit' : 'rich';
    if (editing) text.focus();
    stats();
  }
  text.addEventListener('paste', function (event) {
    pastedText = event.clipboardData ? event.clipboardData.getData('text/plain') : null;
  });
  text.addEventListener('beforeinput', function (event) {
    pendingInput = { type: event.inputType, start: text.selectionStart, end: text.selectionEnd, paste: pastedText };
    pastedText = null;
  });
  text.addEventListener('input', function () {
    buffer.apply(text.value, pendingInput);
    pendingInput = null;
    status.textContent = buffer.failed() ? 'Cannot safely preserve this edit. Copy your text before reloading.' : changed() ? 'Unsaved changes' : 'Loaded';
    if (dirty() && !guardEntry) { history.pushState(history.state, '', location.href); guardEntry = true; }
    stats();
  });
  document.addEventListener('click', function (event) {
    var anchor = event.target.closest('a[href]');
    if (!anchor || !dirty() || event.defaultPrevented || anchor.target === '_blank' || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (!confirmLeave()) { event.preventDefault(); event.stopImmediatePropagation(); return; }
    leaving = true;
    setTimeout(function () { leaving = false; }, 1000);
  }, true);
  window.addEventListener('popstate', function (event) {
    if (!guardEntry) return;
    event.stopImmediatePropagation();
    if (dirty() && !confirmLeave()) { history.pushState(history.state, '', location.href); return; }
    guardEntry = false;
    leaving = true;
    history.back();
  }, true);
  window.addEventListener('beforeunload', function (event) {
    if (!leaving && dirty()) { event.preventDefault(); event.returnValue = ''; }
  });
  if (edit) edit.addEventListener('click', function () {
    if (saving) return;
    if (!edit.dataset.editing) { setMode(true); return; }
    if (buffer.failed()) { status.textContent = 'Cannot safely preserve this edit. Copy your text before reloading.'; return; }
    if (!changed()) { setMode(false); return; }
    var submitted = text.value;
    var submittedRaw = buffer.raw();
    var data = new FormData(form);
    data.set('text', submittedRaw);
    saving = true;
    edit.disabled = true;
    status.textContent = 'Saving…';
    fetch(form.action, { method: 'POST', headers: { 'Accept': 'application/json' }, body: new URLSearchParams(data) })
      .then(function (response) {
        if (response.status === 409) throw new Error('conflict');
        if (!response.ok) throw new Error('save failed');
        return response.json();
      })
      .then(function (result) {
        if (!version || !result || !/^[0-9a-f]{64}$/.test(result.expected_sha256)) throw new Error('save failed');
        version.value = result.expected_sha256;
        baseline = submitted;
        baselineRaw = submittedRaw;
        if (activation && result.origin === 'local' && result.views) {
          room.querySelectorAll('[data-skill-state]').forEach(function (element) { element.textContent = 'Local version'; });
          activation.textContent = 'Agent views: ' + Object.keys(result.views).sort().map(function (name) {
            var state = result.views[name];
            return name + ' ' + (state === 'published' ? 'published' : state === 'conflict' ? 'conflict' : state === 'missing_root' ? 'missing root' : state === 'unmanaged' ? 'unmanaged' : state === 'not_configured' ? 'not configured' : 'unresolved');
          }).join(' · ');
        }
        status.textContent = changed() ? 'Unsaved changes' : 'Saved';
        if (!changed()) setMode(false);
      })
      .catch(function (error) { status.textContent = error.message === 'conflict' ? 'This skill changed elsewhere. Your edits are still here; copy them before reloading.' : 'Save could not be confirmed. Your edits are still here.'; })
      .finally(function () { saving = false; edit.disabled = false; });
  });
  form.addEventListener('submit', function (event) { event.preventDefault(); if (edit) edit.click(); });
  setMode(false);
})();
