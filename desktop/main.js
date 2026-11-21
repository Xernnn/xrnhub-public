'use strict'
// xrnhub as a desktop app: one window over every page the home server has, so none of them needs
// a browser tab. The sidebar is this app's own page (shell/); each server page is the server's own,
// loaded into a view beside it, and kept loaded once opened, so going back to it is instant and it
// keeps what it was showing. The pages share one sign-in, kept between starts.

const { app, BrowserWindow, Menu, WebContentsView, ipcMain, nativeTheme, safeStorage, session, shell } = require('electron')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const pages = require('./pages')
const themes = require('./theme')
const { Logins } = require('./logins')
const { Store } = require('./store')

const SHELL_PAGE = path.join(__dirname, 'shell', 'index.html')
const PRELOAD = path.join(__dirname, 'preload.js')
const PARTITION = 'persist:hub'
const CLIENT = 'xrnhub' // the X-Xrn-Client the hub's own page sends; a cookie counts only with it
// Where the pages go before the sidebar has said (shell.css: the sidebar's width, the frame's gap).
const SHEET_GUESS = { left: 216, gap: 8 }
const SHEET_RADIUS = 11
const STATUS_EVERY_MS = 60_000
const STATUS_IDLE_MS = 5 * 60_000
const RENEW_WITHIN_S = 3 * 86400
const REQUEST_TIMEOUT_MS = 15_000
const SIGN_OUT_TIMEOUT_MS = 4_000 // signing out goes on without the server

// Its own folder, not ~/.config/xrnhub, which holds the bar widget's netrc. The window test moves
// it to a throwaway one; a packaged app never reads that.
app.setPath('userData', !app.isPackaged && process.env.XRNHUB_DESKTOP_DATA ? process.env.XRNHUB_DESKTOP_DATA : path.join(app.getPath('appData'), 'xrnhub-desktop'))

// A debugging port would let any program running as this user read the pages and their cookie.
const DEBUG_SWITCHES = ['remote-debugging-port', 'remote-debugging-pipe', 'inspect', 'inspect-brk']
if (app.isPackaged && DEBUG_SWITCHES.some(name => app.commandLine.hasSwitch(name))) app.exit(1)

// Electron picks the Linux password store from XDG_CURRENT_DESKTOP and gives desktops it doesn't
// know (Hyprland among them) basic_text, a key built into every copy of Chromium, which this app
// refuses. Anywhere but KDE the Secret Service is the keyring. An explicit --password-store wins.
if (process.platform === 'linux' && !app.commandLine.hasSwitch('password-store') && !/kde/i.test(process.env.XDG_CURRENT_DESKTOP ?? '')) {
  app.commandLine.appendSwitch('password-store', 'gnome-libsecret')
}

// The pages have their own keys (t, r, 1-9 on the hub); Electron's default menu would take Alt and
// the zoom keys, and show a menu bar.
Menu.setApplicationMenu(null)

let win = null
let store = null
let logins = null
let hub = null // the pages' session
let origin = null // the server, once the app is set up
let active = 'server'
let panel = null // null, 'setup', 'settings' or 'sign-in'
let question = null // the password box's question, while panel is 'sign-in'
let status = { reachable: null, signedIn: null, down: [], at: 0 }
let theme = null // the frame's colours (theme.js)
let sheet = null // where the pages go: the sidebar page's sheet, in window pixels
let statusTimer = null
let lastRenew = 0
const views = new Map() // view name -> { view, failed }
const outside = new pages.OutsideGate()
const pageSignOut = new pages.PageSignOut()

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', (_event, argv) => {
    if (win) {
      if (win.isMinimized()) win.restore()
      win.show()
      win.focus()
    }
    const asked = pages.pageFromArgs(argv)
    if (asked && origin && pages.takesPage(panel)) {
      panel = null
      show(asked)
    }
  })
  app.whenReady().then(ready)
}

app.on('window-all-closed', () => app.quit())
app.on('before-quit', () => {
  hub?.cookies.flushStore().catch(() => {})
})
// No page may put a <webview> of its own in.
app.on('web-contents-created', (_event, contents) => {
  contents.on('will-attach-webview', event => event.preventDefault())
})

function keyring() {
  if (!app.isPackaged && process.env.XRNHUB_DESKTOP_KEYRING === 'memory') {
    // The window test's stand-in: kept only while the test runs, never in the real keyring.
    return { available: () => true, encrypt: text => Buffer.from(`test:${text}`), decrypt: data => data.toString().replace(/^test:/, '') }
  }
  return {
    available: () => {
      try {
        if (!safeStorage.isEncryptionAvailable()) return false
        return !(process.platform === 'linux' && safeStorage.getSelectedStorageBackend() === 'basic_text')
      } catch {
        return false
      }
    },
    encrypt: text => safeStorage.encryptString(text),
    decrypt: data => safeStorage.decryptString(data)
  }
}

async function ready() {
  store = new Store(app.getPath('userData'), keyring())
  logins = new Logins(store)
  hub = session.fromPartition(PARTITION)
  hub.setPermissionRequestHandler((_contents, permission, callback, details) => callback(!!origin && pages.allowedPermission(permission, details.requestingUrl, origin)))
  hub.setPermissionCheckHandler((_contents, permission, requestingOrigin) => !!origin && pages.allowedPermission(permission, requestingOrigin, origin))
  // The spellchecker would fetch its dictionaries from Google on first use; nothing here needs it.
  hub.setSpellCheckerEnabled(false)
  session.defaultSession.setSpellCheckerEnabled(false)
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
  session.defaultSession.setPermissionCheckHandler(() => false)
  // A page's own Sign out ends the session for every page: the app signs out with it, rather than
  // sign straight back in with the kept password at the next status check.
  hub.webRequest.onSendHeaders({ urls: ['http://*/*', 'https://*/*'] }, details => {
    if (origin && details.webContentsId) pageSignOut.sending(details.webContentsId, details.method, details.url, origin)
  })
  hub.webRequest.onCompleted({ urls: ['http://*/*', 'https://*/*'] }, details => {
    if (!origin || !details.webContentsId) return
    if (pageSignOut.heard(details.webContentsId, details.method, details.url, details.statusCode, origin)) setImmediate(() => signOut(false))
  })
  hub.on('will-download', (_event, item) => {
    item.setSaveDialogOptions({ defaultPath: path.join(app.getPath('downloads'), item.getFilename()) })
  })
  app.on('login', onLogin)
  loadTheme()
  watchTheme()
  registerIpc()

  const saved = pages.normaliseAddress(store.get('address') ?? '')
  origin = saved.origin ?? null
  const asked = pages.pageFromArgs(process.argv)
  active = asked ?? pages.currentId(store.get('page')) ?? 'server'
  createWindow()
  if (!origin) {
    panel = 'setup'
    return layout()
  }
  // Beside the first page, not before it: a server that doesn't answer would hold the window on its
  // loading dots until the sign-in timed out. Pages loaded before a renewal start again after it.
  const renewing = renewSession()
  show(active)
  startStatus()
  renewing.then(renewed => renewed && reloadViews()).catch(() => {})
}

function themePath() {
  return !app.isPackaged && process.env.XRNHUB_DESKTOP_THEME ? process.env.XRNHUB_DESKTOP_THEME : themes.themeFile(os.homedir())
}

/**
 * The frame in the desktop theme's colours, and the pages in its light or dark, so the window looks
 * like the rest of the desktop. Without a theme, both follow the system.
 */
function loadTheme() {
  const colors = themes.readColors(themePath())
  const source = themes.themeSource(colors)
  if (nativeTheme.themeSource !== source) nativeTheme.themeSource = source
  const next = themes.frameColours(colors, nativeTheme.shouldUseDarkColors)
  const changed = JSON.stringify(next) !== JSON.stringify(theme)
  theme = next
  if (!changed) return
  win?.setBackgroundColor(theme.frame)
  for (const { view } of views.values()) view.setBackgroundColor(theme.sheet)
  broadcast()
}

/** `omarchy theme set` replaces the theme's folder and rewrites theme.name beside it. */
function watchTheme() {
  let timer = null
  const again = () => {
    clearTimeout(timer)
    timer = setTimeout(loadTheme, 300)
  }
  try {
    fs.watch(path.dirname(path.dirname(themePath())), again).on('error', () => {})
  } catch {
    // No Omarchy here: the system's light or dark still reaches the window.
  }
  // The system turning light or dark matters only when the theme doesn't say which it is.
  nativeTheme.on('updated', () => {
    if (nativeTheme.themeSource === 'system') again()
  })
}

/** The window's first size. Hyprland tiles it straight away; the screenshot runs ask for a tile's size. */
function firstSize() {
  const asked = !app.isPackaged && /^(\d{3,4})x(\d{3,4})$/.exec(process.env.XRNHUB_DESKTOP_SIZE ?? '')
  return asked ? { width: Number(asked[1]), height: Number(asked[2]) } : { width: 1320, height: 880 }
}

function createWindow() {
  win = new BrowserWindow({
    ...firstSize(),
    minWidth: 480,
    minHeight: 400,
    title: 'xrnhub',
    show: false,
    backgroundColor: theme.frame,
    webPreferences: { preload: PRELOAD, contextIsolation: true, nodeIntegration: false, sandbox: true, spellcheck: false }
  })
  win.once('ready-to-show', () => win?.show())
  win.on('closed', () => {
    win = null
    clearInterval(statusTimer)
  })
  win.on('resize', layout)
  win.on('focus', () => {
    if (Date.now() - status.at > 15_000) pollStatus()
  })
  const contents = win.webContents
  // The sidebar is this app's own file and stays it; its links are buttons.
  contents.on('will-navigate', event => event.preventDefault())
  contents.setWindowOpenHandler(() => ({ action: 'deny' }))
  contents.on('before-input-event', shortcut)
  contents.on('did-finish-load', () => broadcast())
  win.loadFile(SHELL_PAGE).catch(() => {})
}

/** Where the pages go: the sheet the sidebar page draws, or a guess at it until it says. */
function sheetBounds() {
  if (sheet) return sheet
  const [width, height] = win.getContentSize()
  const { left, gap } = SHEET_GUESS
  return { x: left + 1, y: gap + 1, width: Math.max(0, width - left - gap - 2), height: Math.max(0, height - 2 * gap - 2) }
}

/** Places the views on the sheet, and shows only the page in front, unless a panel covers it. */
function layout() {
  if (!win) return
  const bounds = sheetBounds()
  const front = origin && !panel ? pages.pageById(active)?.view : null
  for (const [name, entry] of views) {
    entry.view.setBounds(bounds)
    entry.view.setVisible(name === front && !entry.failed)
  }
  broadcast()
}

function snapshot() {
  const entry = views.get(pages.pageById(active)?.view)
  return {
    address: origin,
    user: store?.get('user') ?? null,
    active,
    theme,
    panel,
    question: question && { ...question, label: questionLabel(question) },
    problem: panel ? null : entry?.failed ?? null,
    status,
    keyring: store?.canKeep() ?? false,
    pages: pages.PAGES.map(({ id, label, group, icon }) => ({ id, label, group, icon }))
  }
}

function questionLabel(asked) {
  const page = origin && pages.pageById(pages.pageFor(asked.url, origin, null))
  return page ? page.label : asked.realm || 'the server'
}

function broadcast() {
  if (!win || win.webContents.isLoading()) return
  win.webContents.send('xrnhub:changed', snapshot())
}

function viewFor(name) {
  let entry = views.get(name)
  if (entry) return entry
  const view = new WebContentsView({
    webPreferences: { partition: PARTITION, contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true, safeDialogs: true, spellcheck: false }
  })
  view.setBackgroundColor(theme.sheet)
  view.setBorderRadius(SHEET_RADIUS)
  entry = { view, failed: null }
  views.set(name, entry)
  win.contentView.addChildView(view)
  guard(name, entry)
  return entry
}

/**
 * Keep each view on its own page. A link to another page shows that page instead (the hub's
 * Habits icon, xrnloops' link to Blocks); another site opens in the usual browser, at most one a
 * second, and never because the server redirected there.
 */
function guard(name, entry) {
  const contents = entry.view.webContents
  const follow = (url, event, redirect = false) => {
    const where = origin ? pages.route(url, origin, name) : { action: 'block' }
    if (where.action === 'stay') return true
    event?.preventDefault()
    if (where.action === 'switch') show(where.page, where.url)
    else if (where.action === 'outside' && !redirect && outside.allow(name)) shell.openExternal(where.url).catch(() => {})
    return false
  }
  contents.setWindowOpenHandler(({ url }) => {
    // A window of its own: the server's pages open in their view, nothing opens a second window.
    if (follow(url)) contents.loadURL(url).catch(() => {})
    return { action: 'deny' }
  })
  contents.on('will-navigate', event => follow(event.url, event))
  contents.on('will-redirect', event => {
    if (event.isMainFrame) follow(event.url, event, true)
  })
  // A frame inside a page may load only that page's own addresses; it never switches the window.
  contents.on('will-frame-navigate', event => {
    if (event.isMainFrame) return
    if (!origin || pages.route(event.url, origin, name).action !== 'stay') event.preventDefault()
  })
  contents.on('dom-ready', () => {
    const url = contents.getURL()
    if (!origin || !url.startsWith(origin) || url.includes('/api/')) return
    contents.insertCSS(pages.PAGE_CSS).catch(() => {})
    if (pages.viewOf(url, origin) === 'hub') contents.insertCSS(pages.HUB_CSS).catch(() => {})
  })
  contents.on('did-navigate', (_event, url) => followed(name, url))
  contents.on('did-navigate-in-page', (_event, url, isMainFrame) => {
    if (isMainFrame) followed(name, url)
  })
  contents.on('did-fail-load', (_event, code, description, url, isMainFrame) => {
    // -3 is a load that was replaced by another one, which is no failure.
    if (!isMainFrame || code === -3) return
    entry.failed = { code, description, url }
    layout()
  })
  contents.on('render-process-gone', (_event, details) => {
    if (details.reason === 'clean-exit') return
    entry.failed = { code: 0, description: 'The page stopped.', url: contents.getURL() }
    layout()
  })
  contents.on('before-input-event', shortcut)
}

/** A page went somewhere by itself (the hub's own tabs): the sidebar follows. */
function followed(name, url) {
  if (!origin || pages.pageById(active)?.view !== name) return
  const page = pages.pageFor(url, origin, active)
  if (page && page !== active) {
    active = page
    store.set({ page })
    win?.setTitle(`${pages.pageById(page).label} · xrnhub`)
    broadcast()
  }
}

/** Brings a page to the front, at `url` if given, else where it was (or its start, the first time). */
function show(id, url) {
  const page = pages.pageById(id)
  if (!page || !origin || !win) return
  active = id
  store.set({ page: id })
  const entry = viewFor(page.view)
  const contents = entry.view.webContents
  const current = contents.getURL()
  let target = url
  if (!target && !current) target = pages.pageUrl(origin, page)
  // The hub's tabs are its #hash: changing only that keeps the page as it is, without loading again.
  if (!target && page.view === 'hub' && pages.pageFor(current, origin, null) !== id) target = pages.pageUrl(origin, page)
  if (target) {
    entry.failed = null
    contents.loadURL(target).catch(() => {})
  }
  win.setTitle(`${page.label} · xrnhub`)
  layout()
  if (!panel && !entry.failed) contents.focus()
}

function reload(entry, hard) {
  entry.failed = null
  const contents = entry.view.webContents
  const url = contents.getURL()
  // After a failed first load there is nothing to reload: start the page again.
  if (!url || url.startsWith('chrome-error:')) contents.loadURL(pages.pageUrl(origin, pages.pageById(active))).catch(() => {})
  else if (hard) contents.reloadIgnoringCache()
  else contents.reload()
  layout()
}

function activeEntry() {
  return views.get(pages.pageById(active)?.view) ?? null
}

function shortcut(event, input) {
  if (input.type !== 'keyDown') return
  const ctrl = input.control || input.meta
  const key = input.key.length === 1 ? input.key.toLowerCase() : input.key
  const entry = activeEntry()
  const contents = entry?.view.webContents
  if (ctrl && !input.alt && !input.shift && /^[1-9]$/.test(key)) {
    const page = pages.PAGES[Number(key) - 1]
    if (page && origin && pages.takesPage(panel)) {
      panel = null
      show(page.id)
    }
  } else if ((ctrl && !input.alt && key === 'r') || key === 'F5') {
    if (entry && !panel) reload(entry, input.shift)
  } else if (input.alt && !ctrl && (key === 'ArrowLeft' || key === 'ArrowRight')) {
    const history = contents?.navigationHistory
    if (key === 'ArrowLeft' && history?.canGoBack()) history.goBack()
    if (key === 'ArrowRight' && history?.canGoForward()) history.goForward()
  } else if (ctrl && !input.shift && !input.alt && key === 'q') {
    app.quit()
  } else if (ctrl && input.shift && key === 'i') {
    contents?.toggleDevTools()
  } else if (ctrl && !input.alt && (key === '=' || key === '+' || key === '-' || key === '0')) {
    // The pages are all on one server, so Chromium zooms them together.
    if (contents) contents.setZoomLevel(key === '0' ? 0 : contents.getZoomLevel() + (key === '-' ? -0.5 : 0.5))
  } else {
    return
  }
  event.preventDefault()
}

/** The hub's sign-in, which every cookie page shares. Resolves to the server's answer. */
function signInHub(server, user, password) {
  return hub.fetch(`${server}/xrnhub/api/session`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'X-Xrn-Client': CLIENT, Authorization: `Basic ${Buffer.from(`${user}:${password}`).toString('base64')}` },
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)
  })
}

/**
 * The session lasts 30 days. With the password kept, sign in again when it is nearly over, so a page
 * never meets its own sign-in form. Without one, the pages ask, as in a browser.
 */
async function renewSession(force = false) {
  const kept = store.login('sync')
  if (!kept || !origin) return false
  if (!force) {
    const cookies = await hub.cookies.get({ url: origin, name: 'xrn_session' }).catch(() => [])
    const expires = cookies[0]?.expirationDate ?? 0
    if (expires - Date.now() / 1000 > RENEW_WITHIN_S) return false
  }
  lastRenew = Date.now()
  const reply = await signInHub(origin, kept.user, kept.password).catch(() => null)
  if (reply?.status === 401) store.forgetLogin('sync')
  if (!reply?.ok) return false
  await markSignedIn()
  return true
}

/** Every page loaded while signed out drew its sign-in form, or nothing: they start again. */
function reloadViews() {
  for (const entry of views.values()) {
    const url = entry.view.webContents.getURL()
    if (!url || url.startsWith('chrome-error:')) continue
    entry.failed = null
    entry.view.webContents.reload()
  }
  layout()
}

/**
 * The habits and blocks pages show their own sign-in form unless the server's localStorage holds
 * their hint, xrn.signedIn, that the cookie is worth trying; their own form sets it, and a cookie
 * that stops working clears it. After signing in here, set it as they would, from the hub's page on
 * the same server, before either of them starts and reads it.
 */
async function markSignedIn() {
  if (!origin || !win) return
  const contents = viewFor('hub').view.webContents
  try {
    if (pages.viewOf(contents.getURL(), origin) !== 'hub') {
      const page = pages.pageById(active)?.view === 'hub' ? pages.pageById(active) : pages.PAGES[0]
      await contents.loadURL(pages.pageUrl(origin, page))
    }
    await contents.executeJavaScript('localStorage.setItem("xrn.signedIn", "true")')
  } catch {
    // The hub didn't load: the pages show their own form, which works as well.
  }
}

function startStatus() {
  clearInterval(statusTimer)
  pollStatus()
  // Every minute while the window has focus, every five otherwise: the hub's own pages refresh the rest.
  statusTimer = setInterval(() => {
    if (win?.isFocused() || Date.now() - status.at >= STATUS_IDLE_MS) pollStatus()
  }, STATUS_EVERY_MS)
}

async function pollStatus() {
  if (!origin) return
  const server = origin
  let reply = null
  let signedIn = null
  try {
    const answer = await hub.fetch(`${server}/xrnhub/api/services`, {
      credentials: 'include',
      headers: { 'X-Xrn-Client': CLIENT },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)
    })
    signedIn = answer.status !== 401
    reply = answer.ok ? await answer.json() : {}
  } catch {
    reply = null
  }
  if (server !== origin) return
  status = { ...pages.readStatus(reply), signedIn, at: Date.now() }
  broadcast()
  // Signed out underneath (the cookie ran out, or was cleared): sign in again, at most hourly.
  if (signedIn === false && store.login('sync') && Date.now() - lastRenew > 3600_000) {
    if (await renewSession(true)) reloadViews()
    pollStatus()
  }
}

function onLogin(event, contents, details, authInfo, callback) {
  // Only the server's own boxes are answered; anything else is refused, as Electron does by default.
  if (!origin || !pages.answersLogin(authInfo, details.url, origin)) return
  event.preventDefault()
  const asked = logins.challenge(details.url, pages.loginGroup(authInfo.realm), authInfo.realm, callback, details.firstAuthAttempt !== false)
  if (asked) ask(asked)
}

function ask(asked) {
  question = asked
  panel = asked ? 'sign-in' : null
  layout()
  if (asked) win?.webContents.focus()
}

function registerIpc() {
  // Only the sidebar, this app's own file, may call these, and every argument is checked.
  const handle = (channel, fn) => {
    ipcMain.handle(channel, (event, ...args) => {
      const frame = event.senderFrame
      if (!win || event.sender !== win.webContents || !frame || frame !== win.webContents.mainFrame || !frame.url.startsWith('file://')) {
        throw new Error('not allowed')
      }
      return fn(...args)
    })
  }
  const text = (value, max) => (typeof value === 'string' && value.length <= max ? value : null)

  handle('xrnhub:state', () => snapshot())
  handle('xrnhub:sheet', rect => {
    const [width, height] = win.getContentSize()
    const ok = rect && ['x', 'y', 'width', 'height'].every(key => Number.isInteger(rect[key]) && rect[key] >= 0)
    if (!ok || rect.x + rect.width > width + 1 || rect.y + rect.height > height + 1) return false
    sheet = { x: rect.x, y: rect.y, width: rect.width, height: rect.height }
    layout()
    return true
  })
  handle('xrnhub:open', id => {
    if (typeof id !== 'string' || !pages.pageById(id) || !pages.takesPage(panel)) return false
    panel = null
    show(id)
    return true
  })
  handle('xrnhub:setup', async form => {
    const address = text(form?.address, 300)
    const user = text(form?.user, 64)
    const password = text(form?.password, 1024)
    if (address === null || user === null || password === null) return { error: 'Something in the form isn\'t right' }
    const found = pages.normaliseAddress(address)
    if (found.error) return { error: found.error, field: 'address' }
    if (!user.trim() || !password) return { error: 'Type your name and password', field: user.trim() ? 'password' : 'user' }
    let reply
    try {
      reply = await signInHub(found.origin, user.trim(), password)
    } catch {
      return { error: 'Can\'t reach that address', field: 'address' }
    }
    if (reply.status === 401) return { error: 'Wrong name or password', field: 'password' }
    if (reply.status === 429) return { error: 'Too many tries' }
    if (!reply.ok) return { error: `The server answered ${reply.status}`, field: 'address' }
    if (origin && origin !== found.origin) closeViews()
    origin = found.origin
    store.set({ address: origin, user: user.trim() })
    const kept = store.keepLogin('sync', user.trim(), password)
    panel = null
    await markSignedIn()
    show(active)
    startStatus()
    return { ok: true, kept }
  })
  handle('xrnhub:sign-in', form => {
    if (panel !== 'sign-in') return false
    let next
    if (form === null) {
      next = logins.cancel()
    } else {
      const user = text(form?.user, 64)
      const password = text(form?.password, 1024)
      if (!user || password === null) return false
      next = logins.answer(user.trim(), password).next
    }
    ask(next)
    if (!next) activeEntry()?.view.webContents.focus()
    return true
  })
  handle('xrnhub:retry', () => {
    const entry = activeEntry()
    if (entry) reload(entry, false)
    pollStatus()
    return true
  })
  handle('xrnhub:panel', name => {
    if (panel === 'setup' || panel === 'sign-in') return false
    if (name !== 'settings' && name !== null) return false
    panel = name
    layout()
    if (!panel) activeEntry()?.view.webContents.focus()
    return true
  })
  handle('xrnhub:sign-out', () => signOut(true))
}

/**
 * Forgets the kept passwords, the pages' sign-in and what they cached, and goes back to setting up.
 * `tellServer` is false when a page has told it already.
 */
async function signOut(tellServer) {
  if (!origin || panel === 'setup') return false
  clearInterval(statusTimer)
  // The kept password goes first, so a status check on its way can't sign in again with it.
  logins.reset()
  store.forgetAll()
  if (tellServer) {
    // The server ends the session too, so a copy of the cookie stops working; offline, it ends here only.
    await hub.fetch(`${origin}/xrnhub/api/session`, {
      method: 'DELETE',
      credentials: 'include',
      headers: { 'X-Xrn-Client': CLIENT },
      signal: AbortSignal.timeout(SIGN_OUT_TIMEOUT_MS)
    }).catch(() => {})
  }
  closeViews()
  await hub.clearStorageData().catch(() => {})
  // The HTTP cache and compiled scripts are kept apart from the storage above.
  await hub.clearCache().catch(() => {})
  await hub.clearCodeCaches({}).catch(() => {})
  await hub.clearAuthCache().catch(() => {})
  status = { reachable: null, signedIn: null, down: [], at: 0 }
  panel = 'setup'
  layout()
  return true
}

function closeViews() {
  for (const { view } of views.values()) {
    win?.contentView.removeChildView(view)
    view.webContents.close()
  }
  views.clear()
}
