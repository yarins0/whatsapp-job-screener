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
const readline = require('readline');
const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');
const axios = require('axios');
const lastSeen = require('./last_seen');

// --- config ----------------------------------------------------------------

// Group IDs are stored in agent/groups.json as a JSON array.
// Edit the file directly, or use the /addgroup and /removegroup Telegram commands.
// Run the listener once with an empty array to discover and print all group IDs.
// Path is resolved relative to the project root (two levels up from this file).
const GROUPS_FILE = path.join(__dirname, '..', '..', 'agent', 'whatsapp_sources.json');

// --- session self-healing ---------------------------------------------------

// After this many consecutive init failures the stale session is wiped so the
// next restart can show a fresh QR code without manual intervention.
const MAX_INIT_FAILURES = 3;

// Stored outside the session dir so it survives the wipe.
const AUTH_DIR = path.join(__dirname, '.wwebjs_auth');
const SESSION_DIR = path.join(AUTH_DIR, 'session');
const FAIL_COUNT_FILE = path.join(AUTH_DIR, '.fail_count');

function readFailCount() {
  try { return parseInt(fs.readFileSync(FAIL_COUNT_FILE, 'utf8'), 10) || 0; } catch (_) { return 0; }
}

function writeFailCount(n) {
  try {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    fs.writeFileSync(FAIL_COUNT_FILE, String(n));
  } catch (_) {}
}

function resetFailCount() {
  try { fs.unlinkSync(FAIL_COUNT_FILE); } catch (_) {}
}

function clearSession() {
  try {
    fs.rmSync(SESSION_DIR, { recursive: true, force: true });
    log('info', '[session] Stale session cleared — next restart will show a fresh QR code.');
  } catch (err) {
    log('warning', `[session] Could not clear session: ${err.message}`);
  }
}

// Called from both startup and reconnect failure paths so the counter covers
// all cases where initialize() fails, not just the initial launch.
function recordInitFailure() {
  const failCount = readFailCount() + 1;
  if (failCount >= MAX_INIT_FAILURES) {
    log('warning', `[init] ${MAX_INIT_FAILURES} consecutive failures — clearing stale session.`);
    clearSession();
    resetFailCount();
  } else {
    writeFailCount(failCount);
    log('info', `[init] Failure ${failCount}/${MAX_INIT_FAILURES} — will clear session on next failure.`);
  }
}

// groups.json is a map: { "id@g.us": "Display Name", ... }
// An empty string means the name has not been resolved yet.
function loadGroups() {
  try {
    const raw = fs.readFileSync(GROUPS_FILE, 'utf8');
    const parsed = JSON.parse(raw);
    return Object.keys(parsed).filter(Boolean);
  } catch (err) {
    log('warning', `[groups] Could not read groups.json: ${err.message}`);
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
      log('warning', `[groups] Could not resolve name for ${groupId}: ${err.message}`);
    }
  }

  try {
    fs.writeFileSync(GROUPS_FILE, JSON.stringify(data, null, 2));
  } catch (err) {
    log('warning', `[groups] Could not write groups.json: ${err.message}`);
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

function log(level, msg) {
  const now = new Date().toTimeString().slice(0, 8);
  process.stdout.write(`[${now}][${level}] ${msg}\n`);
}

// Last-resort safety net. whatsapp-web.js throws some errors asynchronously
// inside Puppeteer event handlers that have no try/catch (e.g. a stray
// "Could not load response body" while WhatsApp Web navigates). Those become
// unhandled rejections that would otherwise kill Node with a raw stack trace,
// bypassing the logger and the orderly exit-1 restart path. Catching them here
// turns every such crash into a clean exit(1) so start.py restarts the listener.
function exitForRestart(label, err) {
  const message = err && err.stack ? err.stack : String(err);
  log('error', `[${label}] Uncaught — exiting for restart: ${message}`);
  process.exit(1);
}

process.on('unhandledRejection', (reason) => exitForRestart('unhandledRejection', reason));
process.on('uncaughtException', (err) => exitForRestart('uncaughtException', err));

// Destroy the current browser process before launching a new one. Calling
// initialize() directly without destroy() leaves the old Chrome process alive,
// and Puppeteer will refuse to open a second browser on the same userDataDir.
async function reconnect() {
  qrPrinted = false;
  lastQrMessageId = null;
  pendingQr = null;
  regenPromptActive = false;
  try {
    await client.destroy();
  } catch (err) {
    // destroy() can throw if the browser frame is already detached — expected.
    log('warning', `[reconnect] destroy() failed: ${err.message}`);
  }
  // Chrome holds a SingletonLock on the user data dir until it fully exits.
  // Without this pause, initialize() races the lock release and fails.
  await new Promise((resolve) => setTimeout(resolve, 3000));
  try {
    await client.initialize();
  } catch (err) {
    // initialize() can fail if Chrome's lock file survives after a bad shutdown.
    // When Node exits, the OS kills Chrome as its child process, releasing the
    // lock. start.py will restart this listener process automatically.
    log('error', `[reconnect] initialize() failed — exiting for restart: ${err.message}`);
    recordInitFailure();
    process.exit(1);
  }
}

async function forwardMessage(msg, groupName) {
  if (!msg.body) return; // skip media-only messages

  // Append any links that whatsapp-web.js parsed but aren't already visible in the
  // body text. This gives the LLM an unambiguous signal for the contact field when
  // the URL is buried in a long message or formatted as a preview rather than plain text.
  let text = msg.body;
  const parsedLinks = (msg.links || []).map((l) => l.link).filter(Boolean);
  const newLinks = [...new Set(parsedLinks)].filter((l) => !text.includes(l));
  if (newLinks.length > 0) {
    text += `\n\nLinks: ${newLinks.join(' ')}`;
  }

  await axios.post(API_URL, {
    group: groupName,
    sender: msg.from,
    text,
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
    log('warning', `[catch-up] Could not resolve group ${groupId} — skipping. ${err.message}`);
    return;
  }

  let messages;
  try {
    // fetchMessages returns newest first; we reverse to process chronologically.
    messages = await chat.fetchMessages({ limit: CATCHUP_LIMIT });
  } catch (err) {
    log('error', `[catch-up] fetchMessages failed for ${ltr(chat.name)}: ${err.message}`);
    return;
  }

  const missed = messages
    .filter((m) => m.timestamp > since && m.body)
    .reverse();

  // Only warn when every fetched slot was a missed message — that means
  // there may be even older missed messages beyond the fetch limit.
  if (missed.length === CATCHUP_LIMIT) {
    log('warning', `[catch-up] ${ltr(chat.name)}: replayed ${CATCHUP_LIMIT} messages and may have missed older ones. Increase CATCHUP_LIMIT if this is a busy group.`);
  }

  if (missed.length > 0) {
    log('info', `[catch-up] ${ltr(chat.name)}: replaying ${missed.length} missed message(s)`);
    for (const m of missed) {
      try {
        await forwardMessage(m, chat.name);
      } catch (err) {
        log('error', `[catch-up] Failed to forward message: ${err.message}`);
      }
    }
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

// Readline interface for the regen prompt. Reads from the real terminal stdin
// (inherited from start.py — only stdout is piped, not stdin).
const rl = readline.createInterface({ input: process.stdin });

// Most recent QR string received while waiting for the user to answer the
// regen prompt. Always holds the freshest code so the user scans a valid one.
let pendingQr = null;
// True while the "regen?" prompt is waiting for a keypress.
let regenPromptActive = false;

// Ask the user whether to print the next QR. Waits indefinitely for input.
function promptRegen(callback) {
  process.stdout.write('\nQR expired — regen another? y/n: ');
  rl.once('line', (answer) => {
    callback(answer.trim().toLowerCase() !== 'n');
  });
}

// Telegram message ID of the last QR photo sent. Reused to edit in place so
// the owner sees a refreshed code without accumulating new messages.
// Reset to null on reconnect so each new session starts with a fresh send.
let lastQrMessageId = null;

// Send or update the QR code photo in the owner's Telegram chat.
// First call sends a new message; subsequent calls edit it in place.
// Only runs when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set.
async function sendQrToTelegram(qr) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_CHAT_ID;
  if (!token || !chatId) return;
  try {
    const pngBuffer = await QRCode.toBuffer(qr, { scale: 8 });
    const blob = new Blob([pngBuffer], { type: 'image/png' });

    if (lastQrMessageId) {
      // Edit the existing message so there is no new notification or chat spam.
      // attach://photo links the multipart field named "photo" as the new media.
      const form = new FormData();
      form.append('chat_id', chatId);
      form.append('message_id', String(lastQrMessageId));
      form.append('media', JSON.stringify({ type: 'photo', media: 'attach://photo' }));
      form.append('photo', blob, 'whatsapp-qr.png');
      await axios.post(`https://api.telegram.org/bot${token}/editMessageMedia`, form);
    } else {
      const form = new FormData();
      form.append('chat_id', chatId);
      form.append('photo', blob, 'whatsapp-qr.png');
      form.append('caption', 'WhatsApp QR — scan within 20 seconds.');
      const res = await axios.post(`https://api.telegram.org/bot${token}/sendPhoto`, form);
      lastQrMessageId = res.data.result.message_id;
    }
    log('info', '[qr] QR code updated in Telegram');
  } catch (err) {
    log('warning', `[qr] Could not update QR in Telegram: ${err.message}`);
  }
}

// Edit the QR message to show a connected confirmation once the scan succeeds.
async function resolveQrMessage() {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_CHAT_ID;
  if (!token || !chatId || !lastQrMessageId) return;
  try {
    await axios.post(`https://api.telegram.org/bot${token}/editMessageCaption`, {
      chat_id: chatId,
      message_id: lastQrMessageId,
      caption: 'WhatsApp connected.',
    });
  } catch (_) { /* best-effort — ignore if message was already deleted */ }
  lastQrMessageId = null;
}

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
  // Pin a known-good WhatsApp Web version served via request interception.
  // Without this, whatsapp-web.js scrapes the live page to auto-detect the
  // version, calling `res.text()` in a response listener that has no try/catch.
  // On certain navigations that throws "Could not load response body", which
  // becomes an unhandled rejection and crashes the whole process. Serving a
  // cached HTML takes the request-interception branch and never reads the
  // response body, eliminating the crash and the WA-version-drift fragility.
  // Bump the version string if WhatsApp ships an incompatible Web update; the
  // available builds are listed at github.com/wppconnect-team/wa-version.
  webVersionCache: {
    type: 'remote',
    remotePath: 'https://raw.githubusercontent.com/wppconnect-team/wa-version/main/html/2.3000.1037892406-alpha.html',
  },
  puppeteer: {
    args: ['--no-sandbox'],
    protocolTimeout: 120000, // 120s — getChats() on large accounts can be slow
  },
});

client.on('qr', (qr) => {
  if (!qrPrinted) {
    // First QR — send to Telegram once, print to terminal, then wait for a scan.
    qrPrinted = true;
    sendQrToTelegram(qr);
    log('info', 'Scan this QR code with WhatsApp:');
    qrcode.generate(qr, { small: true });
    return;
  }

  // Subsequent QRs: store the latest and ask before printing.
  pendingQr = qr;
  if (regenPromptActive) return; // prompt already shown; pendingQr will be used when answered

  regenPromptActive = true;
  promptRegen((confirmed) => {
    regenPromptActive = false;
    if (confirmed && pendingQr) {
      qrcode.generate(pendingQr, { small: true });
    }
    pendingQr = null;
  });
});

// Tracks whether the client is currently connected. Used by the heartbeat to
// avoid calling initialize() when a reconnect is already in progress.
let isReady = false;

// Prevents the QR code from being printed more than once per initialize() call.
// WhatsApp Web regenerates the QR every ~20 s while waiting for a scan, which
// would flood the terminal. Reset to false before each new initialize().
let qrPrinted = false;

// Path for the all-groups snapshot written on every connect.
// Read by the Telegram /listgroups command to show group IDs for discovery.
const ALL_GROUPS_FILE = path.join(__dirname, '..', '..', 'agent', 'all_whatsapp_groups.json');

// Fetch every WhatsApp group on the account and write a snapshot to
// all_whatsapp_groups.json so the Telegram bot can serve /listgroups without
// requiring the listener to be in discovery mode.
async function saveAllGroups() {
  let chats;
  try {
    chats = await client.getChats();
  } catch (err) {
    log('warning', `[groups] Could not fetch all groups for snapshot: ${err.message}`);
    return;
  }
  const groups = chats.filter((c) => c.isGroup);
  const snapshot = {};
  groups.forEach((g) => { snapshot[g.id._serialized] = g.name; });
  try {
    fs.writeFileSync(ALL_GROUPS_FILE, JSON.stringify(snapshot, null, 2));
  } catch (err) {
    log('warning', `[groups] Could not write all_whatsapp_groups.json: ${err.message}`);
  }
}

client.on('ready', async () => {
  isReady = true;
  resetFailCount(); // successful init — clear any accumulated failure count
  await resolveQrMessage();
  // Snapshot the groups list once at startup — used for catch-up only.
  // Live message filtering re-reads groups.json each time, so /addgroup
  // and /removegroup take effect without a restart.
  const watchedGroups = loadGroups();

  // Always write a full snapshot of all groups so /listgroups in the Telegram
  // bot has up-to-date data regardless of which groups are being watched.
  await saveAllGroups();

  if (watchedGroups.length === 0) {
    // Discovery mode: list all groups so the user can pick IDs.
    log('info', 'Connected. groups.json is empty — fetching your groups...');
    try {
      const chats = await client.getChats();
      const groups = chats.filter((c) => c.isGroup);
      log('info', `Found ${groups.length} groups:`);
      groups.forEach((g) => log('info', `  ${g.id._serialized}  —  ${ltr(g.name)}`));
      log('info', 'Use /addgroup <id> in Telegram, or edit agent/groups.json directly, then restart.');
    } catch (err) {
      log('error', `Could not fetch groups: ${err.message}`);
    }
    return;
  }

  // Cache group display names so the Telegram bot can show them in /groups.
  await saveGroupNames(watchedGroups);

  // Snapshot timestamps before the loop so that live messages arriving during
  // catch-up can't advance a group's cursor and cause older messages to be missed.
  const snapshot = lastSeen.load();
  for (const groupId of watchedGroups) {
    await catchUp(groupId, snapshot[groupId] || 0);
  }
  log('info', `Ready — watching ${watchedGroups.length} group(s)`);
});

client.on('auth_failure', (m) => log('error', `auth_failure: ${m}`));

client.on('disconnected', (reason) => {
  isReady = false;
  log('warning', `Disconnected (${reason}). Reconnecting in 10s...`);
  setTimeout(() => {
    log('info', 'Reconnecting...');
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
    log('error', `Failed to forward message: ${err.message}`);
  }
});

// Catch transient startup failures (e.g. "execution context was destroyed"
// when WhatsApp Web navigates during Puppeteer script injection). Exiting with
// code 1 lets start.py restart the listener automatically; it usually succeeds
// on the next attempt.
// After MAX_INIT_FAILURES consecutive failures the stale session is wiped so
// the next restart prompts a fresh QR scan instead of looping forever.
client.initialize().catch((err) => {
  log('error', `[init] initialize() failed — exiting for restart: ${err.message}`);
  recordInitFailure();
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
      log('warning', `[heartbeat] State is ${state || 'null'} — reconnecting...`);
      isReady = false;
      reconnect();
    }
  } catch (err) {
    log('warning', `[heartbeat] getState() failed — reconnecting... ${err.message}`);
    isReady = false;
    reconnect();
  }
}, HEARTBEAT_INTERVAL_MS);
