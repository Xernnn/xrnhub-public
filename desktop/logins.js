'use strict'
// The server's password boxes. Most pages sign in with the shared session cookie and never ask,
// but xrnhome's laptop version (and Radicale, and any page's address opened directly) answer with
// a Basic password challenge. A browser would show its own box; Electron leaves it to the app.
//
// A kept password answers by itself. If the server refuses it, Chromium asks again for that same
// request, no longer its first attempt: the kept one was wrong (it was changed on the server), so it
// is forgotten and the box asks. Two requests asking at once are each a first attempt, and each is
// answered with the kept one.

class Logins {
  constructor(store) {
    this.store = store
    this.waiting = [] // { url, group, realm, callback, retry }
  }

  /**
   * The server at `url` asks for a password. `group` is the kept login that answers it ('sync',
   * 'calendar', or null for a realm the app doesn't know); `first` is false when this request's
   * answer was refused. Returns the question to show, or null when it was answered already.
   */
  challenge(url, group, realm, callback, first = true) {
    const again = !first
    if (group && !again) {
      const kept = this.store.login(group)
      if (kept) {
        callback(kept.user, kept.password)
        return null
      }
    }
    if (again && group) this.store.forgetLogin(group)
    this.waiting.push({ url, group, realm: String(realm ?? ''), callback, retry: again })
    return this.question()
  }

  /** What the box is asking now: the first one still waiting. */
  question() {
    const first = this.waiting[0]
    return first ? { url: first.url, group: first.group, realm: first.realm, retry: first.retry } : null
  }

  /** Every request waiting on the same login as the first one. */
  take() {
    const first = this.waiting[0]
    if (!first) return []
    const same = item => (first.group ? item.group === first.group : !item.group && item.realm === first.realm)
    const taken = this.waiting.filter(same)
    this.waiting = this.waiting.filter(item => !same(item))
    return taken
  }

  /** The box was filled in. Returns whether the password was kept, and the next question. */
  answer(user, password) {
    const taken = this.take()
    const group = taken[0]?.group
    for (const item of taken) item.callback(user, password)
    const kept = group ? this.store.keepLogin(group, user, password) : false
    return { kept, next: this.question() }
  }

  /** The box was closed: those requests go on without a password, and the server refuses them. */
  cancel() {
    for (const item of this.take()) item.callback()
    return this.question()
  }

  /** Signing out: nothing waits. */
  reset() {
    for (const item of this.waiting) item.callback()
    this.waiting = []
  }
}

module.exports = { Logins }
