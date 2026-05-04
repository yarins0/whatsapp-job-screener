'use strict';

/**
 * Persists the last-seen message timestamp per WhatsApp group so the listener
 * can replay missed messages after a disconnect or sleep.
 *
 * State is stored as JSON at the path provided (defaults to .last_seen.json
 * next to this file). Passing a custom path makes the module testable without
 * touching the real file.
 */

const fs = require('fs');
const path = require('path');

const DEFAULT_PATH = path.join(__dirname, '.last_seen.json');

function load(filePath = DEFAULT_PATH) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    return {};
  }
}

function save(data, filePath = DEFAULT_PATH) {
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2));
}

/**
 * Update the stored timestamp for a group, keeping only the highest value seen.
 * Reads and writes atomically within a single call.
 */
function update(groupId, timestamp, filePath = DEFAULT_PATH) {
  const state = load(filePath);
  state[groupId] = Math.max(state[groupId] || 0, timestamp);
  save(state, filePath);
}

module.exports = { load, save, update, DEFAULT_PATH };
