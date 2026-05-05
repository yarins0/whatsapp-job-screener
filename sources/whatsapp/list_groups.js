/**
 * List all WhatsApp groups — terminal discovery tool.
 *
 * When the WhatsApp listener is running it writes agent/all_whatsapp_groups.json
 * on every connect. This script reads that snapshot first (instant, no browser
 * conflict) and only falls back to connecting directly when the file doesn't
 * exist (i.e. the listener has never been started).
 *
 * Usage:
 *   node sources/whatsapp/list_groups.js
 *
 * Copy an ID from the output and use it with:
 *   /addgroup <id>    (Telegram bot)
 *   — or —
 *   edit agent/whatsapp_sources.json directly
 */

'use strict';

const fs   = require('fs');
const path = require('path');

// U+200E LEFT-TO-RIGHT MARK — keeps Hebrew names readable in LTR terminals.
const LRM = '\u200E';
const ltr  = (str) => `${LRM}${str}${LRM}`;

const SNAPSHOT_FILE    = path.join(__dirname, '..', '..', 'agent', 'all_whatsapp_groups.json');
const WATCHED_FILE     = path.join(__dirname, '..', '..', 'agent', 'whatsapp_sources.json');

function printGroups(groups, watched) {
  const ids = Object.keys(groups);
  if (ids.length === 0) {
    console.log('No groups found.');
    return;
  }
  console.log(`\nFound ${ids.length} group(s):\n`);
  ids.forEach((id) => {
    const name    = groups[id] || 'unknown';
    const marker  = watched.has(id) ? '  [watching]' : '';
    console.log(`  ${ltr(name)}${marker}`);
    console.log(`  ${id}\n`);
  });
  console.log('To start watching a group:');
  console.log('  Telegram: /addgroup <id>');
  console.log('  Direct:   add the id to agent/whatsapp_sources.json\n');
}

function loadWatched() {
  try {
    return new Set(Object.keys(JSON.parse(fs.readFileSync(WATCHED_FILE, 'utf8'))));
  } catch (_) {
    return new Set();
  }
}

// --- Primary path: read the snapshot written by listener.js ----------------

if (fs.existsSync(SNAPSHOT_FILE)) {
  try {
    const groups  = JSON.parse(fs.readFileSync(SNAPSHOT_FILE, 'utf8'));
    const watched = loadWatched();
    console.log('(Reading cached snapshot from all_whatsapp_groups.json)');
    printGroups(groups, watched);
  } catch (err) {
    console.error('Could not read snapshot:', err.message);
    process.exit(1);
  }
  process.exit(0);
}

// --- Fallback: connect directly (only works when listener is NOT running) ---

console.log('No snapshot found — connecting to WhatsApp directly...');
console.log('(Stop the listener first if it is running, or start it once to create the snapshot.)\n');

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: 'sources/whatsapp/.wwebjs_auth' }),
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  puppeteer: { args: ['--no-sandbox'], protocolTimeout: 60000 },
});

client.on('qr', (qr) => {
  console.log('No saved session found. Scan this QR code, or run listener.js first.\n');
  qrcode.generate(qr, { small: true });
});

client.on('ready', async () => {
  let chats;
  try {
    chats = await client.getChats();
  } catch (err) {
    console.error('Could not fetch chats:', err.message);
    await client.destroy();
    process.exit(1);
  }

  const groups  = {};
  chats.filter((c) => c.isGroup).forEach((g) => { groups[g.id._serialized] = g.name; });
  const watched = loadWatched();
  printGroups(groups, watched);

  await client.destroy();
  process.exit(0);
});

client.on('auth_failure', (msg) => {
  console.error('Authentication failed:', msg);
  process.exit(1);
});

client.initialize().catch((err) => {
  console.error('initialize() failed:', err.message);
  console.error('If the listener is running, stop it first — Chrome cannot share a session.');
  process.exit(1);
});
