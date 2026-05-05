/**
 * List all WhatsApp groups — terminal discovery tool.
 *
 * Connects using the saved WhatsApp session (no QR scan needed as long as
 * you have authenticated at least once via listener.js), prints every group
 * as "  <id>  —  <name>", then exits.
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

const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

// U+200E LEFT-TO-RIGHT MARK — keeps Hebrew names readable in LTR terminals.
const LRM = '\u200E';
const ltr = (str) => `${LRM}${str}${LRM}`;

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: 'sources/whatsapp/.wwebjs_auth' }),
  userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  puppeteer: { args: ['--no-sandbox'], protocolTimeout: 60000 },
});

client.on('qr', (qr) => {
  // Session expired — need to re-authenticate via listener.js first.
  console.log('\nNo saved session found. Run listener.js first to scan the QR code.\n');
  qrcode.generate(qr, { small: true });
});

client.on('ready', async () => {
  console.log('\nFetching your WhatsApp groups...\n');

  let chats;
  try {
    chats = await client.getChats();
  } catch (err) {
    console.error('Could not fetch chats:', err.message);
    process.exit(1);
  }

  const groups = chats.filter((c) => c.isGroup);

  if (groups.length === 0) {
    console.log('No groups found on this account.');
  } else {
    console.log(`Found ${groups.length} group(s):\n`);
    groups.forEach((g) => {
      console.log(`  ${g.id._serialized}  —  ${ltr(g.name)}`);
    });
    console.log('\nTo start watching a group:');
    console.log('  Telegram: /addgroup <id>');
    console.log('  Direct:   add the id to agent/whatsapp_sources.json\n');
  }

  await client.destroy();
  process.exit(0);
});

client.on('auth_failure', (msg) => {
  console.error('Authentication failed:', msg);
  process.exit(1);
});

client.initialize().catch((err) => {
  console.error('initialize() failed:', err.message);
  process.exit(1);
});
