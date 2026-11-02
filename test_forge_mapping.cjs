'use strict';

// Behavioral contracts for the read-only Forge view model. No browser or server.
const assert = require('node:assert/strict');
const { derive, slots, groups } = require('./web/forge-live.js');
const THREAD = 'sample-thread';
let passed = 0;
let failed = 0;

function freeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}
function snapshot(turn = {}, extra = {}) {
  return {
    thread: { id: THREAD, title: '示例任务' },
    connection: { ok: true },
    now: 2000,
    turns: [{ id: 'turn-a', status: 'active', start: 1900, steps: [], ...turn }],
    codeEntries: [],
    ...extra,
  };
}
function step(id, category, status, extra = {}) {
  return { id, category, status, tool: 'functions.' + category, start: 1901, end: status === 'active' ? null : 1902, ...extra };
}
function check(name, fn) {
  try {
    fn();
    passed += 1;
    console.log('PASS ' + name);
  } catch (error) {
    failed += 1;
    console.error('FAIL ' + name + '\n' + error.stack);
  }
}

check('another chat is rejected before exposing a live view', () => {
  const view = derive(snapshot({}, { thread: { id: 'unselected-chat' } }));
  assert.equal(view.valid, false);
  assert.equal(view.connected, false);
  assert.equal(view.code, undefined);
});

check('active leaf tools focus their own groups, even under a pending outer exec', () => {
  const input = freeze(snapshot({ steps: [
    step('outer', 'plan', 'active', { batch: true }),
    step('read-1', 'read', 'active'),
    step('write-1', 'write', 'active'),
  ] }));
  const view = derive(input);
  assert.equal(view.running, true);
  assert.equal(view.focus.kind, 'tool');
  assert.deepEqual([...view.focus.groups].sort(), ['read', 'write']);
  assert.deepEqual(view.work.map(s => s.id), ['read-1', 'write-1']);
  assert.equal(view.groups.find(g => g.id === 'plan').records.length, 0);
});

check('pending outer exec focuses orchestration after its leaf tool has returned', () => {
  const input = freeze(snapshot({ steps: [
    step('outer', 'plan', 'active', { batch: true }),
    step('cmd-1', 'command', 'complete'),
  ] }));
  const view = derive(input, { turnId: 'turn-a', groups: ['command'] });
  assert.deepEqual(view.focus.groups, ['plan']);
  assert.equal(view.focus.kind, 'tool');
  assert.equal(view.returned, 1);
  assert.equal(view.work.length, 1, 'outer exec is not counted as a second leaf operation');
  assert.equal(input.turns[0].steps[1].status, 'complete');
});

check('processing gap preserves focus without relabeling a completed step', () => {
  const input = freeze(snapshot({ steps: [step('write-1', 'write', 'complete')] }));
  const before = JSON.stringify(input);
  const view = derive(input, { turnId: 'turn-a', groups: ['write'] });
  assert.equal(view.running, true);
  assert.equal(view.focus.kind, 'processing');
  assert.deepEqual(view.focus.groups, ['plan']);
  assert.equal(view.work[0].status, 'complete');
  assert.equal(view.active.length, 0);
  assert.equal(view.groups.find(g => g.id === 'write').pending, 0);
  assert.equal(view.groups.find(g => g.id === 'write').returned, 1);
  assert.equal(JSON.stringify(input), before, 'render focus must not mutate recorded facts');
});

check('initial active snapshot can focus the last observed completed tool', () => {
  const view = derive(snapshot({ steps: [step('search-1', 'search', 'returned')] }));
  assert.deepEqual(view.focus.groups, ['plan']);
  assert.equal(view.focus.kind, 'processing');
  assert.equal(view.work[0].status, 'returned');
});

for (const status of ['completed', 'failed', 'interrupted']) {
  check(status + ' turn clears focus while retaining original step status', () => {
    const input = freeze(snapshot({ status, steps: [step('write-1', 'write', 'active')] }));
    const view = derive(input, { turnId: 'turn-a', groups: ['write'] });
    assert.equal(view.running, false);
    assert.deepEqual(view.focus.groups, []);
    assert.equal(view.focus.kind, 'idle');
    assert.equal(input.turns[0].steps[0].status, 'active');
  });
}

check('disconnection clears focus although the last recorded turn was active', () => {
  const input = freeze(snapshot({ steps: [step('cmd-1', 'command', 'active')] }, { connection: { ok: false, error: 'source unavailable' } }));
  const view = derive(input, { turnId: 'turn-a', groups: ['command'] });
  assert.equal(view.connected, false);
  assert.equal(view.running, false);
  assert.deepEqual(view.focus.groups, []);
  assert.equal(view.focus.kind, 'idle');
  assert.equal(view.work[0].status, 'active');
});

check('new turn never inherits a previous turn focus', () => {
  const view = derive(snapshot({ id: 'turn-b', steps: [] }), { turnId: 'turn-a', groups: ['write'] });
  assert.equal(view.focus.turnId, 'turn-b');
  assert.deepEqual(view.focus.groups, ['plan']);
  assert.equal(view.focus.kind, 'processing');
  const withNewTool = derive(snapshot({ id: 'turn-b', steps: [step('read-b', 'read', 'complete')] }), { turnId: 'turn-a', groups: ['write'] });
  assert.deepEqual(withNewTool.focus.groups, ['plan']);
});

check('missing Token data remains unavailable rather than zero', () => {
  const view = derive(snapshot());
  assert.equal(view.usage, null);
  const unavailable = derive(snapshot({ usage: { available: false, scope: 'turn', total: { total_tokens: 0 } } }, { usage: { available: false, scope: 'session', total: { total_tokens: 0 } } }));
  assert.equal(unavailable.usage, null);
  const partial = derive(snapshot({ usage: { available: true, scope: 'turn', total: { input_tokens: 7 } } }));
  assert.equal(partial.usage.total.output_tokens, undefined);
  assert.equal(partial.usage.total.total_tokens, undefined);
});

check('a recorded zero Token value is preserved when usage is available', () => {
  const view = derive(snapshot({ usage: { available: true, scope: 'turn', total: { input_tokens: 0, output_tokens: 0, total_tokens: 0 } } }));
  assert.equal(view.usage.total.total_tokens, 0);
  assert.equal(view.usage.available, true);
});

check('current-turn usage takes precedence over session cumulative usage', () => {
  const turnUsage = { available: true, scope: 'turn', source: 'turn-record', total: { total_tokens: 37 } };
  const sessionUsage = { available: true, scope: 'session', source: 'session-record', total: { total_tokens: 9001 } };
  const view = derive(freeze(snapshot({ usage: turnUsage }, { usage: sessionUsage })));
  assert.equal(view.usage.scope, 'turn');
  assert.equal(view.usage.source, 'turn-record');
  assert.equal(view.usage.total.total_tokens, 37);
});

check('session fallback keeps session scope when no current-turn usage exists', () => {
  const view = derive(snapshot({ usage: { available: false, scope: 'turn' } }, { usage: { available: true, scope: 'session', total: { total_tokens: 9001 } } }));
  assert.equal(view.usage.scope, 'session');
  assert.equal(view.usage.total.total_tokens, 9001);
});

check('code view contains only the current turn and keeps the latest 64 entries', () => {
  const codeEntries = [{ id: 'old', turnId: 'turn-old', code: 'old_turn_private_value()' }];
  for (let i = 0; i < 80; i++) codeEntries.push({ id: 'current-' + i, turnId: 'turn-a', code: 'step_' + i + '()' });
  codeEntries.push({ id: 'other', turnId: 'turn-other', code: 'other_turn_private_value()' });
  const input = freeze(snapshot({}, { codeEntries }));
  const view = derive(input);
  assert.equal(view.code.length, 64);
  assert.equal(view.code[0].id, 'current-16');
  assert.equal(view.code.at(-1).id, 'current-79');
  assert.ok(view.code.every(e => e.turnId === 'turn-a'));
  assert.equal(input.codeEntries.length, 82);
});

check('without a current turn no historical code is shown', () => {
  const view = derive(snapshot({}, { turns: [], codeEntries: [{ id: 'old', turnId: 'turn-old', code: 'do_not_show()' }] }));
  assert.equal(view.turn, null);
  assert.deepEqual(view.code, []);
});

for (const count of [6, 7, 8]) {
  check(count + ' geometry slots cover every tool category exactly once', () => {
    const mapping = slots(count);
    const all = mapping.flat();
    const expected = groups.map(g => g.id).sort();
    assert.equal(mapping.length, count);
    assert.equal(all.length, 8);
    assert.deepEqual([...all].sort(), expected);
    assert.equal(new Set(all).size, 8);
    assert.ok(mapping.every(ids => ids.length >= 1 && ids.length <= 2));
  });
}

console.log('\nForge mapping checks: ' + passed + ' passed, ' + failed + ' failed');
if (failed) process.exitCode = 1;