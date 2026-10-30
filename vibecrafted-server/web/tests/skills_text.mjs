import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const source = readFileSync(new URL('../src/scaffold/skills-text.js', import.meta.url), 'utf8');
const create = runInNewContext(source + '\ncreateSkillBuffer');
const edit = (type, start, end = start, paste = null) => ({ type, start, end, paste });

test('keeps leading newline, mixed newline bytes, and unicode through sequential edits', () => {
  const buffer = create('\r\nα\r\n😀\nlast\r');
  assert.equal(buffer.raw(), '\r\nα\r\n😀\nlast\r');
  assert.equal(buffer.apply('\nα!\n😀\nlast\n', edit('insertText', 2)), true);
  assert.equal(buffer.raw(), '\r\nα!\r\n😀\nlast\r');
  // Delete the LF after the surrogate pair; retain the other CRLF and CR.
  assert.equal(buffer.apply('\nα!\n😀last\n', edit('deleteContentForward', 6)), true);
  assert.equal(buffer.raw(), '\r\nα!\r\n😀last\r');
  assert.equal(buffer.apply('\nα!\n😀\nlast\n', edit('insertLineBreak', 6)), true);
  assert.equal(buffer.raw(), '\r\nα!\r\n😀\r\nlast\r');
  assert.equal(buffer.apply('\nα!\n😀last\n', edit('historyUndo', 0)), true);
  assert.equal(buffer.raw(), '\r\nα!\r\n😀last\r');
  assert.equal(buffer.apply('\nα!\n😀\nlast\n', edit('historyRedo', 0)), true);
  assert.equal(buffer.raw(), '\r\nα!\r\n😀\r\nlast\r');
});

test('uses the actual caret when adjacent mixed newlines make a textual diff ambiguous', () => {
  const buffer = create('x\r\n\ny');
  assert.equal(buffer.apply('x\ny', edit('deleteContentForward', 1)), true);
  assert.equal(buffer.raw(), 'x\ny');
  const other = create('x\r\n\ny');
  assert.equal(other.apply('x\ny', edit('deleteContentForward', 2)), true);
  assert.equal(other.raw(), 'x\r\ny');
});

test('preserves multiline clipboard bytes, and refuses unprovable edits', () => {
  const buffer = create('a\r\nb');
  assert.equal(buffer.apply('a\n猫\nz\nb', edit('insertFromPaste', 2, 2, '猫\nz\n')), true);
  assert.equal(buffer.raw(), 'a\r\n猫\nz\nb');
  assert.equal(buffer.apply('a\n猫\nq\nb', edit('insertFromPaste', 4, 6, null)), false);
  assert.equal(buffer.failed(), true);
  assert.equal(buffer.raw(), 'a\r\n猫\nz\nb');
  const missingEvent = create('x\r\ny');
  assert.equal(missingEvent.apply('x\ny!', null), false);
  assert.equal(missingEvent.failed(), true);
  assert.equal(missingEvent.raw(), 'x\r\ny');
});


test('same visible text pasted with different newlines remains a real raw edit', () => {
  const buffer = create('x\r\ny');
  assert.equal(buffer.apply('x\ny', edit('insertFromPaste', 1, 2, '\n')), true);
  assert.equal(buffer.raw(), 'x\ny');
  assert.equal(buffer.apply('x\ny', edit('historyUndo', 0)), true);
  assert.equal(buffer.raw(), 'x\r\ny');
  assert.equal(buffer.apply('x\ny', edit('historyRedo', 0)), true);
  assert.equal(buffer.raw(), 'x\ny');
});

test('unknown input metadata fails closed', () => {
  const buffer = create('a\r\nb');
  assert.equal(buffer.apply('a!\nb', { start: 1, end: 1 }), false);
  assert.equal(buffer.failed(), true);
  assert.equal(buffer.raw(), 'a\r\nb');
});


test('unverified multiline insertion and unavailable undo state fail closed', () => {
  const dropped = create('a\r\nb');
  assert.equal(dropped.apply('a\nx\nb', edit('insertFromDrop', 2)), false);
  assert.equal(dropped.raw(), 'a\r\nb');
  const history = create('x');
  for (let i = 0; i < 140; i++) {
    const current = 'x'.repeat(i + 1);
    assert.equal(history.apply(current + 'x', edit('insertText', current.length)), true);
  }
  assert.equal(history.apply('x', edit('historyUndo', 0)), false);
  assert.equal(history.failed(), true);
});
