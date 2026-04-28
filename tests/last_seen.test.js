'use strict';

/**
 * Tests for listener/last_seen.js — the module that persists per-group
 * message timestamps so the listener can catch up after a disconnect.
 *
 * Each test uses a tmp file path via os.tmpdir() so tests are fully isolated
 * and never touch the real .last_seen.json file.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const { load, save, update } = require('../listener/last_seen');

function tmpFile() {
  return path.join(os.tmpdir(), `last_seen_test_${Date.now()}_${Math.random()}.json`);
}

afterEach(() => {
  // Nothing to clean up — tmp files are small and OS will handle them.
});

test('load returns empty object when file does not exist', () => {
  const result = load('/nonexistent/path/last_seen.json');
  expect(result).toEqual({});
});

test('save and load round-trip', () => {
  const file = tmpFile();
  const data = { 'group1@g.us': 1700000000, 'group2@g.us': 1700000001 };
  save(data, file);
  expect(load(file)).toEqual(data);
});

test('load returns empty object when file contains invalid JSON', () => {
  const file = tmpFile();
  fs.writeFileSync(file, 'not json at all');
  expect(load(file)).toEqual({});
});

test('update sets timestamp for a new group', () => {
  const file = tmpFile();
  update('group1@g.us', 1700000000, file);
  expect(load(file)['group1@g.us']).toBe(1700000000);
});

test('update keeps the higher timestamp when called twice', () => {
  const file = tmpFile();
  update('group1@g.us', 1700000000, file);
  update('group1@g.us', 1700000050, file);
  expect(load(file)['group1@g.us']).toBe(1700000050);
});

test('update does not lower an existing timestamp', () => {
  const file = tmpFile();
  update('group1@g.us', 1700000100, file);
  update('group1@g.us', 1700000050, file); // older — should be ignored
  expect(load(file)['group1@g.us']).toBe(1700000100);
});

test('update is independent per group', () => {
  const file = tmpFile();
  update('groupA@g.us', 1000, file);
  update('groupB@g.us', 2000, file);
  const state = load(file);
  expect(state['groupA@g.us']).toBe(1000);
  expect(state['groupB@g.us']).toBe(2000);
});
