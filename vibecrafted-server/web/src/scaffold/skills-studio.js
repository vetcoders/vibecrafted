(function () {
  var STATUS_RE = /`?\[([ xX~!?])\]`?/g;
  function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function normalizeMark(m) { return m === 'X' ? 'x' : m; }
  // Skills are documents, not Scaffold trackers: status markers stay inert.
  function statusChip(mark) { return '[' + esc(mark) + ']'; }
  /* SHARED_MARKDOWN */
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
  var mode = room.querySelector('[data-skill-mode]');
  var chars = room.querySelector('[data-skill-chars]');
  var lines = room.querySelector('[data-skill-lines]');
  var baseline = text.value;
  var saving = false;
  var guardEntry = false;
  var leaving = false;
  function dirty() { return text.value !== baseline || saving; }
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
  text.addEventListener('input', function () {
    status.textContent = text.value === baseline ? 'Loaded' : 'Unsaved changes';
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
    if (text.value === baseline) { setMode(false); return; }
    var submitted = text.value;
    saving = true;
    edit.disabled = true;
    status.textContent = 'Saving…';
    fetch(form.action, { method: 'POST', headers: { 'Accept': 'application/json' }, body: new URLSearchParams(new FormData(form)) })
      .then(function (response) {
        if (response.status === 409) throw new Error('conflict');
        if (!response.ok) throw new Error('save failed');
        return response.json();
      })
      .then(function (result) {
        if (!version || !result || !/^[0-9a-f]{64}$/.test(result.expected_sha256)) throw new Error('save failed');
        version.value = result.expected_sha256;
        baseline = submitted;
        status.textContent = text.value === baseline ? 'Saved' : 'Unsaved changes';
        if (text.value === baseline) setMode(false);
      })
      .catch(function (error) { status.textContent = error.message === 'conflict' ? 'This skill changed elsewhere. Your edits are still here; copy them before reloading.' : 'Save could not be confirmed. Your edits are still here.'; })
      .finally(function () { saving = false; edit.disabled = false; });
  });
  form.addEventListener('submit', function (event) { event.preventDefault(); if (edit) edit.click(); });
  setMode(false);
})();
