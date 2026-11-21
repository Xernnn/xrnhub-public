'use strict'
// What the window holds, and which of the home server's addresses belong to which page. Nothing
// here touches Electron, so the tests can check every rule without a window.

/**
 * The sidebar, top to bottom. Pages with the same `view` share one page load: Server, Today,
 * Review and Apps are the hub's own tabs, so switching between them changes the hub's #hash and
 * keeps everything else it has open. `service` is the name the hub's /api/services gives the page's
 * server, so the sidebar can say when it is down.
 */
const PAGES = [
  { id: 'server', label: 'Server', group: 'hub', view: 'hub', path: '/xrnhub/', hash: 'server', icon: 'server' },
  { id: 'today', label: 'Today', group: 'hub', view: 'hub', path: '/xrnhub/', hash: 'today', icon: 'today' },
  { id: 'review', label: 'Review', group: 'hub', view: 'hub', path: '/xrnhub/', hash: 'review', icon: 'review' },
  { id: 'apps', label: 'Apps', group: 'hub', view: 'hub', path: '/xrnhub/', hash: 'apps', icon: 'apps' },
  { id: 'habits', label: 'Habits', group: 'apps', view: 'habits', path: '/xrnloops/', service: 'xrnloops', icon: 'xrnloops.svg' },
  { id: 'blocks', label: 'Blocks', group: 'apps', view: 'blocks', path: '/xrnrule/', service: 'xrnrule', icon: 'xrnrule.svg' },
  { id: 'home', label: 'Home', group: 'apps', view: 'home', path: '/xrnhome/', service: 'xrnhome', icon: 'xrnhome.svg' },
  { id: 'insta', label: 'Insta', group: 'apps', view: 'insta', path: '/xrninsta/', service: 'xrninsta', icon: 'xrninsta.svg' },
  { id: 'photos', label: 'Photos', group: 'apps', view: 'photos', path: '/xrnimg/', service: 'xrnimg', icon: 'xrnimg.svg' },
  { id: 'notes', label: 'Notes', group: 'apps', view: 'notes', path: '/xrnnote/', service: 'xrnnote', icon: 'xrnnote.svg' }
]
// Radicale's own page (/.web/) isn't here: it only makes and renames calendars, which xrncal does,
// and it asks for the calendar password every time, in a look of its own.

// Pages that went, and the one each old id or hub #hash lands on: the hub's Nudges tab (its rules are
// switched on Today's cards now), which a profile can still name as its last page.
const MOVED = { nudges: 'today' }

const HUB_TABS = new Set(PAGES.filter(page => page.view === 'hub').map(page => page.hash))
const LOOPBACK = new Set(['127.0.0.1', 'localhost', '[::1]'])
// The sync password's services, by the realm each names when it asks for a password. XrnTube and
// Radicale take the calendar password instead.
const SYNC_REALMS = new Set(['xrnhub', 'xrnloops', 'xrnrule', 'xrnwal', 'xrnhome', 'xrninsta', 'xrnimg', 'xrnnote'])
const PERMISSIONS = new Set(['clipboard-sanitized-write', 'fullscreen'])
const OUTSIDE_GAP_MS = 1000
const REFUSED_WITHIN_MS = 10_000

/**
 * Put into the hub's page in this window. Its header holds its tabs, its links to Habits and Blocks
 * and the count of services down, which the sidebar beside it shows already; on a phone-width
 * window it is a bar along the bottom. Only the look changes: its keys still work. The page keeps
 * clear of the sheet's rounded edges, at the top as much as at the sides.
 */
const HUB_CSS = '.frame > .top { display: none !important; } .frame { padding: 30px 28px 32px !important; }'

/**
 * Put into every page: slim rounded scrollbars with no track and no arrow buttons, instead of
 * Chromium's grey ones, which look like nothing else on this desktop. (The standard
 * scrollbar-width: thin still draws the buttons on Linux; these older rules don't.) A grey that
 * reads on light and dark pages alike.
 */
const PAGE_CSS = [
  '::-webkit-scrollbar { width: 10px; height: 10px; background: transparent; }',
  '::-webkit-scrollbar-thumb { border: 3px solid transparent; border-radius: 10px; background: rgb(128 128 128 / 0.42) padding-box; }',
  '::-webkit-scrollbar-thumb:hover { background-color: rgb(128 128 128 / 0.62); }',
  '::-webkit-scrollbar-button, ::-webkit-scrollbar-track, ::-webkit-scrollbar-corner { display: none; background: transparent; }'
].join(' ')

function pageById(id) {
  return PAGES.find(page => page.id === id) || null
}

/** A page's id as it is now, for one kept from before (the last page, `--page=`): null if none. */
function currentId(id) {
  const now = Object.hasOwn(MOVED, id) ? MOVED[id] : id
  return pageById(now) ? now : null
}

/**
 * The server's origin from what someone typed: `homeserver.example.ts.net` or a whole address.
 * It must be https, since the pages carry a password and a session cookie, except on this computer
 * itself, where the made-up server for trying things out speaks plain http.
 */
function normaliseAddress(typed) {
  const text = String(typed ?? '').trim()
  if (!text) return { error: 'Type the server\'s address' }
  let url
  try {
    url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `https://${text}`)
  } catch {
    return { error: 'That isn\'t an address' }
  }
  if (url.username || url.password) return { error: 'Leave the name and password out of the address' }
  if (url.protocol === 'http:' && !LOOPBACK.has(url.hostname)) {
    return { error: 'It has to be an https address' }
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') return { error: 'That isn\'t a web address' }
  return { origin: url.origin }
}

/** Where a page starts: the hub's tabs are its #hash. */
function pageUrl(origin, page) {
  return `${origin}${page.path}${page.hash ? `#${page.hash}` : ''}`
}

function parse(url) {
  try {
    return new URL(url)
  } catch {
    return null
  }
}

/** The view an address on the server belongs to, or null for one that is no page's own. */
function viewOf(url, origin) {
  const parsed = parse(url)
  if (!parsed || parsed.origin !== origin) return null
  const page = PAGES.find(p => parsed.pathname === p.path.slice(0, -1) || parsed.pathname.startsWith(p.path))
  return page ? page.view : null
}

/**
 * Which sidebar entry a page's address stands for. On the hub that is its tab (#today/2026-11-01
 * is Today); a #hash that is no tab keeps whichever was showing.
 */
function pageFor(url, origin, current) {
  const view = viewOf(url, origin)
  if (!view) return null
  if (view !== 'hub') return PAGES.find(page => page.view === view).id
  const asked = (parse(url).hash.slice(1).split('/')[0] || 'server').toLowerCase()
  const tab = Object.hasOwn(MOVED, asked) ? MOVED[asked] : asked
  if (HUB_TABS.has(tab)) return tab
  return pageById(current)?.view === 'hub' ? current : 'server'
}

/**
 * What to do when a page in `view` goes to `url`, by a link or by opening a window:
 *   stay     load it where it is: this view's own, or a server address no page owns
 *   switch   it is another page's (the hub's link to Habits): show that page, at that address
 *   outside  another site: the usual browser opens it
 *   block    anything else (file:, javascript:, smb:, ...)
 */
function route(url, origin, view) {
  const parsed = parse(url)
  if (!parsed) return { action: 'block' }
  if (parsed.origin === origin) {
    const target = viewOf(url, origin)
    if (!target || target === view) return { action: 'stay' }
    return { action: 'switch', page: pageFor(url, origin, null), url: parsed.href }
  }
  if (opensOutside(parsed.href)) return { action: 'outside', url: parsed.href }
  return { action: 'block' }
}

/** Only web and mail addresses are handed to the system; anything else could run a program. */
function opensOutside(url) {
  const parsed = parse(url)
  return !!parsed && ['https:', 'http:', 'mailto:'].includes(parsed.protocol)
}

/**
 * Whether a view may hand an address to the usual browser now: at most one a second from each, so a
 * page (or script injected into one) can't open a flood of tabs.
 */
class OutsideGate {
  constructor(now = Date.now) {
    this.now = now
    this.last = new Map() // view -> when it last opened something
  }

  allow(view) {
    const at = this.now()
    if (this.last.has(view) && at - this.last.get(view) < OUTSIDE_GAP_MS) return false
    this.last.set(view, at)
    return true
  }
}

/**
 * Whether a page has just signed out because the user asked: the hub's Sign out sends
 * DELETE /xrnhub/api/session, xrnloops' /xrnloops/api/session and xrnrule's /xrnrule/session. Those
 * pages also sign themselves out when the server refuses their session, and the kept password should
 * mend that, so a sign-out right after one of the page's own requests was refused doesn't count.
 * `view` tells pages apart (their webContents id).
 */
class PageSignOut {
  constructor(now = Date.now) {
    this.now = now
    this.refused = new Map() // view -> when one of its requests was last refused
    this.signingOut = new Map() // view -> when it sent a sign-out not yet answered
  }

  /** A page is sending a request; a sign-out is noted until its answer comes. */
  sending(view, method, url, origin) {
    const parsed = parse(url)
    if (parsed?.origin === origin && isSignOut(method, parsed)) this.signingOut.set(view, this.now())
  }

  /** One of a page's requests came back with `status`; returns whether it was the page's Sign out. */
  heard(view, method, url, status, origin) {
    const parsed = parse(url)
    if (!parsed || parsed.origin !== origin) return false
    const signOut = isSignOut(method, parsed)
    const sentSignOut = this.signingOut.get(view)
    if (signOut) this.signingOut.delete(view)
    if (status === 401) {
      // A request already on its way when the page signed out (a poll, say) is refused because the
      // sign-out ended the session: that says nothing about the session before. Its answer can get
      // here before the sign-out's own.
      const caused = !signOut && sentSignOut !== undefined && this.now() - sentSignOut < REFUSED_WITHIN_MS
      if (!caused) this.refused.set(view, this.now())
      return false
    }
    if (!signOut || status < 200 || status >= 300) return false
    const refused = this.refused.get(view)
    return refused === undefined || this.now() - refused >= REFUSED_WITHIN_MS
  }
}

function isSignOut(method, parsed) {
  return method === 'DELETE' && parsed.pathname.endsWith('/session')
}

/** Copying to the clipboard and going full screen, for the server's own pages; nothing else. */
function allowedPermission(permission, requestingUrl, origin) {
  const parsed = parse(requestingUrl)
  return !!parsed && parsed.origin === origin && PERMISSIONS.has(permission)
}

/** Which saved password a server's password box asks for: 'sync', 'calendar', or null if unknown. */
function loginGroup(realm) {
  const name = String(realm ?? '')
  if (SYNC_REALMS.has(name)) return 'sync'
  if (name === 'XrnTube' || /radicale/i.test(name)) return 'calendar'
  return null
}

/**
 * Whether a password box may be answered at all: only the server's own, over the connection the
 * app was set up with, never a proxy's.
 */
function answersLogin(authInfo, requestUrl, origin) {
  const request = parse(requestUrl)
  const server = parse(origin)
  if (!request || !server || authInfo?.isProxy || request.origin !== origin) return false
  if (String(authInfo?.scheme ?? '').toLowerCase() !== 'basic') return false
  return authInfo.host === server.hostname
}

/**
 * The sidebar's reading of the hub's /api/services: which pages' servers are down. `reply` is the
 * parsed answer, or null when the hub couldn't be reached.
 */
function readStatus(reply) {
  if (!reply || !Array.isArray(reply.services)) return { reachable: !!reply, down: [] }
  const down = []
  for (const service of reply.services) {
    if (service && service.up === false) {
      const page = PAGES.find(p => p.service === service.name)
      down.push(page ? page.id : String(service.name))
    }
  }
  return { reachable: true, down }
}

/** Whether a page may come to the front over `panel`: Settings gives way; setting up and a password box don't. */
function takesPage(panel) {
  return panel === null || panel === 'settings'
}

/** The page asked for on the command line (`--page=today`), if it is one. */
function pageFromArgs(argv) {
  for (const arg of argv) {
    const found = /^--page=(.+)$/.exec(arg)
    if (found && currentId(found[1])) return currentId(found[1])
  }
  return null
}

module.exports = {
  HUB_CSS,
  OUTSIDE_GAP_MS,
  OutsideGate,
  PAGE_CSS,
  PAGES,
  PageSignOut,
  REFUSED_WITHIN_MS,
  allowedPermission,
  answersLogin,
  currentId,
  loginGroup,
  normaliseAddress,
  opensOutside,
  pageById,
  pageFor,
  pageFromArgs,
  pageUrl,
  readStatus,
  route,
  takesPage,
  viewOf
}
