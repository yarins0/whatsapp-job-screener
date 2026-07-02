'use strict';

const { pickBuild, buildNumber } = require('../web_version');

test('buildNumber extracts the long numeric segment', () => {
  expect(buildNumber('2.3000.1040532093-alpha')).toBe(1040532093);
});

test('pickBuild sorts numerically and picks offset from newest', () => {
  const names = [
    '2.3000.1040000000-alpha.html',
    '2.3000.1042000000-alpha.html', // newest
    '2.3000.1041000000-alpha.html',
    'not-a-build.txt', // ignored
  ];
  expect(pickBuild(names, 0)).toBe('2.3000.1042000000-alpha'); // newest
  expect(pickBuild(names, 1)).toBe('2.3000.1041000000-alpha'); // one back
  expect(pickBuild(names, 99)).toBe('2.3000.1040000000-alpha'); // clamps to oldest
});

test('pickBuild falls back when nothing usable', () => {
  expect(pickBuild([])).toBe('2.3000.1042448437-alpha');
});
