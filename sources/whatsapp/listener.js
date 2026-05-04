/**
 * WhatsApp listener — forwards every message from watched groups to the
 * FastAPI ingest endpoint.
 *
 * On reconnect (e.g. after the computer wakes from sleep) it replays any
 * messages received since the last time it was running, using timestamps
 * stored in sources/whatsapp/.last_seen.json.
 *
 *   1.  npm install
 *   2.  node sources/whatsapp/listener.js
 *   3.  Scan the QR code with WhatsApp on your phone.
 */

'use strict';

const fs = require('fs');
const path = require('path');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const lastSeen = require('./last_seen');

// --- config ----------------------------------------------------------------

// Group IDs are stored in agent/groups.json as a JSON array.
// Edit the file directly, or use the /addgroup and /removegroup Telegram commands.
// Run the listener once with an empty array to discover and print all group IDs.
// Path is resolved relative to the project root (two levels up from this file).
const GROUPS_FILE = path.join(__dirname, '..', '..', 'agent', 'whatsapp_sources.json');

// groups.json is a map: { "id@g.us": "Display Name", ... }
// An empty string means the name has not been resolved yet.
function loadGroups() {
  try {
    const raw = fs.readFileSync(GROUPS_FILE, 'utf8');
    const parsed = JSON.parse(raw);
    return Object.keys(parsed).filter(Boolean);
  } catch (err) {
    console.warn('[groups] Could not read groups.json:', err.message);
    return [];
  }
}

// Resolve display names for all watched groups and write them back into
// groups.json so the Telegram bot can show human-readable names in /groups.
// Preserves existing entries so names from previous sessions are not lost if
// a group temporarily fails to load.
async function saveGroupNames(groupIds) {
  let data = {};
  try {
    data = JSON.parse(fs.readFileSync(GROUPS_FILE, 'utf8'));
  } catch (_) { /* use empty map if file is missing or corrupt */ }

  for (const groupId of groupIds) {
    try {
      const chat = await client.getChatById(groupId);
      data[groupId] = chat.name;
    } catch (err) {
      console.warn(`[groups] Could not resolve name for ${groupId}:`, err.message);
    }
  }

  try {
    fs.writeFileSync(GROUPS_FILE, JSON.stringify(data, null, 2));
  } catch (err) {
    console.warn('[groups] Could not write groups.json:', err.message);
  }
}

const API_URL = process.env.INGEST_API_URL || 'http://localhost:8000/ingest';

// How many recent messages to fetch per group on reconnect.
// fetchMessages always returns the N *newest* messages, so if a group had
// more than this many messages during downtime, the oldest ones are skipped.
const CATCHUP_LIMIT = 50;

// Never replay messages older than this, even if .last_seen.json has an older
// timestamp (or is missing). Prevents a huge backlog after a long absence.
const CATCHUP_MAX_AGE_S = 48 * 60 * 60;

// --- helpers ---------------------------------------------------------------

// U+200E LEFT-TO-RIGHT MARK — zero-width character that tells the terminal's
// BiDi algorithm the surrounding context is LTR. Without it, Hebrew group names
// embedded in an LTR log line are displayed in reversed visual order.
const LRM = '\u200E';
const ltr = (str) => `${LRM}${str}${LRM}`;

// Destroy the current browser process before launching a new one. Calling
// initialize() directly without destroy() leaves the old Chrome process alive,
// and Puppeteer will refuse to open a second browser on the same userDataDir.
async function reconnect() {
  try {
    await client.destroy();
  } catch (err) {
    // destroy() can throw if the browser frame is already detached — expected.
    console.warn('[reconnect] destroy() failed:', err.message);
  }
  try {
    await client.initialize();
  } catch (err) {
    // initialize() can fail if Chrome's lock file survives after a bad shutdown.
    // When Node exits, the OS kills Chrome as its child process, releasing the
    // lock. start.py will restart this listener process automatically.
    console.error('[reconnect] initialize() failed — exiting for restart:', err.message);
    process.exit(1);
  }
}

async function forwardMessage(msg, groupName) {
  if (!msg.body) return; // skip media-only messages
  await axios.post(API_URL, {
    group: groupName,
    sender: msg.from,
    text: msg.body,
    timestamp: msg.timestamp,
  });
}

// snapshotTimestamp is read by the caller before the loop starts, so live
// messages arriving during catch-up can't advance `since` and cause misses.
async function catchUp(groupId, snapshotTimestamp) {
  const cutoff = Math.floor(Date.now() / 1000) - CATCHUP_MAX_AGE_S;
  const since = Math.max(snapshotTimestamp, cutoff);

  let chat;
  try {
    chat = await client.getChatById(groupId);
  } catch (err) {
    console.error(`[catch-up] Could not get chat ${groupId}:`, err.message);
    return;
  }

  let messages;
  try {
    // fetchMessages returns newest first; we reverse to process chronologically.
    messages = await chat.fetchMessages({ limit: CATCHUP_LIMIT });
  } catch (err) {
    console.error(`[catch-up] fetchMessages failed for ${ltr(chat.name)}:`, err.message);
    return;
  }

  // Warn if we fetched exactly CATCHUP_LIMIT messages — the oldest messages in
  // the window may have been cut off and will be permanently missed.
  if (messages.length === CATCHUP_LIMIT) {
    console.warn(
      `[catch-up] ${ltr(chat.name)}: hit the ${CATCHUP_LIMIT}-message fetch ceiling — ` +
      `some older messages may have been missed. Increase CATCHUP_LIMIT if this is a busy group.`
    );
  }

  const missed = messages
    .filter((m) => m.timestamp > since && m.body)
    .reverse();

  if (missed.length > 0) {
    console.log(`[catch-up] ${ltr(chat.name)}: replaying ${missed.length} missed message(s)`);
    for (const m of missed) {
      try {
        await forwardMessage(m, chat.name);
      } catch (err) {
        console.error(`[catch-up] Failed to forward message:`, err.message);
      }
    }
  } else {
    console.log(`[catch-up] ${ltr(chat.name)}: no missed messages`);
  }

  // Advance the cursor to the newest message we just saw.
  if (messages.length > 0) {
    const latest = Math.max(...messages.map((m) => m.timestamp));
    lastSeen.update(groupId, latest);
  }
}

// Heartbeat: how often to check whether the WA connection is still alive.
// On PC wake, the disconnected event often doesn't fire, so we poll instead.
const HEARTBEAT_INTERVAL_MS = 2 * 60 * 1000; // 2 minutes

// --- client ----------------------------------------------------------------

// WhatsApp derives the linked-device name from the user agent string.
// A Windows Chrome UA makes the device appear as "Chrome (Windows)" in the
// WhatsApp app's linked-devices list instead of the default "Chrome (Mac OS)"
// that Puppeteer's bundled Chromium reports.
// dataPath is resolved relative to CWD (the project root when launched via start.py),
// so existing auth in listener/.wwebjs_auth/ is reused without re-scanning the QR code.
const client = new Client({
  authStrategy: new LocalAuth({ dataPath: 'sources/whatsapp/.wwebjs_auth' }),
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  puppeteer: {
    args: ['--no-sandbox'],
    protocolTimeout: 60000, // 60s — getChats() on large accounts can be slow
  },
});

client.on('qr', (qr) => {
  console.log('Scan this QR code with WhatsApp:');
  qrcode.generate(qr, { small: true });
});

// Tracks whether the client is currently connected. Used by the heartbeat to
// avoid calling initialize() when a reconnect is already in progress.
let isReady = false;

client.on('ready', async () => {
  isReady = true;
  // Snapshot the groups list once at startup — used for catch-up only.
  // Live message filtering re-reads groups.json each time, so /addgroup
  // and /removegroup take effect without a restart.
  const watchedGroups = loadGroups();

  if (watchedGroups.length === 0) {
    // Discovery mode: list all groups so the user can pick IDs.
    console.log('\nConnected. groups.json is empty — fetching your groups...\n');
    try {
      const chats = await client.getChats();
      const groups = chats.filter((c) => c.isGroup);
      console.log(`Found ${groups.length} groups:\n`);
      groups.forEach((g) => console.log(`  ${g.id._serialized}  —  ${ltr(g.name)}`));
      console.log('\nUse /addgroup <id> in Telegram, or edit agent/groups.json directly, then restart.\n');
    } catch (err) {
      console.error('Could not fetch groups:', err.message);
    }
    return;
  }

  console.log(`\nConnected. Watching ${watchedGroups.length} group(s). Checking for missed messages...\n`);

  // Cache group display names so the Telegram bot can show them in /groups.
  await saveGroupNames(watchedGroups);

  // Snapshot timestamps before the loop so that live messages arriving during
  // catch-up can't advance a group's cursor and cause older messages to be missed.
  const snapshot = lastSeen.load();
  for (const groupId of watchedGroups) {
    await catchUp(groupId, snapshot[groupId] || 0);
  }
  console.log('\nReady — listening for new messages.\n');
});

client.on('auth_failure', (m) => console.error('auth_failure:', m));

client.on('disconnected', (reason) => {
  isReady = false;
  console.warn(`Disconnected (${reason}). Reconnecting in 10s...`);
  setTimeout(() => {
    console.log('Reconnecting...');
    reconnect();
  }, 10_000);
});

client.on('message', async (msg) => {
  try {
    const chat = await msg.getChat();
    if (!chat.isGroup) return;
    // Re-read groups.json on every message so /addgroup and /removegroup
    // take effect immediately without restarting the listener.
    if (!loadGroups().includes(chat.id._serialized)) return;

    await forwardMessage(msg, chat.name);
    lastSeen.update(chat.id._serialized, msg.timestamp);
  } catch (err) {
    console.error('Failed to forward message:', err.message);
  }
});

// Catch transient startup failures (e.g. "execution context was destroyed"
// when WhatsApp Web navigates during Puppeteer script injection). Exiting with
// code 1 lets start.py restart the listener automatically; it usually succeeds
// on the next attempt.
client.initialize().catch((err) => {
  console.error('[init] initialize() failed — exiting for restart:', err.message);
  process.exit(1);
});

// Heartbeat: detect silent disconnects (e.g. after PC sleep) where the
// 'disconnected' event never fires. Polls getState() and triggers a reconnect
// if the client is no longer CONNECTED, which in turn fires 'ready' → catchUp().
setInterval(async () => {
  if (!isReady) return; // already reconnecting, nothing to do

  try {
    const state = await client.getState();
    if (state !== 'CONNECTED') {
      console.warn(`[heartbeat] State is ${state || 'null'} — reconnecting...`);
      isReady = false;
      reconnect();
    }
  } catch (err) {
    console.warn('[heartbeat] getState() failed — reconnecting...', err.message);
    isReady = false;
    reconnect();
  }
}, HEARTBEAT_INTERVAL_MS);
