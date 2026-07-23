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
const webVersion = require('./web_version');

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

// clearSession retries: Chrome can keep the session files locked for a moment
// after it is asked to exit, so a single unlink attempt can lose the race and
// fail with EBUSY. Retrying with a short pause lets the lock release.
const SESSION_CLEAR_RETRIES = 3;
const SESSION_CLEAR_RETRY_DELAY_MS = 1000;

// Upper bound on how long to wait for the browser to tear down during cleanup,
// so a hung Chrome can never stall the exit-for-restart path.
const BROWSER_DESTROY_TIMEOUT_MS = 10000;

// Stored outside the session dir so it survives the wipe.
const AUTH_DIR = path.join(__dirname, '.wwebjs_auth');
const SESSION_DIR = path.join(AUTH_DIR, 'session');
const FAIL_COUNT_FILE = path.join(AUTH_DIR, '.fail_count');

// Telegram message id of the QR photo, persisted so it survives crash-restarts.
// Without this the id lived only in memory: every restart re-sent a brand-new
// photo (with a notification) instead of editing the existing one in place,
// spamming the owner whenever the listener thrashed while a scan was pending.
const QR_MESSAGE_ID_FILE = path.join(AUTH_DIR, '.qr_message_id');

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

function readQrMessageId() {
  try { return parseInt(fs.readFileSync(QR_MESSAGE_ID_FILE, 'utf8'), 10) || null; } catch (_) { return null; }
}

// Persists the id alongside the in-memory value so a needed rescan keeps editing
// the same Telegram message in place, even across restarts.
function setQrMessageId(id) {
  lastQrMessageId = id;
  try {
    fs.mkdirSync(AUTH_DIR, { recursive: true });
    fs.writeFileSync(QR_MESSAGE_ID_FILE, String(id));
  } catch (_) {}
}

function clearQrMessageId() {
  lastQrMessageId = null;
  try { fs.unlinkSync(QR_MESSAGE_ID_FILE); } catch (_) {}
}

// Returns true only if the session directory is actually gone afterwards.
// Retries because Chrome can keep the LevelDB files locked for a moment after
// it is told to exit; a single attempt previously lost that race and failed
// with EBUSY, leaving the corrupt session in place and the loop running forever.
async function clearSession() {
  for (let attempt = 1; attempt <= SESSION_CLEAR_RETRIES; attempt++) {
    try {
      fs.rmSync(SESSION_DIR, { recursive: true, force: true });
      log('info', '[session] Stale session cleared — next restart will show a fresh QR code.');
      return true;
    } catch (err) {
      log('warning', `[session] Could not clear session (attempt ${attempt}/${SESSION_CLEAR_RETRIES}): ${err.message}`);
      if (attempt < SESSION_CLEAR_RETRIES) {
        await new Promise((resolve) => setTimeout(resolve, SESSION_CLEAR_RETRY_DELAY_MS));
      }
    }
  }
  return false;
}

// Best-effort browser teardown so Chrome exits and releases its locks on the
// session dir before we wipe it (or before the process exits). This prevents
// the two failure modes seen in the wild: the EBUSY that defeated every session
// wipe, and the corrupt session left when Chrome was hard-killed mid-write to
// LevelDB. Bounded by a timeout so a hung browser can't stall the exit.
async function destroyBrowser() {
  try {
    await Promise.race([
      client.destroy(),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error('destroy() timed out')), BROWSER_DESTROY_TIMEOUT_MS)),
    ]);
  } catch (err) {
    log('warning', `[init] Browser teardown during cleanup failed: ${err.message}`);
  }
}

// Called from both startup and reconnect failure paths so the counter covers
// all cases where initialize() fails, not just the initial launch. Destroys the
// browser first so the session files are unlocked, and only resets the counter
// when the wipe truly succeeds — otherwise the next launch retries the clear
// instead of looping forever on a session that was never removed.
async function recordInitFailure() {
  await destroyBrowser();

  const failCount = readFailCount() + 1;
  if (failCount >= MAX_INIT_FAILURES) {
    log('warning', `[init] ${MAX_INIT_FAILURES} consecutive failures — clearing stale session.`);
    if (await clearSession()) {
      resetFailCount();
    }
  } else {
    writeFailCount(failCount);
    log('info', `[init] Failure ${failCount}/${MAX_INIT_FAILURES} — will clear session on next failure.`);
  }
}

// groups.json is a map: { "id@g.us": "Display Name", ... }
// An empty string means the name has not been resolved yet.
function loadGroupMap() {
  try {
    return JSON.parse(fs.readFileSync(GROUPS_FILE, 'utf8'));
  } catch (err) {
    log('warning', `[groups] Could not read groups.json: ${err.message}`);
    return {};
  }
}

function loadGroups() {
  return Object.keys(loadGroupMap()).filter(Boolean);
}

// Resolve display names for all watched groups and write them back into
// groups.json so the Telegram bot can show human-readable names in /groups.
// Preserves existing entries so names from previous sessions are not lost if
// a group temporarily fails to load.
async function saveGroupNames(groupIds, chatMap) {
  let data = {};
  try {
    data = JSON.parse(fs.readFileSync(GROUPS_FILE, 'utf8'));
  } catch (_) { /* use empty map if file is missing or corrupt */ }

  for (const groupId of groupIds) {
    try {
      // getChatById() reliably throws right after 'ready' even for groups
      // getChats() just returned successfully, so prefer the already-fetched
      // chat object and only fall back to getChatById for groups missing
      // from the snapshot (e.g. a group joined since the last poll).
      const chat = chatMap.get(groupId) || await client.getChatById(groupId);
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
  pendingQr = null;
  regenPromptActive = false;
  // lastQrMessageId is intentionally left intact (and is disk-backed): if this
  // reconnect needs a fresh scan, editing the existing QR message in place is
  // cleaner than sending a new photo. It is cleared only once a scan succeeds.
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
    // recordInitFailure() tears the browser down (releasing the lock and
    // flushing LevelDB cleanly) and, after enough failures, wipes the stale
    // session so the next start shows a fresh QR. start.py then restarts us.
    log('error', `[reconnect] initialize() failed — exiting for restart: ${err.message}`);
    await recordInitFailure();
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
async function catchUp(groupId, snapshotTimestamp, chatMap) {
  const cutoff = Math.floor(Date.now() / 1000) - CATCHUP_MAX_AGE_S;
  const since = Math.max(snapshotTimestamp, cutoff);

  let chat;
  try {
    // See saveGroupNames() — getChatById() throws right after 'ready', so
    // prefer the chat object getChats() already fetched.
    chat = chatMap.get(groupId) || await client.getChatById(groupId);
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

// Telegram message ID of the last QR photo sent. Reused to edit in place so the
// owner sees one refreshing code instead of a new photo on every ~20s regen,
// reconnect, or crash-restart. Loaded from disk so it survives restarts; cleared
// only once a scan succeeds (resolveQrMessage).
let lastQrMessageId = readQrMessageId();

// Replace the photo on the already-sent QR message. Returns false if the edit
// failed (e.g. the owner deleted the message, or the persisted id is stale after
// a restart) so the caller can fall back to sending a fresh photo.
// attach://photo links the multipart field named "photo" as the new media.
async function editQrInTelegram(token, chatId, pngBuffer) {
  const blob = new Blob([pngBuffer], { type: 'image/png' });
  const form = new FormData();
  form.append('chat_id', chatId);
  form.append('message_id', String(lastQrMessageId));
  form.append('media', JSON.stringify({ type: 'photo', media: 'attach://photo' }));
  form.append('photo', blob, 'whatsapp-qr.png');
  try {
    await axios.post(`https://api.telegram.org/bot${token}/editMessageMedia`, form);
    return true;
  } catch (err) {
    log('warning', `[qr] Could not edit existing QR message: ${err.message}`);
    return false;
  }
}

// Send a new QR photo and remember its id (memory + disk) so future updates edit
// it in place rather than sending another photo.
async function sendNewQrToTelegram(token, chatId, pngBuffer) {
  const blob = new Blob([pngBuffer], { type: 'image/png' });
  const form = new FormData();
  form.append('chat_id', chatId);
  form.append('photo', blob, 'whatsapp-qr.png');
  form.append('caption', 'WhatsApp QR — scan within 20 seconds.');
  try {
    const res = await axios.post(`https://api.telegram.org/bot${token}/sendPhoto`, form);
    setQrMessageId(res.data.result.message_id);
  } catch (err) {
    log('warning', `[qr] Could not send QR to Telegram: ${err.message}`);
  }
}

// Push the current QR to the owner's Telegram chat. Edits the one persisted
// message in place whenever possible (across regens and restarts) so there is no
// new notification or chat spam; only sends a fresh photo when no editable
// message exists. Only runs when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set.
async function sendQrToTelegram(qr) {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const chatId = process.env.TELEGRAM_CHAT_ID;
  if (!token || !chatId) return;
  let pngBuffer;
  try {
    pngBuffer = await QRCode.toBuffer(qr, { scale: 8 });
  } catch (err) {
    log('warning', `[qr] Could not render QR image: ${err.message}`);
    return;
  }
  if (lastQrMessageId && (await editQrInTelegram(token, chatId, pngBuffer))) return;
  await sendNewQrToTelegram(token, chatId, pngBuffer);
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
  clearQrMessageId();
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
  // Pin a WhatsApp Web version served via request interception. Without this,
  // whatsapp-web.js scrapes the live page to auto-detect the version, calling
  // `res.text()` in a response listener that has no try/catch. On certain
  // navigations that throws "Could not load response body", which becomes an
  // unhandled rejection and crashes the whole process. Serving a cached HTML
  // takes the request-interception branch and never reads the response body,
  // eliminating the crash and the WA-version-drift fragility.
  //
  // The build is auto-resolved at startup (see resolveWebVersion below) so the
  // pin can't rot into a forced device-unlink; this fallback is only used before
  // that resolution completes and if the fetch fails.
  webVersionCache: {
    type: 'remote',
    remotePath: webVersion.remotePath(webVersion.FALLBACK_BUILD),
  },
  puppeteer: {
    args: ['--no-sandbox'],
    protocolTimeout: 120000, // 120s — getChats() on large accounts can be slow
  },
});

client.on('qr', (qr) => {
  // Always refresh the Telegram QR so the owner can scan the freshest valid code
  // from their phone. WhatsApp regenerates it every ~20s; this edits the one
  // persisted message in place rather than sending a new photo each time.
  sendQrToTelegram(qr);

  if (!qrPrinted) {
    // First QR — print to the terminal, then wait for a scan.
    qrPrinted = true;
    log('info', 'Scan this QR code with WhatsApp:');
    qrcode.generate(qr, { small: true });
    return;
  }

  // Subsequent QRs in the terminal: store the latest and ask before printing, to
  // avoid flooding the terminal with a new code every ~20s.
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

// Work around a whatsapp-web.js bug against current WhatsApp Web builds.
//
// The injected WWebJS.getChatModel() resolves a chat's last message via
// Msg.getMessagesById([chat.lastReceivedKey._serialized]). On current builds
// lastReceivedKey is present but its _serialized field is undefined, so that
// call reaches IndexedDB with no key and throws
//   DataError: Failed to execute 'get' on 'IDBObjectStore': No key or key range specified.
// Measured on this account: 485 of 545 chats threw. getChats() wraps every chat
// in Promise.all, so a single failure rejects the whole call — which is why
// both getChats() and getChatById() failed with a minified "r" for every group.
//
// Guarding the shared collection method fixes every caller at once. Filtering
// unusable ids can only prevent the crash: an undefined id could never have
// matched a record. Returns the same {messages} shape the callers destructure.
// The listener never reads chat.lastMessage, so losing it costs nothing.
//
// Re-applied on every 'ready' because a reconnect reloads the page and rebuilds
// the Store. Remove once whatsapp-web.js ships a fix upstream.
async function patchLastMessageLookup() {
  try {
    await client.pupPage.evaluate(() => {
      const Msg = window.require('WAWebCollections').Msg;
      if (Msg.__guardedGetMessagesById) return;
      const original = Msg.getMessagesById.bind(Msg);
      Msg.getMessagesById = async (ids, ...rest) => {
        const usable = (ids || []).filter((id) => typeof id === 'string' && id.length > 0);
        if (usable.length === 0) return { messages: [] };
        return original(usable, ...rest);
      };
      Msg.__guardedGetMessagesById = true;
    });
  } catch (err) {
    log('warning', `[store] Could not apply lastMessage guard: ${err.message}`);
  }
}

// Fetch every WhatsApp group on the account, write a snapshot to
// all_whatsapp_groups.json so the Telegram bot can serve /listgroups without
// requiring the listener to be in discovery mode, and return a map of
// group id -> Chat object so callers can reuse it instead of calling
// client.getChatById() again per group.
async function saveAllGroups() {
  let chats;
  try {
    chats = await client.getChats();
  } catch (err) {
    log('warning', `[groups] Could not fetch all groups for snapshot: ${err.message}`);
    return new Map();
  }
  const groups = chats.filter((c) => c.isGroup);
  const snapshot = {};
  const chatMap = new Map();
  groups.forEach((g) => {
    snapshot[g.id._serialized] = g.name;
    chatMap.set(g.id._serialized, g);
  });
  try {
    fs.writeFileSync(ALL_GROUPS_FILE, JSON.stringify(snapshot, null, 2));
  } catch (err) {
    log('warning', `[groups] Could not write all_whatsapp_groups.json: ${err.message}`);
  }
  return chatMap;
}

client.on('ready', async () => {
  isReady = true;
  resetFailCount(); // successful init — clear any accumulated failure count
  await resolveQrMessage();
  // Must run before any getChats()/getChatById() call — see patchLastMessageLookup.
  await patchLastMessageLookup();
  // Snapshot the groups list once at startup — used for catch-up only.
  // Live message filtering re-reads groups.json each time, so /addgroup
  // and /removegroup take effect without a restart.
  const watchedGroups = loadGroups();

  // Always write a full snapshot of all groups so /listgroups in the Telegram
  // bot has up-to-date data regardless of which groups are being watched.
  // Reuse the returned chat map for name resolution and catch-up below,
  // since getChatById() is unreliable this soon after 'ready'.
  const chatMap = await saveAllGroups();

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
  await saveGroupNames(watchedGroups, chatMap);

  // Snapshot timestamps before the loop so that live messages arriving during
  // catch-up can't advance a group's cursor and cause older messages to be missed.
  const snapshot = lastSeen.load();
  for (const groupId of watchedGroups) {
    await catchUp(groupId, snapshot[groupId] || 0, chatMap);
  }
  log('info', `Ready — watching ${watchedGroups.length} group(s)`);
});

client.on('auth_failure', (m) => log('error', `auth_failure: ${m}`));

client.on('disconnected', async (reason) => {
  isReady = false;
  // A LOGOUT/UNPAIRED reason means WhatsApp unlinked this device: the saved
  // session is dead, so reconnecting with it just loops back to a QR forever.
  // Wipe it first so the reconnect shows a fresh QR cleanly. Transient drops
  // (network blip, PC sleep — e.g. CONFLICT/NAVIGATION) keep the session and
  // simply reconnect.
  const loggedOut = /LOGOUT|UNPAIRED/i.test(String(reason));
  log('warning', `Disconnected (${reason}). ${loggedOut ? 'Logged out — clearing dead session. ' : ''}Reconnecting in 10s...`);
  if (loggedOut) {
    await destroyBrowser();
    await clearSession();
  }
  setTimeout(() => {
    log('info', 'Reconnecting...');
    reconnect();
  }, 10_000);
});

client.on('message', async (msg) => {
  try {
    // Derive the chat from the already-serialized message instead of calling
    // msg.getChat(). getChat() is a Puppeteer/CDP round-trip, and under catch-up
    // load it contends for the single CDP connection and times out
    // ("Runtime.callFunctionOn timed out"). For a received group message,
    // msg.from is the group's serialized id (the sender is msg.author).
    const groupId = msg.from;
    if (!groupId.endsWith('@g.us')) return; // not a group message

    // Re-read groups.json on every message so /addgroup and /removegroup take
    // effect immediately without restarting the listener.
    const groupMap = loadGroupMap();
    if (!(groupId in groupMap)) return;

    // Fall back to the id if the display name hasn't been resolved yet.
    await forwardMessage(msg, groupMap[groupId] || groupId);
    lastSeen.update(groupId, msg.timestamp);
  } catch (err) {
    log('error', `Failed to forward message: ${err.message}`);
  }
});

// Fetch the current WhatsApp Web build list and pick a recent-but-not-newest
// build, falling back to the pinned default if the network is unavailable.
// Runs once at startup; reconnect() reuses the resolved remotePath so we don't
// hammer GitHub on every reconnect.
async function resolveWebVersion() {
  try {
    const res = await axios.get(webVersion.LIST_URL, {
      headers: { Accept: 'application/vnd.github+json' },
      timeout: 10000,
    });
    return webVersion.pickBuild(res.data.map((file) => file.name));
  } catch (err) {
    log('warning', `[version] Could not fetch build list (${err.message}) — using fallback ${webVersion.FALLBACK_BUILD}.`);
    return webVersion.FALLBACK_BUILD;
  }
}

// Bootstrap: resolve the build before the first initialize(), then start.
// Catch transient startup failures (e.g. "execution context was destroyed"
// when WhatsApp Web navigates during Puppeteer script injection). Exiting with
// code 1 lets start.py restart the listener automatically; it usually succeeds
// on the next attempt. After MAX_INIT_FAILURES consecutive failures the stale
// session is wiped so the next restart prompts a fresh QR scan instead of looping.
(async () => {
  const build = await resolveWebVersion();
  client.options.webVersionCache.remotePath = webVersion.remotePath(build);
  log('info', `[version] Pinning WhatsApp Web build ${build}`);
  client.initialize().catch(async (err) => {
    log('error', `[init] initialize() failed — exiting for restart: ${err.message}`);
    await recordInitFailure();
    process.exit(1);
  });
})();

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
