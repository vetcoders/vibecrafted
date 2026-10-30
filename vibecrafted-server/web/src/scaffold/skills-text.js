// Preserve exact newline bytes for untouched text while editing in a textarea,
// whose DOM value normalizes CRLF and CR to LF.
function createSkillBuffer(initial) {
  var raw = initial;
  var value = initial.replace(/\r\n|\r/g, '\n');
  var history = [{ value: value, raw: raw }];
  var cursor = 0;
  var failed = false;
  function remember() {
    history.splice(cursor + 1);
    history.push({ value: value, raw: raw });
    cursor = history.length - 1;
    // Old undo states may be forgotten; an unrecognized undo fails closed.
    while (history.length > 1 && (history.length > 128 || history.reduce(function (total, item) { return total + item.value.length + item.raw.length; }, 0) > 4 * 1024 * 1024)) {
      history.shift();
      cursor--;
    }
  }
  function offset(position) {
    var index = 0, count = 0;
    while (index < raw.length && count < position) {
      index += raw.charAt(index) === '\r' && raw.charAt(index + 1) === '\n' ? 2 : 1;
      count++;
    }
    return index;
  }
  return {
    raw: function () { return raw; },
    failed: function () { return failed; },
    apply: function (next, edit) {
      if (failed) return false;
      if (!edit) {
        if (next === value) return true;
        failed = true; return false;
      }
      if (typeof edit.type !== 'string') { failed = true; return false; }
      if (next === value && edit.type !== 'insertFromPaste' && edit.type !== 'historyUndo' && edit.type !== 'historyRedo') return true;
      if (edit.type === 'historyUndo' || edit.type === 'historyRedo') {
        var direction = edit.type === 'historyUndo' ? -1 : 1;
        var target = -1;
        // Browsers may group several inputs into one undo step; if two
        // reachable versions display the same text but differ in newline
        // bytes, there is no safe way to guess which one was requested.
        var foundRaw = null;
        for (var i = cursor + direction; i >= 0 && i < history.length; i += direction) {
          if (history[i].value !== next) continue;
          if (foundRaw !== null && foundRaw !== history[i].raw) { failed = true; return false; }
          if (target < 0) target = i;
          foundRaw = history[i].raw;
        }
        if (target < 0) { failed = true; return false; }
        cursor = target;
        raw = history[cursor].raw;
        value = next;
        return true;
      }
      var start = edit.start, end = edit.end;
      if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end < start || end > value.length) { failed = true; return false; }
      if (start === end && edit.type.indexOf('delete') === 0) {
        var removed = value.length - next.length;
        if (removed <= 0) { failed = true; return false; }
        if (edit.type.indexOf('Backward') !== -1) start -= removed;
        else if (edit.type.indexOf('Forward') !== -1) end += removed;
        else { failed = true; return false; }
      } else if (edit.type.indexOf('insert') !== 0 && edit.type.indexOf('delete') !== 0) {
        failed = true; return false;
      }
      if (start < 0 || end > value.length) { failed = true; return false; }
      var prefix = value.slice(0, start), suffix = value.slice(end);
      if (!next.startsWith(prefix) || !next.endsWith(suffix) || next.length < prefix.length + suffix.length) { failed = true; return false; }
      var inserted = next.slice(prefix.length, next.length - suffix.length);
      var rawStart = offset(start), rawEnd = offset(end);
      if (edit.type === 'insertFromPaste' && inserted.indexOf('\n') !== -1) {
        if (typeof edit.paste !== 'string' || edit.paste.replace(/\r\n|\r/g, '\n') !== inserted) { failed = true; return false; }
        inserted = edit.paste;
      } else {
        if (inserted.indexOf('\n') !== -1 && edit.type !== 'insertLineBreak' && edit.type !== 'insertParagraph') { failed = true; return false; }
        var newline = (raw.match(/\r\n|\r|\n/) || ['\n'])[0];
        inserted = inserted.replace(/\n/g, function () { return newline; });
      }
      raw = raw.slice(0, rawStart) + inserted + raw.slice(rawEnd);
      value = next;
      remember();
      return true;
    }
  };
}
