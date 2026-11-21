'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { Logins } = require('../logins')
const { Store } = require('../store')

const ORIGIN = 'https://server.example.ts.net'

function fakeKeyring(open = true) {
  return { open, available() { return this.open }, encrypt: text => Buffer.from(`sealed:${text}`), decrypt: data => data.toString().replace(/^sealed:/, '') }
}

function setup(open = true) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'xrnhub-desktop-'))
  const keyring = fakeKeyring(open)
  const store = new Store(dir, keyring)
  return { dir, keyring, store, logins: new Logins(store) }
}

function recorder() {
  const calls = []
  const callback = (...args) => calls.push(args)
  return { calls, callback }
}

test('a kept password answers the box by itself', () => {
  const { store, logins } = setup()
  store.keepLogin('sync', 'me', 'right')
  const box = recorder()
  assert.equal(logins.challenge(`${ORIGIN}/xrnhome/`, 'sync', 'xrnhome', box.callback), null)
  assert.deepEqual(box.calls, [['me', 'right']])
})

test('a kept password the server refuses is forgotten, and the box asks', () => {
  const { store, logins } = setup()
  store.keepLogin('sync', 'me', 'old')
  const first = recorder()
  logins.challenge(`${ORIGIN}/xrnhome/`, 'sync', 'xrnhome', first.callback, true)
  const second = recorder()
  const asked = logins.challenge(`${ORIGIN}/xrnhome/`, 'sync', 'xrnhome', second.callback, false)
  assert.deepEqual(asked, { url: `${ORIGIN}/xrnhome/`, group: 'sync', realm: 'xrnhome', retry: true })
  assert.equal(second.calls.length, 0, 'waits for the box')
  assert.equal(store.login('sync'), null)

  const { kept, next } = logins.answer('me', 'new')
  assert.equal(kept, true)
  assert.equal(next, null)
  assert.deepEqual(second.calls, [['me', 'new']])
  assert.deepEqual(store.login('sync'), { user: 'me', password: 'new' })

  // The same address asked again by a new request is a new visit, not a wrong password.
  const third = recorder()
  assert.equal(logins.challenge(`${ORIGIN}/xrnhome/`, 'sync', 'xrnhome', third.callback, true), null)
  assert.deepEqual(third.calls, [['me', 'new']])
})

test('two requests asking for the same address at once are both answered with the kept password', () => {
  const { store, logins } = setup()
  store.keepLogin('sync', 'me', 'right')
  const a = recorder()
  const b = recorder()
  assert.equal(logins.challenge(`${ORIGIN}/xrnhome/state`, 'sync', 'xrnhome', a.callback, true), null)
  assert.equal(logins.challenge(`${ORIGIN}/xrnhome/state`, 'sync', 'xrnhome', b.callback, true), null)
  assert.deepEqual(a.calls, [['me', 'right']])
  assert.deepEqual(b.calls, [['me', 'right']])
  assert.deepEqual(store.login('sync'), { user: 'me', password: 'right' }, 'still kept, so renewals go on')
})

test('one answer serves every request waiting on the same login, and the others wait their turn', () => {
  const { logins } = setup()
  const a = recorder()
  const b = recorder()
  const c = recorder()
  logins.challenge(`${ORIGIN}/xrnhome/`, 'sync', 'xrnhome', a.callback)
  logins.challenge(`${ORIGIN}/.web/x`, 'calendar', 'Radicale - Password Required', c.callback)
  logins.challenge(`${ORIGIN}/xrnhome/state`, 'sync', 'xrnhome', b.callback)
  const { next } = logins.answer('me', 'pw')
  assert.deepEqual(a.calls, [['me', 'pw']])
  assert.deepEqual(b.calls, [['me', 'pw']])
  assert.equal(c.calls.length, 0)
  assert.equal(next.group, 'calendar')
  assert.equal(logins.cancel(), null)
  assert.deepEqual(c.calls, [[]], 'closing the box sends no password')
})

test('an unknown realm is asked every time and never kept', () => {
  const { store, logins } = setup()
  const box = recorder()
  const asked = logins.challenge(`${ORIGIN}/other/`, null, 'Other', box.callback)
  assert.equal(asked.group, null)
  assert.equal(logins.answer('me', 'pw').kept, false)
  assert.deepEqual(store.secrets, {})
})

test('without a keyring nothing is kept, and the box still works', () => {
  const { dir, store, logins } = setup(false)
  const box = recorder()
  logins.challenge(`${ORIGIN}/xrnhome/`, 'sync', 'xrnhome', box.callback)
  assert.equal(logins.answer('me', 'pw').kept, false)
  assert.deepEqual(box.calls, [['me', 'pw']])
  assert.equal(fs.existsSync(path.join(dir, 'secrets.json')), false)
})

test('a keyring that is locked now hides the kept password rather than failing', () => {
  const { keyring, store, logins } = setup()
  store.keepLogin('sync', 'me', 'pw')
  keyring.open = false
  const box = recorder()
  assert.ok(logins.challenge(`${ORIGIN}/xrnhome/`, 'sync', 'xrnhome', box.callback), 'asks')
  keyring.open = true
  assert.deepEqual(store.login('sync'), { user: 'me', password: 'pw' }, 'still there once it opens')
})

test('signing out answers nothing that waits and forgets what was answered', () => {
  const { logins } = setup()
  const box = recorder()
  logins.challenge(`${ORIGIN}/xrnhome/`, 'sync', 'xrnhome', box.callback)
  logins.reset()
  assert.deepEqual(box.calls, [[]])
  assert.equal(logins.question(), null)
})

test('settings and secrets survive a restart, and the secrets file is the user\'s alone', () => {
  const { dir, keyring, store } = setup()
  store.set({ address: ORIGIN, page: 'review' })
  store.keepLogin('calendar', 'me', 'cal')
  const again = new Store(dir, keyring)
  assert.equal(again.get('address'), ORIGIN)
  assert.equal(again.get('page'), 'review')
  assert.deepEqual(again.login('calendar'), { user: 'me', password: 'cal' })
  assert.equal(fs.statSync(path.join(dir, 'secrets.json')).mode & 0o777, 0o600)
  assert.ok(!fs.readFileSync(path.join(dir, 'secrets.json'), 'utf8').includes('cal"'), 'sealed, not written out')
  again.forgetAll()
  assert.equal(new Store(dir, keyring).login('calendar'), null)
  store.set({ page: null })
  assert.equal(new Store(dir, keyring).get('page'), null)
})

test('a damaged settings file is read as empty, not a crash', () => {
  const { dir, keyring } = setup()
  fs.writeFileSync(path.join(dir, 'settings.json'), '{ not json')
  fs.writeFileSync(path.join(dir, 'secrets.json'), '[1, 2]')
  const store = new Store(dir, keyring)
  assert.equal(store.get('address'), null)
  assert.equal(store.login('sync'), null)
})
