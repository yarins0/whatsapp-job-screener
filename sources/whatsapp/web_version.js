'use strict';

/**
 * Auto-resolve which WhatsApp Web client build to pin.
 *
 * WhatsApp continuously deprecates old web client builds and force-unlinks any
 * device that reports one — which shows up as "getting thrown out" and a fresh
 * QR on the next start. A hand-set pin therefore rots every few weeks. Instead
 * of bumping it by hand, the listener fetches the current build list at startup
 * and picks a recent-but-not-newest build: the newest builds regularly ship
 * breaking changes to whatsapp-web.js, so a few releases back is the safer bet.
 *
 * The pure selection logic lives here (no network, no client) so it can be
 * unit-tested; the listener owns the actual HTTP fetch and wiring.
 */

// wppconnect-team/wa-version mirrors every WhatsApp Web build as an HTML file.
const RAW_BASE =
  'https://raw.githubusercontent.com/wppconnect-team/wa-version/main/html';
// GitHub contents API caps at 1000 files without pagination; the html dir holds
// ~285 today, so a single listing is fine. Revisit if it ever exceeds 1000.
const LIST_URL =
  'https://api.github.com/repos/wppconnect-team/wa-version/contents/html';

// Used when the build list can't be fetched (offline / GitHub rate-limited) so
// startup never depends on the network. Keep this a known-good recent build.
const FALLBACK_BUILD = '2.3000.1042448437-alpha';

// How many builds back from newest to select.
// ponytail: fixed-offset heuristic. If a fetched build fails to connect, raise it.
const OFFSET_FROM_NEWEST = 10;

// Sort key = the long numeric segment (e.g. 1040532093 in 2.3000.1040532093-alpha).
function buildNumber(build) {
  const digits = build.split('.').pop().replace(/\D/g, '');
  return parseInt(digits, 10) || 0;
}

// Pure: given the raw file-name list from wa-version/html, return the chosen
// build id (without the .html suffix). Falls back when the list is unusable.
function pickBuild(names, offset = OFFSET_FROM_NEWEST) {
  const builds = names
    .filter((name) => name.endsWith('.html'))
    .map((name) => name.replace(/\.html$/, ''))
    .sort((a, b) => buildNumber(a) - buildNumber(b)); // ascending → newest last
  if (builds.length === 0) return FALLBACK_BUILD;
  const index = Math.max(0, builds.length - 1 - offset);
  return builds[index];
}

function remotePath(build) {
  return `${RAW_BASE}/${build}.html`;
}

module.exports = {
  LIST_URL,
  FALLBACK_BUILD,
  OFFSET_FROM_NEWEST,
  buildNumber,
  pickBuild,
  remotePath,
};
