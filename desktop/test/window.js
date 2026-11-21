'use strict'
// Starts the desktop app for real, headless, against the hub's made-up server (tests/demo_server.py),
// and checks it through Chromium's debugging port: setting up signs every page in, the sidebar and
// the hub's own tabs follow each other, an old #nudges address lands on Today, a nudge card's menu
// turns its rule off and Today's list turns it on, links between pages switch pages, a password box is answered
// with the kept password, a second start hands its page to the first, the sign-in outlasts a
// restart, and a server that has gone shows a panel rather than Chromium's error page.
//
//   npm run test:window
//
// Everything it makes goes in a throwaway folder, and the passwords in a stand-in keyring that never
// touches the real one. It never opens anything in the desktop's own browser: the one address it
// tries outside the server is smb:, which the app refuses.

const assert = require('node:assert/strict')
const fs = require('node:fs')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')
const { spawn } = require('node:child_process')
const { Store } = require('../store')

const HERE = path.join(__dirname, '..')
const REPO = path.join(HERE, '..')
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'xrnhub-window-'))
const profile = path.join(scratch, 'profile')
// A made-up Omarchy theme, laid out as Omarchy keeps the current one.
const themeDir = path.join(scratch, 'omarchy', 'current', 'theme')
const themeFile = path.join(themeDir, 'colors.toml')
fs.mkdirSync(themeDir, { recursive: true })
fs.writeFileSync(themeFile, 'mode = "dark"\naccent = "#798186"\nbackground = "#101315"\nforeground = "#cacccc"\n')
const env = { ...process.env, XRNHUB_DESKTOP_DATA: profile, XRNHUB_DESKTOP_KEYRING: 'memory', XRNHUB_DESKTOP_THEME: themeFile }
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const launched = []

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      server.close(() => resolve(port))
    })
  })
}

function start(command, args, options) {
  const child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'], ...options })
  launched.push(child)
  let output = ''
  child.stdout.on('data', chunk => { output += chunk })
  child.stderr.on('data', chunk => { output += chunk })
  child.output = () => output
  child.exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({ code, signal })))
  return child
}

function launch(debugPort, extra = []) {
  // Headless, so no window appears on this desktop; basic, so Chromium never asks the real keyring.
  const flags = ['--ozone-platform=headless', '--ozone-override-screen-size=1320,880', `--remote-debugging-port=${debugPort}`, '--password-store=basic', ...extra]
  return start(require(path.join(HERE, 'node_modules', 'electron')), [HERE, ...flags], { env })
}

async function until(what, check, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs
  let last
  for (;;) {
    try {
      const value = await check()
      if (value) return value
    } catch (error) {
      last = error
    }
    if (Date.now() > deadline) throw new Error(`Timed out waiting for ${what}${last ? ` (${last.message})` : ''}`)
    await sleep(200)
  }
}

async function targets(debugPort) {
  return (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json()
}

/**
 * A page's reply, or an error if the page goes away first or takes over 30 s. A page the app closes
 * (as signing out closes them all) never replies, and without this the test waited for it forever.
 */
function answer(socket, reply, what) {
  let timer
  const gone = new Promise((_resolve, reject) => {
    socket.onclose = () => reject(new Error(`the page went away before answering ${what}`))
    timer = setTimeout(() => reject(new Error(`no answer in 30 s to ${what}`)), 30_000)
  })
  return Promise.race([reply, gone]).finally(() => clearTimeout(timer))
}

/** Send one debugging command to a page; resolves to its result. */
async function send(target, method, params) {
  const socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.onopen = resolve
    socket.onerror = reject
  })
  try {
    const reply = new Promise((resolve, reject) => {
      socket.onmessage = event => {
        const message = JSON.parse(event.data)
        if (message.id !== 1) return
        if (message.error) reject(new Error(JSON.stringify(message.error)))
        else resolve(message.result)
      }
    })
    socket.send(JSON.stringify({ id: 1, method, params }))
    return await answer(socket, reply, method)
  } finally {
    socket.close()
  }
}

/** Run an expression in a page, through the debugging port. */
async function evaluate(target, expression) {
  const socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.onopen = resolve
    socket.onerror = reject
  })
  try {
    const reply = new Promise((resolve, reject) => {
      socket.onmessage = event => {
        const message = JSON.parse(event.data)
        if (message.id !== 1) return
        if (message.error || message.result.exceptionDetails) reject(new Error(JSON.stringify(message.error || message.result.exceptionDetails)))
        else resolve(message.result.result.value)
      }
    })
    socket.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression, awaitPromise: true, returnByValue: true, userGesture: true } }))
    return await answer(socket, reply, expression.slice(0, 80))
  } finally {
    socket.close()
  }
}

const shellTarget = debugPort => until('the sidebar', async () => (await targets(debugPort)).find(t => t.type === 'page' && t.url.startsWith('file:') && t.url.endsWith('/shell/index.html')))
const pageTarget = (debugPort, prefix) => until(`a page at ${prefix}`, async () => (await targets(debugPort)).find(t => t.type === 'page' && t.url.startsWith(prefix)))

/** The sidebar's own state, read from what it draws. */
function sidebar(shell) {
  return evaluate(shell, `({
    current: document.querySelector('.item[aria-current="page"]')?.dataset.page ?? null,
    locked: [...document.querySelectorAll('.item')].every(b => b.disabled),
    panel: document.querySelector('main .panel h1')?.textContent ?? null,
    error: document.querySelector('main .error')?.textContent ?? '',
    status: document.querySelector('#status .label').textContent
  })`)
}

async function setUp(shell, address, user, password) {
  await evaluate(shell, `(() => {
    const form = document.querySelector('main form')
    form.address.value = ${JSON.stringify(address)}
    form.user.value = ${JSON.stringify(user)}
    form.password.value = ${JSON.stringify(password)}
    form.requestSubmit()
    return true
  })()`)
}

async function main() {
  const checks = []
  const ok = name => {
    checks.push(name)
    console.log(`ok - ${name}`)
  }

  const port = await freePort()
  const server = `http://127.0.0.1:${port}`
  const demoFolder = path.join(scratch, 'demo')
  const demo = start('python3', [path.join(REPO, 'tests', 'demo_server.py'), demoFolder, '--port', String(port)], {})
  await until('the demo server', async () => (await fetch(`${server}/xrnhub/health`)).ok)
  const password = fs.readFileSync(path.join(demoFolder, 'password'), 'utf8').trim()

  const debugPort = await freePort()
  const first = launch(debugPort)
  let shell = await shellTarget(debugPort)
  let side = await until('the set-up panel', async () => {
    const s = await sidebar(shell)
    return s.panel ? s : null
  })
  assert.equal(side.panel, 'Sign in to the home server')
  assert.ok(side.locked, 'no page can be opened before setting up')
  ok('the first start asks for the server, and nothing else can be opened')

  await setUp(shell, `http://127.0.0.1:${await freePort()}`, 'tester', password)
  await until('the unreachable error', async () => /Can't reach/.test((await sidebar(shell)).error))
  await setUp(shell, `http://192.0.2.1:${port}`, 'tester', password)
  await until('the https error', async () => /https/.test((await sidebar(shell)).error))
  await setUp(shell, server, 'tester', 'wrong')
  await until('the wrong-password error', async () => /Wrong name or password/.test((await sidebar(shell)).error), 20_000)
  assert.equal((await targets(debugPort)).filter(t => t.url.startsWith(server)).length, 0, 'no page loads until it works')
  ok('a wrong address, plain http to another computer and a wrong password are each refused, with why')

  await setUp(shell, server, 'tester', password)
  let hub = await pageTarget(debugPort, `${server}/xrnhub/`)
  await until('the hub signed in', () => evaluate(hub, '!!document.querySelector(".frame") && !document.querySelector(".signin")'))
  side = await until('the sidebar on Server', async () => {
    const s = await sidebar(shell)
    return s.current === 'server' && !s.panel ? s : null
  })
  await until('the status', async () => (await sidebar(shell)).status !== 'Checking…')
  side = await sidebar(shell)
  assert.ok(!/Can't reach|Signed out/.test(side.status), side.status)
  // Set from the hub's page just after it signs in, so the page can be up a moment before it is.
  await until('the habits and blocks pages will try the cookie', () => evaluate(hub, 'localStorage.getItem("xrn.signedIn") === "true"'))
  ok(`setting up signs the hub in, without its own sign-in form (sidebar: ${side.status})`)

  // The page is drawn exactly on the sheet, in a frame of the theme's colours.
  const frame = () => evaluate(shell, `(() => {
    const sheet = document.getElementById('sheet')
    return { width: sheet.clientWidth, height: sheet.clientHeight, frame: getComputedStyle(document.body).backgroundColor, mode: document.documentElement.dataset.mode }
  })()`)
  let drawn = await frame()
  assert.equal(drawn.frame, 'rgb(16, 19, 21)')
  assert.equal(drawn.mode, 'dark')
  await until('the page on the sheet', async () => {
    const inner = await evaluate(hub, '({ width: innerWidth, height: innerHeight })')
    return inner.width === drawn.width && inner.height === drawn.height
  })
  assert.equal(await evaluate(hub, 'matchMedia("(prefers-color-scheme: dark)").matches'), true)
  // The Server page once opened 74 px down, its sections hidden above the sheet's top edge.
  assert.equal(await evaluate(hub, 'scrollY'), 0, 'the Server page opens at its top')
  ok('the page sits exactly on the sheet, at its top, in a frame of the desktop theme\'s colours')

  // Changing the desktop's theme reaches the frame and the pages, as omarchy theme set does it.
  fs.writeFileSync(themeFile, 'mode = "light"\naccent = "#586e75"\nbackground = "#fdf6e3"\nforeground = "#586e75"\n')
  fs.writeFileSync(path.join(themeDir, '..', 'theme.name'), 'made-up-light\n')
  await until('the light theme', async () => (await frame()).frame === 'rgb(253, 246, 227)')
  assert.equal(await evaluate(hub, 'matchMedia("(prefers-color-scheme: dark)").matches'), false, 'the page turned light too')
  fs.writeFileSync(themeFile, 'mode = "dark"\naccent = "#798186"\nbackground = "#101315"\nforeground = "#cacccc"\n')
  fs.writeFileSync(path.join(themeDir, '..', 'theme.name'), 'made-up-dark\n')
  await until('the dark theme again', async () => (await frame()).mode === 'dark')
  ok('a new desktop theme reaches the frame and the pages while the app is open')

  // The hub's tabs are one page: the sidebar changes its #hash, and the page stays loaded.
  await evaluate(hub, 'window.__sameDocument = true')
  await evaluate(shell, 'document.querySelector(\'.item[data-page="today"]\').click(), true')
  await until('Today', () => evaluate(hub, 'location.hash === "#today"'))
  assert.equal(await evaluate(hub, 'window.__sameDocument === true'), true, 'not loaded again')
  assert.equal((await sidebar(shell)).current, 'today')
  await evaluate(hub, 'location.hash = "#review"')
  await until('the sidebar to follow', async () => (await sidebar(shell)).current === 'review')
  await evaluate(hub, 'location.hash = "#apps"')
  await until('Apps', async () => (await sidebar(shell)).current === 'apps')
  await evaluate(hub, 'location.hash = "#review"')
  await until('Review again', async () => (await sidebar(shell)).current === 'review')
  ok('the sidebar switches the hub\'s tabs without reloading it, and follows the hub\'s own tab changes')

  // Nudges is a tab no more, here or in the hub's own header; an old #nudges address shows Today.
  assert.deepEqual(await evaluate(shell, '[...document.querySelectorAll(".item")].map(item => item.dataset.page)'),
    ['server', 'today', 'review', 'apps', 'habits', 'blocks', 'home', 'insta', 'photos', 'notes'])
  assert.equal(await evaluate(hub, 'document.querySelector(\'a[href="#nudges"]\')'), null)
  await evaluate(hub, 'location.hash = "#nudges"')
  await until('Today from #nudges', async () => (await sidebar(shell)).current === 'today' &&
    evaluate(hub, 'location.hash === "#today" && !!document.querySelector("#view .today .slot-habits")'))
  assert.equal(await evaluate(hub, 'window.__sameDocument === true'), true, 'not loaded again')
  ok('Nudges is gone from the sidebar and the hub, and an old #nudges address lands on Today')

  // A nudge card's menu turns its rule off on the server; then the rules sit folded at the foot of
  // Today, where its switch turns it back on and the card comes back.
  const rulesOn = 'fetch("api/rules", { headers: { "X-Xrn-Client": "xrnhub" } }).then(r => r.json()).then(d => Object.fromEntries(d.rules.map(r => [r.id, r.on])))'
  const nudge = await until('a nudge card on Today', () => evaluate(hub, `(() => {
    const menu = document.querySelector('.nudge .menu')
    return menu && { id: menu.id.replace('nudge-menu-', ''), name: menu.closest('.nudge').querySelector('b').textContent }
  })()`))
  assert.equal(await evaluate(hub, 'document.querySelector(".rules")'), null, 'no list of rules while all are on')
  await evaluate(hub, 'document.querySelector(".nudge .more").click(), true')
  await until('the card\'s menu', () => evaluate(hub, `document.getElementById('nudge-menu-${nudge.id}').matches(':popover-open')`))
  assert.deepEqual(await evaluate(hub, `[...document.querySelectorAll('#nudge-menu-${nudge.id} button')].map(b => b.textContent)`), ['Hide today', 'Turn off'])
  await evaluate(hub, `[...document.querySelectorAll('#nudge-menu-${nudge.id} button')].find(b => b.textContent === 'Turn off').click(), true`)
  await until('the rule off on the server', async () => (await evaluate(hub, rulesOn))[nudge.id] === false)
  await until('the card gone, the rules at the foot', () => evaluate(hub,
    `!document.getElementById('nudge-menu-${nudge.id}') && /1 off/.test(document.querySelector('.rules summary')?.textContent)`))
  // From the server, not just this page: a fresh load still has it off.
  await evaluate(hub, 'location.reload(), true')
  await until('Today loaded again', () => evaluate(hub, '!!document.querySelector("#view .today .slot-habits") && !!document.querySelector(".rules summary")'))
  assert.equal(await evaluate(hub, `!!document.getElementById('nudge-menu-${nudge.id}')`), false)
  await evaluate(hub, 'document.querySelector(".rules summary").click(), true')
  const rule = `[...document.querySelectorAll('.rules .switch')].find(s => s.getAttribute('aria-label') === ${JSON.stringify(nudge.name)})`
  await until('its switch, off', () => evaluate(hub, `${rule}?.getAttribute('aria-checked') === 'false'`))
  await evaluate(hub, `${rule}.click(), true`)
  await until('the rule on again', async () => (await evaluate(hub, rulesOn))[nudge.id] === true)
  await until('the card back', () => evaluate(hub, `!!document.getElementById('nudge-menu-${nudge.id}')`))
  ok(`a nudge card's Turn off turns its rule off (${nudge.id}), and the rules at the foot of Today turn it back on`)

  // The hub's header (its tabs, its links, the down count) is the sidebar's job here.
  assert.equal(await evaluate(hub, 'getComputedStyle(document.querySelector(".frame > .top")).display'), 'none')
  ok('the hub page\'s own header is hidden beside the sidebar')

  // A link to another page shows that page instead; another site never opens inside.
  await evaluate(hub, 'location.hash = "#review"; location.href = "/xrnloops/"; true')
  await until('Habits', async () => (await sidebar(shell)).current === 'habits')
  await pageTarget(debugPort, `${server}/xrnloops/`)
  hub = await pageTarget(debugPort, `${server}/xrnhub/`)
  assert.ok(hub.url.endsWith('#review'), `the hub stays where it was (${hub.url})`)
  const before = (await targets(debugPort)).filter(t => t.type === 'page').length
  await evaluate(hub, 'window.open("smb://example.invalid/share", "_blank"); location.href = "smb://example.invalid/share"; true')
  await sleep(800)
  const after = await targets(debugPort)
  assert.equal(after.filter(t => t.type === 'page').length, before, 'no new window')
  assert.equal(after.filter(t => t.url.startsWith('smb:')).length, 0)
  ok('a link from the hub to Habits switches to Habits; another kind of address opens nowhere')

  // Opened directly, an API address answers with a password box, which the kept password fills.
  await evaluate(hub, 'location.href = "/xrnhub/api/services"; true')
  const answered = await until('the API answer', async () => {
    const t = (await targets(debugPort)).find(target => target.url === `${server}/xrnhub/api/services`)
    return t && JSON.parse(await evaluate(t, 'document.body.innerText'))
  })
  assert.ok(Array.isArray(answered.services))
  assert.equal((await sidebar(shell)).panel, null, 'no box was shown')
  ok('the server\'s password box is answered with the kept password, without asking')

  // A second start hands its page to the first and ends.
  const second = launch(await freePort(), ['--page=today'])
  const secondExit = await Promise.race([second.exited, sleep(20_000).then(() => 'still running')])
  assert.notEqual(secondExit, 'still running', 'the second start hands over and ends')
  await until('Today from the second start', async () => (await sidebar(shell)).current === 'today')
  ok('a second start with --page=today shows Today in the first window')

  // Settings covers the page, and says where the app is signed in.
  await evaluate(shell, 'document.getElementById("settings").click(), true')
  await until('settings', async () => (await sidebar(shell)).panel === 'Settings')
  const facts = await evaluate(shell, 'document.querySelector(".facts").textContent')
  assert.ok(facts.includes(server) && facts.includes('tester'), facts)
  await evaluate(shell, 'document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" })), true')
  await until('settings closed', async () => (await sidebar(shell)).panel === null)
  ok('settings show the server and the user, and Escape closes them')

  // A page asked for from outside (the bar widget, the launcher) comes up over Settings.
  await evaluate(shell, 'document.getElementById("settings").click(), true')
  await until('settings again', async () => (await sidebar(shell)).panel === 'Settings')
  const third = launch(await freePort(), ['--page=review'])
  await Promise.race([third.exited, sleep(20_000)])
  await until('Review over Settings', async () => {
    const s = await sidebar(shell)
    return s.current === 'review' && s.panel === null
  })
  await evaluate(shell, 'document.querySelector(\'.item[data-page="today"]\').click(), true')
  await until('Today again', async () => (await sidebar(shell)).current === 'today')
  ok('a second start with --page=review closes Settings and shows Review')

  // Signing out tells the server, so the session ends there too; setting up again signs back in.
  await evaluate(shell, 'window.xrnhub.signOut()')
  await until('the set-up panel after signing out', async () => (await sidebar(shell)).panel === 'Sign in to the home server')
  await until('the server told', () => /"DELETE \/xrnhub\/api\/session HTTP\/1\.1" 200/.test(demo.output()))
  await setUp(shell, server, 'tester', password)
  await until('the hub signed in after setting up again', async () => {
    hub = await pageTarget(debugPort, `${server}/xrnhub/`)
    return evaluate(hub, '!!document.querySelector(".frame") && !document.querySelector(".signin")')
  })
  // The sidebar drops its set-up panel in its own time, after the page has signed in: a later wait
  // for that panel to come back must not be answered by this one, still on its way out.
  await until('the set-up panel gone', async () => (await sidebar(shell)).panel === null)
  ok('signing out ends the session on the server, and setting up again signs the pages back in')

  // The hub's own Sign out, inside the window, signs the app out too; the kept password doesn't
  // sign straight back in.
  const pageDelete = 'fetch("api/session", { method: "DELETE", credentials: "same-origin", headers: { "X-Xrn-Client": "xrnhub" } }).then(r => r.status)'
  // The app closes the page as soon as the server has answered, sometimes before the page's own answer
  // gets back here, so the server's log says whether it was signed out.
  const signedOut = () => demo.output().match(/"DELETE \/xrnhub\/api\/session HTTP\/1\.1" 200/g)?.length ?? 0
  const signedOutBefore = signedOut()
  await evaluate(hub, `${pageDelete}, true`)
  await until('the server told by the page', () => signedOut() > signedOutBefore)
  await until('the set-up panel after the page signed out', async () => (await sidebar(shell)).panel === 'Sign in to the home server')
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(profile, 'secrets.json'), 'utf8')), {})
  ok('the hub page\'s own Sign out signs the window out, and the kept password goes with it')

  await setUp(shell, server, 'tester', password)
  await until('the hub signed in once more', async () => {
    hub = await pageTarget(debugPort, `${server}/xrnhub/`)
    return evaluate(hub, '!!document.querySelector(".frame") && !document.querySelector(".signin")')
  })
  // The sidebar drops its set-up panel in its own time, after the page has signed in: the checks below
  // that it stays away must not be answered by this one, still on its way out.
  await until('the set-up panel gone again', async () => (await sidebar(shell)).panel === null)
  // A page that signs itself out because its session was just refused (as Habits and Blocks do) is
  // mended by the kept password instead.
  assert.equal(await evaluate(hub, `fetch("api/services", { credentials: "omit", headers: { "X-Xrn-Client": "xrnhub" } }).then(r => r.status)`), 401)
  assert.equal(await evaluate(hub, pageDelete), 200)
  await sleep(500)
  assert.equal((await sidebar(shell)).panel, null, 'still signed in')
  assert.ok(JSON.parse(fs.readFileSync(path.join(profile, 'secrets.json'), 'utf8')).sync, 'the password is still kept')
  await evaluate(shell, 'window.xrnhub.retry()')
  await until('the hub signed in by the kept password', () => evaluate(hub, '!!document.querySelector(".frame") && !document.querySelector(".signin")'))
  assert.equal(await evaluate(hub, `fetch("api/services", { headers: { "X-Xrn-Client": "xrnhub" } }).then(r => r.status)`), 200)
  ok('a page signing out after its session was refused is signed in again by the kept password')

  // Quit, start again: no setting up, the last page, still signed in.
  for (const target of await targets(debugPort)) await fetch(`http://127.0.0.1:${debugPort}/json/close/${target.id}`).catch(() => {})
  const firstExit = await Promise.race([first.exited, sleep(20_000).then(() => 'still running')])
  assert.notEqual(firstExit, 'still running', 'the app ends when its window closes')
  const debugPort2 = await freePort()
  const restarted = launch(debugPort2)
  shell = await shellTarget(debugPort2)
  hub = await pageTarget(debugPort2, `${server}/xrnhub/`)
  assert.ok(hub.url.endsWith('#today'), `opens on the last page (${hub.url})`)
  await until('the hub signed in again', () => evaluate(hub, '!!document.querySelector(".frame") && !document.querySelector(".signin")'))
  assert.equal((await sidebar(shell)).panel, null)
  ok('started again, it opens on the last page, still signed in')

  // The cookie goes while the window is open, and the hub, behind another page, draws its sign-in
  // form. Once the app has signed in again by itself, the hub starts again, signed in.
  await evaluate(shell, 'document.querySelector(\'.item[data-page="habits"]\').click(), true')
  await until('Habits', async () => (await sidebar(shell)).current === 'habits')
  await send(hub, 'Network.deleteCookies', { name: 'xrn_session', url: `${server}/xrnhub/` })
  await evaluate(hub, 'setTimeout(() => location.reload(), 0), true')
  await until('the hub\'s own sign-in form', () => evaluate(hub, '!!document.querySelector(".signin")'))
  await evaluate(shell, 'window.xrnhub.retry()')
  await until('the hub signed in by the app', () => evaluate(hub, '!!document.querySelector(".frame") && !document.querySelector(".signin")'))
  await evaluate(shell, 'document.querySelector(\'.item[data-page="today"]\').click(), true')
  await until('Today', async () => (await sidebar(shell)).current === 'today')
  ok('after the app signs in again by itself, a page showing its sign-in form starts again')

  // The server goes away: a panel says so, not Chromium's error page.
  demo.kill('SIGTERM')
  await demo.exited
  await evaluate(shell, 'window.xrnhub.retry()')
  await until('the problem panel', async () => (await sidebar(shell)).panel === 'Can\'t reach the server')
  await until('the status', async () => (await sidebar(shell)).status === 'Can\'t reach the server')
  ok('with the server gone, the page area says it can\'t be reached, and so does the sidebar')

  // Signing out forgets the passwords, the cookie and the pages' cache, and goes back to setting up.
  const cacheFolder = path.join(profile, 'Partitions', 'hub', 'Cache', 'Cache_Data')
  const cached = () => (fs.existsSync(cacheFolder) ? fs.readdirSync(cacheFolder).filter(name => /^[0-9a-f]{16}_[0-9s]$/.test(name)) : [])
  assert.ok(cached().length > 0, 'the pages were cached')
  await evaluate(shell, 'window.xrnhub.signOut()')
  await until('the set-up panel again', async () => (await sidebar(shell)).panel === 'Sign in to the home server')
  assert.equal(await evaluate(shell, 'document.querySelector("main form").address.value'), server, 'the address is kept to sign in again')
  const secrets = JSON.parse(fs.readFileSync(path.join(profile, 'secrets.json'), 'utf8'))
  assert.deepEqual(secrets, {})
  assert.deepEqual(cached(), [], 'nothing the pages showed is left in the cache')
  ok('signing out forgets the kept password and the pages\' cache, and goes back to setting up')

  // A server that takes the connection and never answers, with the session due for renewal: the
  // first page is asked for at once, not after the sign-in has waited out its 15 s.
  const heard = []
  const silent = net.createServer(socket => {
    socket.on('data', chunk => heard.push(...chunk.toString().split('\r\n').filter(line => /^[A-Z]+ \//.test(line))))
    socket.on('error', () => {})
  })
  await new Promise(resolve => silent.listen(0, '127.0.0.1', resolve))
  for (const target of await targets(debugPort2)) await fetch(`http://127.0.0.1:${debugPort2}/json/close/${target.id}`).catch(() => {})
  assert.notEqual(await Promise.race([restarted.exited, sleep(20_000).then(() => 'still running')]), 'still running')
  const kept = new Store(profile, { available: () => true, encrypt: text => Buffer.from(`test:${text}`), decrypt: data => data.toString().replace(/^test:/, '') })
  kept.set({ address: `http://127.0.0.1:${silent.address().port}`, page: 'today' })
  kept.keepLogin('sync', 'tester', password)
  const startedAt = Date.now()
  launch(await freePort())
  // Both go out at once; the sign-in reads the cookie jar first, so it can reach the server a moment
  // after the page. Within 10 s, either way: a page held back by the sign-in would wait out its 15 s.
  await until('the first page and the sign-in asked for', () => heard.some(line => line.startsWith('GET /xrnhub/ ')) &&
    heard.some(line => line.startsWith('POST /xrnhub/api/session ')), 10_000)
  ok(`with a server that never answers, the first page is asked for at once (${Date.now() - startedAt} ms), beside the sign-in`)

  console.log(`all passed (${checks.length})`)
  // The app goes first: still running, it writes its profile back into the folder.
  await stopAll()
  fs.rmSync(scratch, { recursive: true, force: true })
  process.exit(0) // The app's helper processes can hold its output pipes open a while.
}

/** Stops everything the test started and waits for it to end, at most 5 s. */
async function stopAll() {
  const running = launched.filter(child => child.exitCode === null && child.signalCode === null)
  for (const child of running) child.kill('SIGTERM')
  await Promise.race([Promise.all(running.map(child => child.exited)), sleep(5_000)])
}

main().catch(error => {
  console.error(`FAIL - ${error.stack || error}`)
  for (const child of launched) {
    const tail = child.output().slice(-2500)
    if (tail.trim()) console.error(`--- ${child.spawnargs.slice(0, 2).join(' ')}\n${tail}`)
  }
  console.error(`left in ${scratch}`)
  for (const child of launched) if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM')
  setTimeout(() => process.exit(1), 3000)
})
