/**
 * WhatsApp listener — forwards every message from watched groups to the
 * FastAPI ingest endpoint.
 *
 * IMPORTANT: Use a *spare* WhatsApp number. WhatsApp may flag automation
 * on your primary number.
 *
 *   1.  npm install
 *   2.  node listener/listener.js
 *   3.  Scan the QR code with WhatsApp on your phone.
 */

'use strict';

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const axios = require('axios');

// --- config ----------------------------------------------------------------

// Comma-separated list of group IDs (e.g. 120363XXXXXXXXXX@g.us).
// Group IDs never change even if the group is renamed.
// Run the listener once to see all your groups and their IDs printed to the console.
const WATCHED_GROUPS = (process.env.WATCHED_GROUPS || '')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

const API_URL = process.env.INGEST_API_URL || 'http://localhost:8000/ingest';

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
  if (WATCHED_GROUPS.length > 0) {
    console.log(`\nConnected. Watching: ${WATCHED_GROUPS.join(', ')}\n`);
    return;
  }

  // WATCHED_GROUPS is empty — list all groups so the user can pick IDs.
  console.log('\nConnected. WATCHED_GROUPS is empty — fetching your groups...\n');
  try {
    const chats = await client.getChats();
    const groups = chats.filter((c) => c.isGroup);
    console.log(`Found ${groups.length} groups:\n`);
    groups.forEach((g) => console.log(`  ${g.id._serialized}  —  ${g.name}`));
    console.log('\nCopy the IDs you want into WATCHED_GROUPS in your .env file, then restart.\n');
  } catch (err) {
    console.error('Could not fetch groups:', err.message);
    console.log('Try setting protocolTimeout higher or restart the listener.\n');
  }
});

client.on('auth_failure', (m) => console.error('auth_failure:', m));
client.on('disconnected', (r) => console.warn('disconnected:', r));

client.on('message', async (msg) => {
  try {
    const chat = await msg.getChat();
    if (!chat.isGroup) return;
    if (!WATCHED_GROUPS.includes(chat.id._serialized)) return;

    await axios.post(API_URL, {
      group: chat.name,
      sender: msg.from,
      text: msg.body,
      timestamp: msg.timestamp,
    });
  } catch (err) {
    console.error('Failed to forward message:', err.message);
  }
});

client.initialize();
