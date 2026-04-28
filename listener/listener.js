/**
 * WhatsApp listener — forwards every message from watched groups to the
 * FastAPI ingest endpoint.
 *
 * On reconnect (e.g. after the computer wakes from sleep) it replays any
 * messages received since the last time it was running, using timestamps
 * stored in listener/.last_seen.json.
 *
 *   1.  npm install
 *   2.  node listener/listener.js
 *   3.  Scan the QR code with WhatsApp on your phone.
 */

'use strict';

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const axios = require('axios');
const lastSeen = require('./last_seen');

// --- config ----------------------------------------------------------------

// Comma-separated list of group IDs (e.g. 120363XXXXXXXXXX@g.us).
// Group IDs never change even if the group is renamed.
// Run the listener once with this empty to print all group IDs.
const WATCHED_GROUPS = (process.env.WATCHED_GROUPS || '')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

const API_URL = process.env.INGEST_API_URL || 'http://localhost:8000/ingest';

// How many recent messages to fetch per group on reconnect.
const CATCHUP_LIMIT = 100;

// --- helpers ---------------------------------------------------------------

async function forwardMessage(msg, groupName) {
  if (!msg.body) return; // skip media-only messages
  await axios.post(API_URL, {
    group: groupName,
    sender: msg.from,
    text: msg.body,
    timestamp: msg.timestamp,
  });
}

async function catchUp(groupId) {
  const since = lastSeen.load()[groupId] || 0;

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
    console.error(`[catch-up] fetchMessages failed for ${chat.name}:`, err.message);
    return;
  }

  const missed = messages
    .filter((m) => m.timestamp > since && m.body)
    .reverse();

  if (missed.length > 0) {
    console.log(`[catch-up] ${chat.name}: replaying ${missed.length} missed message(s)`);
    for (const m of missed) {
      try {
        await forwardMessage(m, chat.name);
      } catch (err) {
        console.error(`[catch-up] Failed to forward message:`, err.message);
      }
    }
  } else {
    console.log(`[catch-up] ${chat.name}: no missed messages`);
  }

  // Advance the cursor to the newest message we just saw.
  if (messages.length > 0) {
    const latest = Math.max(...messages.map((m) => m.timestamp));
    lastSeen.update(groupId, latest);
  }
}

// --- client ----------------------------------------------------------------

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: 'listener/.wwebjs_auth' }),
  puppeteer: {
    args: ['--no-sandbox'],
    protocolTimeout: 60000, // 60s — getChats() on large accounts can be slow
  },
});

client.on('qr', (qr) => {
  console.log('Scan this QR code with WhatsApp:');
  qrcode.generate(qr, { small: true });
});

client.on('ready', async () => {
  if (WATCHED_GROUPS.length === 0) {
    // Discovery mode: list all groups so the user can pick IDs.
    console.log('\nConnected. WATCHED_GROUPS is empty — fetching your groups...\n');
    try {
      const chats = await client.getChats();
      const groups = chats.filter((c) => c.isGroup);
      console.log(`Found ${groups.length} groups:\n`);
      groups.forEach((g) => console.log(`  ${g.id._serialized}  —  ${g.name}`));
      console.log('\nCopy the IDs you want into WATCHED_GROUPS in your .env file, then restart.\n');
    } catch (err) {
      console.error('Could not fetch groups:', err.message);
    }
    return;
  }

  console.log(`\nConnected. Watching ${WATCHED_GROUPS.length} group(s). Checking for missed messages...\n`);
  for (const groupId of WATCHED_GROUPS) {
    await catchUp(groupId);
  }
  console.log('\nReady — listening for new messages.\n');
});

client.on('auth_failure', (m) => console.error('auth_failure:', m));

client.on('disconnected', (reason) => {
  console.warn(`Disconnected (${reason}). Reconnecting in 10s...`);
  setTimeout(() => {
    console.log('Reconnecting...');
    client.initialize();
  }, 10_000);
});

client.on('message', async (msg) => {
  try {
    const chat = await msg.getChat();
    if (!chat.isGroup) return;
    if (!WATCHED_GROUPS.includes(chat.id._serialized)) return;

    await forwardMessage(msg, chat.name);
    lastSeen.update(chat.id._serialized, msg.timestamp);
  } catch (err) {
    console.error('Failed to forward message:', err.message);
  }
});

client.initialize();
