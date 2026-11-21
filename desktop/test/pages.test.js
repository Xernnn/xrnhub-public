'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const pages = require('../pages')

const ORIGIN = 'https://server.example.ts.net'

test('an address is the server\'s origin, https unless it is this computer', () => {
  assert.deepEqual(pages.normaliseAddress('server.example.ts.net'), { origin: ORIGIN })
  assert.deepEqual(pages.normaliseAddress('  https://server.example.ts.net/xrnhub/#today '), { origin: ORIGIN })
  assert.deepEqual(pages.normaliseAddress('http://127.0.0.1:15238/xrnhub'), { origin: 'http://127.0.0.1:15238' })
  assert.deepEqual(pages.normaliseAddress('http://localhost:5238'), { origin: 'http://localhost:5238' })
  assert.match(pages.normaliseAddress('http://server.example.ts.net').error, /https/)
  assert.match(pages.normaliseAddress('https://me:secret@server.example.ts.net').error, /password out/)
  assert.match(pages.normaliseAddress('ftp://server.example.ts.net').error, /web address/)
  assert.match(pages.normaliseAddress('').error, /Type/)
  assert.match(pages.normaliseAddress('https://exa mple').error, /isn't an address/)
})

test('each page starts at its own path, the hub\'s at its tab', () => {
  assert.equal(pages.pageUrl(ORIGIN, pages.pageById('today')), `${ORIGIN}/xrnhub/#today`)
  assert.equal(pages.pageUrl(ORIGIN, pages.pageById('habits')), `${ORIGIN}/xrnloops/`)
  // xrnimg and xrnnote were xrnphotos and xrnnotes until 2026-11-21; their pages keep their ids.
  assert.equal(pages.pageUrl(ORIGIN, pages.pageById('photos')), `${ORIGIN}/xrnimg/`)
  assert.equal(pages.pageUrl(ORIGIN, pages.pageById('notes')), `${ORIGIN}/xrnnote/`)
  assert.deepEqual([pages.pageById('photos').service, pages.pageById('notes').service], ['xrnimg', 'xrnnote'])
  assert.equal(pages.pageById('calendars'), null, 'Radicale\'s page is left out')
  const ids = pages.PAGES.map(page => page.id)
  assert.equal(new Set(ids).size, ids.length)
  assert.deepEqual(pages.PAGES.slice(0, 5).map(page => page.id), ['server', 'today', 'review', 'apps', 'habits'], 'the hub\'s own, first, in its order')
})

test('each app page\'s logo is one of the hub\'s', () => {
  const web = path.join(__dirname, '..', '..', 'web', 'apps')
  for (const page of pages.PAGES.filter(p => p.group === 'apps')) {
    assert.ok(fs.existsSync(path.join(web, page.icon)), page.icon)
  }
})

test('the hub\'s Nudges tab is gone, and what named it lands on Today', () => {
  assert.equal(pages.pageById('nudges'), null)
  assert.ok(!pages.PAGES.some(page => page.hash === 'nudges' || page.icon === 'nudges'))
  // An old #nudges address, a profile whose last page was Nudges, and --page=nudges.
  assert.equal(pages.pageFor(`${ORIGIN}/xrnhub/#nudges`, ORIGIN, 'review'), 'today')
  assert.equal(pages.currentId('nudges'), 'today')
  assert.equal(pages.pageFromArgs(['/app', '--page=nudges']), 'today')
  assert.equal(pages.currentId('review'), 'review')
  assert.equal(pages.currentId('nope'), null)
  assert.equal(pages.currentId(null), null)
  assert.equal(pages.currentId('constructor'), null)
})

test('an address belongs to the page whose path it is under', () => {
  assert.equal(pages.viewOf(`${ORIGIN}/xrnhub/#review`, ORIGIN), 'hub')
  assert.equal(pages.viewOf(`${ORIGIN}/xrnhub`, ORIGIN), 'hub')
  assert.equal(pages.viewOf(`${ORIGIN}/xrnloops/index.html?x=1`, ORIGIN), 'habits')
  assert.equal(pages.viewOf(`${ORIGIN}/.web`, ORIGIN), null, 'no page\'s own: a link there opens where it is')
  assert.equal(pages.viewOf(`${ORIGIN}/xrnhubby/`, ORIGIN), null, 'a longer name is another path')
  assert.equal(pages.viewOf(`${ORIGIN}/xrnwal/`, ORIGIN), null)
  assert.equal(pages.viewOf('https://elsewhere.example/xrnhub/', ORIGIN), null)
})

test('the hub\'s #hash says which of its tabs is showing', () => {
  assert.equal(pages.pageFor(`${ORIGIN}/xrnhub/`, ORIGIN, 'apps'), 'server')
  assert.equal(pages.pageFor(`${ORIGIN}/xrnhub/#server/traffic`, ORIGIN, 'server'), 'server')
  assert.equal(pages.pageFor(`${ORIGIN}/xrnhub/#today/2026-11-01`, ORIGIN, 'server'), 'today')
  assert.equal(pages.pageFor(`${ORIGIN}/xrnhub/#apps`, ORIGIN, 'server'), 'apps')
  // A #hash that is no tab keeps the one that was showing.
  assert.equal(pages.pageFor(`${ORIGIN}/xrnhub/#elsewhere`, ORIGIN, 'today'), 'today')
  assert.equal(pages.pageFor(`${ORIGIN}/xrnhub/#elsewhere`, ORIGIN, 'habits'), 'server')
  assert.equal(pages.pageFor(`${ORIGIN}/xrnrule/`, ORIGIN, 'today'), 'blocks')
  assert.equal(pages.pageFor(`${ORIGIN}/xrntube/`, ORIGIN, 'today'), null)
})

test('a link to another page switches to it; another site opens outside; the rest is refused', () => {
  assert.deepEqual(pages.route(`${ORIGIN}/xrnhub/#review`, ORIGIN, 'hub'), { action: 'stay' })
  assert.deepEqual(pages.route(`${ORIGIN}/xrnloops/`, ORIGIN, 'hub'), { action: 'switch', page: 'habits', url: `${ORIGIN}/xrnloops/` })
  assert.deepEqual(pages.route(`${ORIGIN}/xrnhub/#today`, ORIGIN, 'habits'), { action: 'switch', page: 'today', url: `${ORIGIN}/xrnhub/#today` })
  assert.deepEqual(pages.route(`${ORIGIN}/xrntube/update/xrntube.crx`, ORIGIN, 'hub'), { action: 'stay' })
  assert.deepEqual(pages.route('https://github.com/someone/xrnhub', ORIGIN, 'hub'), { action: 'outside', url: 'https://github.com/someone/xrnhub' })
  assert.deepEqual(pages.route('mailto:someone@example.com', ORIGIN, 'hub'), { action: 'outside', url: 'mailto:someone@example.com' })
  assert.deepEqual(pages.route('http://server.example.ts.net/xrnhub/', ORIGIN, 'hub'), { action: 'outside', url: 'http://server.example.ts.net/xrnhub/' })
  for (const url of ['file:///etc/passwd', 'javascript:alert(1)', 'smb://example.invalid/share', 'data:text/html,hi', 'chrome://gpu', 'not a url']) {
    assert.deepEqual(pages.route(url, ORIGIN, 'hub'), { action: 'block' }, url)
  }
})

test('a view hands at most one address a second to the usual browser', () => {
  let now = 1_000_000
  const gate = new pages.OutsideGate(() => now)
  assert.equal(gate.allow('hub'), true)
  for (let i = 0; i < 50; i++) assert.equal(gate.allow('hub'), false, 'a burst opens one tab')
  assert.equal(gate.allow('insta'), true, 'each view has its own')
  now += pages.OUTSIDE_GAP_MS - 1
  assert.equal(gate.allow('hub'), false)
  now += 1
  assert.equal(gate.allow('hub'), true)
  assert.equal(gate.allow('hub'), false)
})

test('a page\'s own Sign out is told from a page signing out because its session was refused', () => {
  let now = 1_000_000
  const watch = new pages.PageSignOut(() => now)
  assert.equal(watch.heard(1, 'DELETE', `${ORIGIN}/xrnhub/api/session`, 200, ORIGIN), true, 'the hub\'s Sign out')
  assert.equal(watch.heard(2, 'DELETE', `${ORIGIN}/xrnloops/api/session`, 200, ORIGIN), true, 'Habits\' Sign out')
  assert.equal(watch.heard(3, 'DELETE', `${ORIGIN}/xrnrule/session`, 200, ORIGIN), true, 'Blocks\' Sign out')
  // Refused first, then signed out: the page is reacting to the refusal, which the kept password mends.
  assert.equal(watch.heard(2, 'GET', `${ORIGIN}/xrnloops/api/changes`, 401, ORIGIN), false)
  now += 2000
  assert.equal(watch.heard(2, 'DELETE', `${ORIGIN}/xrnloops/api/session`, 200, ORIGIN), false)
  assert.equal(watch.heard(1, 'DELETE', `${ORIGIN}/xrnhub/api/session`, 200, ORIGIN), true, 'another page\'s refusal is its own')
  now += pages.REFUSED_WITHIN_MS
  assert.equal(watch.heard(2, 'DELETE', `${ORIGIN}/xrnloops/api/session`, 200, ORIGIN), true, 'long after, it is the user again')
  assert.equal(watch.heard(1, 'DELETE', `${ORIGIN}/xrnhub/api/session`, 502, ORIGIN), false, 'a sign-out that failed')
  assert.equal(watch.heard(1, 'POST', `${ORIGIN}/xrnhub/api/session`, 200, ORIGIN), false, 'signing in')
  assert.equal(watch.heard(1, 'DELETE', `${ORIGIN}/xrnhub/api/rules`, 200, ORIGIN), false)
  assert.equal(watch.heard(1, 'DELETE', 'https://elsewhere.example/xrnhub/api/session', 200, ORIGIN), false)
})

test('a request already on its way when a page signs out, refused because of it, doesn\'t undo the sign-out', () => {
  let now = 1_000_000
  const watch = new pages.PageSignOut(() => now)
  // The hub polls as its Sign out goes: the poll reaches the server just after the session has
  // ended, and its 401 gets back here just before the sign-out's own answer.
  watch.sending(1, 'GET', `${ORIGIN}/xrnhub/api/services`, ORIGIN)
  watch.sending(1, 'DELETE', `${ORIGIN}/xrnhub/api/session`, ORIGIN)
  now += 5
  assert.equal(watch.heard(1, 'GET', `${ORIGIN}/xrnhub/api/services`, 401, ORIGIN), false)
  assert.equal(watch.heard(1, 'DELETE', `${ORIGIN}/xrnhub/api/session`, 200, ORIGIN), true)
  // Refused before the page signed out: the page is still reacting to the refusal.
  assert.equal(watch.heard(2, 'GET', `${ORIGIN}/xrnloops/api/changes`, 401, ORIGIN), false)
  watch.sending(2, 'DELETE', `${ORIGIN}/xrnloops/api/session`, ORIGIN)
  assert.equal(watch.heard(2, 'GET', `${ORIGIN}/xrnloops/api/changes`, 401, ORIGIN), false)
  assert.equal(watch.heard(2, 'DELETE', `${ORIGIN}/xrnloops/api/session`, 200, ORIGIN), false)
  // A sign-out that never got an answer excuses nothing long after.
  watch.sending(3, 'DELETE', `${ORIGIN}/xrnrule/session`, ORIGIN)
  now += pages.REFUSED_WITHIN_MS
  assert.equal(watch.heard(3, 'GET', `${ORIGIN}/xrnrule/status`, 401, ORIGIN), false)
  watch.sending(3, 'DELETE', `${ORIGIN}/xrnrule/session`, ORIGIN)
  assert.equal(watch.heard(3, 'DELETE', `${ORIGIN}/xrnrule/session`, 200, ORIGIN), false)
})

test('only the server\'s pages get a permission, and only copying and full screen', () => {
  assert.equal(pages.allowedPermission('clipboard-sanitized-write', `${ORIGIN}/xrnhub/`, ORIGIN), true)
  assert.equal(pages.allowedPermission('fullscreen', ORIGIN, ORIGIN), true)
  for (const permission of ['notifications', 'media', 'geolocation', 'clipboard-read', 'openExternal', 'hid']) {
    assert.equal(pages.allowedPermission(permission, `${ORIGIN}/xrnhub/`, ORIGIN), false, permission)
  }
  assert.equal(pages.allowedPermission('clipboard-sanitized-write', 'https://elsewhere.example/', ORIGIN), false)
  assert.equal(pages.allowedPermission('clipboard-sanitized-write', '', ORIGIN), false)
})

test('a password box is answered only for the server itself, by the login its realm names', () => {
  const box = { isProxy: false, scheme: 'basic', host: 'server.example.ts.net', port: 443, realm: 'xrnhome' }
  assert.equal(pages.answersLogin(box, `${ORIGIN}/xrnhome/`, ORIGIN), true)
  assert.equal(pages.answersLogin({ ...box, isProxy: true }, `${ORIGIN}/xrnhome/`, ORIGIN), false)
  assert.equal(pages.answersLogin({ ...box, scheme: 'digest' }, `${ORIGIN}/xrnhome/`, ORIGIN), false)
  assert.equal(pages.answersLogin({ ...box, host: 'elsewhere.example' }, 'https://elsewhere.example/', ORIGIN), false)
  assert.equal(pages.answersLogin(box, 'http://server.example.ts.net/xrnhome/', ORIGIN), false, 'not over plain http')
  for (const realm of ['xrnhub', 'xrnloops', 'xrnrule', 'xrnwal', 'xrnhome', 'xrninsta', 'xrnimg', 'xrnnote']) assert.equal(pages.loginGroup(realm), 'sync', realm)
  assert.equal(pages.loginGroup('XrnTube'), 'calendar')
  assert.equal(pages.loginGroup('Radicale - Password Required'), 'calendar')
  assert.equal(pages.loginGroup('Something else'), null)
  assert.equal(pages.loginGroup(undefined), null)
})

test('the hub\'s service list says which pages\' servers are down', () => {
  assert.deepEqual(pages.readStatus(null), { reachable: false, down: [] })
  assert.deepEqual(pages.readStatus({}), { reachable: true, down: [] })
  const reply = { services: [{ name: 'calendar', up: false }, { name: 'xrnloops', up: true }, { name: 'xrnwal', up: false }, { name: 'xrnrule', up: false }] }
  assert.deepEqual(pages.readStatus(reply), { reachable: true, down: ['calendar', 'xrnwal', 'blocks'] })
})

test('a page comes to the front over Settings, never over setting up or a password box', () => {
  assert.equal(pages.takesPage(null), true)
  assert.equal(pages.takesPage('settings'), true)
  assert.equal(pages.takesPage('setup'), false)
  assert.equal(pages.takesPage('sign-in'), false)
})

test('--page= picks a page, and only a real one', () => {
  assert.equal(pages.pageFromArgs(['/app', '--page=review']), 'review')
  assert.equal(pages.pageFromArgs(['/app', '--page=nope']), null)
  assert.equal(pages.pageFromArgs(['/app']), null)
})
