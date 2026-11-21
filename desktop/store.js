'use strict'
// What the app remembers between starts: the server's address and the last page in settings.json,
// and the passwords in secrets.json, each encrypted by the system keyring. Both sit in the app's own
// folder (~/.config/xrnhub), readable by this user only.

const fs = require('node:fs')
const path = require('node:path')

class Store {
  /**
   * `keyring` is { available(), encrypt(text) -> Buffer, decrypt(Buffer) -> text }: Electron's
   * safeStorage in the app, a stand-in in the tests.
   */
  constructor(dir, keyring) {
    this.dir = dir
    this.keyring = keyring
    this.settings = this.read('settings.json')
    this.secrets = this.read('secrets.json')
  }

  read(name) {
    try {
      const doc = JSON.parse(fs.readFileSync(path.join(this.dir, name), 'utf8'))
      return doc && typeof doc === 'object' && !Array.isArray(doc) ? doc : {}
    } catch {
      return {}
    }
  }

  /** Written beside and renamed over, so a crash mid-write leaves the old file, not half of one. */
  write(name, doc) {
    fs.mkdirSync(this.dir, { recursive: true, mode: 0o700 })
    const file = path.join(this.dir, name)
    const temp = `${file}.${process.pid}.tmp`
    fs.writeFileSync(temp, `${JSON.stringify(doc, null, 2)}\n`, { mode: 0o600 })
    fs.renameSync(temp, file)
  }

  get(key) {
    return this.settings[key] ?? null
  }

  set(changes) {
    this.settings = { ...this.settings, ...changes }
    for (const [key, value] of Object.entries(this.settings)) if (value === null || value === undefined) delete this.settings[key]
    this.write('settings.json', this.settings)
  }

  /** Whether a password can be kept: never without a keyring to encrypt it with. */
  canKeep() {
    try {
      return this.keyring.available()
    } catch {
      return false
    }
  }

  /** { user, password } for 'sync' or 'calendar', or null if none is kept or it can't be read now. */
  login(group) {
    const sealed = this.secrets[group]
    if (typeof sealed !== 'string' || !this.canKeep()) return null
    try {
      const doc = JSON.parse(this.keyring.decrypt(Buffer.from(sealed, 'base64')))
      return typeof doc.user === 'string' && typeof doc.password === 'string' ? { user: doc.user, password: doc.password } : null
    } catch {
      return null
    }
  }

  /** Keeps it if the keyring is there, and says whether it did. */
  keepLogin(group, user, password) {
    if (!this.canKeep()) return false
    const sealed = this.keyring.encrypt(JSON.stringify({ user, password })).toString('base64')
    this.secrets = { ...this.secrets, [group]: sealed }
    this.write('secrets.json', this.secrets)
    return true
  }

  forgetLogin(group) {
    if (!(group in this.secrets)) return
    const { [group]: _gone, ...rest } = this.secrets
    this.secrets = rest
    this.write('secrets.json', this.secrets)
  }

  forgetAll() {
    this.secrets = {}
    this.write('secrets.json', this.secrets)
  }
}

module.exports = { Store }
