'use strict'
// The sidebar, and the panels shown in the page area when there is no page to show: setting up,
// a password box, settings, and a page that couldn't load. The app (main.js) holds the state and
// sends it whole on every change; this draws it.

const api = window.xrnhub
const nav = document.getElementById('nav')
const content = document.getElementById('content')
const sheet = document.getElementById('sheet')
const host = document.getElementById('host')
const statusButton = document.getElementById('status')
const settingsButton = document.getElementById('settings')
const SVG = 'http://www.w3.org/2000/svg'

// The hub's own tab icons (web/app.js), and this app's Settings.
const GLYPHS = {
  server: 'M4 5h16v6H4zM4 13h16v6H4zM8 8h.01M8 16h.01',
  today: 'M5 12.5 9.5 17 19 7',
  review: 'M5 19V11M12 19V5M19 19v-5',
  apps: 'M5 4h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM15 4h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM5 14h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1zM15 14h4a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1z',
  settings: 'M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4'
}

// Services with no page of their own, as the status line names them.
const SERVICES = { calendar: 'The calendar server', xrnwal: 'xrnwal', xrntube: 'xrntube' }

// Chromium's codes for a server that isn't there, as opposed to a page that broke.
const UNREACHABLE = new Set([-7, -21, -100, -101, -102, -104, -105, -106, -109, -118, -130, -137, -138])

let state = null
let shownPanel = '' // which panel is drawn, so a state change doesn't wipe a half-typed form

function h(tag, attrs, ...children) {
  const node = document.createElement(tag)
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value)
    else if (key === 'class') node.className = value
    else node.setAttribute(key, value === true ? '' : value)
  }
  for (const child of children.flat()) if (child !== null && child !== undefined && child !== false) node.append(child)
  return node
}

function glyph(name) {
  const box = document.createElementNS(SVG, 'svg')
  box.setAttribute('viewBox', '0 0 24 24')
  box.setAttribute('class', 'glyph')
  box.setAttribute('aria-hidden', 'true')
  const line = document.createElementNS(SVG, 'path')
  line.setAttribute('d', GLYPHS[name])
  box.append(line)
  return box
}

function pageIcon(icon) {
  return /\.(svg|png)$/.test(icon) ? h('img', { src: `vendor/apps/${icon}`, alt: '' }) : glyph(icon)
}

function label(id) {
  return state.pages.find(page => page.id === id)?.label ?? SERVICES[id] ?? id
}

function drawNav() {
  const locked = !state.address || state.panel === 'setup'
  const items = []
  let group = null
  state.pages.forEach((page, index) => {
    if (page.group !== group) {
      if (group) items.push(h('div', { class: 'divider', role: 'separator' }))
      group = page.group
    }
    const down = state.status.down.includes(page.id)
    items.push(h('button', {
      type: 'button',
      class: 'item',
      'data-page': page.id,
      'aria-current': page.id === state.active && !state.panel ? 'page' : null,
      title: `${page.label}${index < 9 ? ` · Ctrl+${index + 1}` : ''}`,
      disabled: locked,
      onclick: () => api.open(page.id)
    }, pageIcon(page.icon), h('span', { class: 'label' }, page.label), down && h('span', { class: 'down', 'aria-label': 'down' })))
  })
  nav.replaceChildren(...items)
}

function drawStatus() {
  const { status } = state
  let text = 'Checking…'
  let tone = ''
  if (!state.address || state.panel === 'setup') text = 'Not set up'
  else if (status.reachable === false) [text, tone] = ['Can\'t reach the server', 'bad']
  else if (status.signedIn === false) [text, tone] = ['Signed out', 'bad']
  else if (status.down.length === 1) [text, tone] = [`${label(status.down[0])} is down`, 'bad']
  else if (status.down.length > 1) [text, tone] = [`${status.down.length} services down`, 'bad']
  else if (status.reachable) [text, tone] = ['All running', 'ok']
  statusButton.className = `status ${tone}`
  statusButton.querySelector('.label').textContent = text
  statusButton.title = status.down.length ? status.down.map(label).join(', ') : text
  statusButton.disabled = !state.address || state.panel === 'setup'
}

function field(name, title, attrs) {
  const input = h('input', { name, ...attrs })
  return { input, node: h('label', { class: 'field' }, h('span', null, title), input) }
}

function setupPanel() {
  const address = field('address', 'Server address', { value: state.address ?? '', placeholder: 'homeserver.example.ts.net', autocapitalize: 'none', spellcheck: 'false', required: true })
  const user = field('user', 'Name', { value: state.user ?? '', autocomplete: 'username', autocapitalize: 'none', spellcheck: 'false', required: true })
  const password = field('password', 'Sync password', { type: 'password', autocomplete: 'current-password', required: true })
  const error = h('p', { class: 'error', role: 'alert' })
  const submit = h('button', { class: 'primary wide', type: 'submit' }, 'Sign in')
  const inputs = { address: address.input, user: user.input, password: password.input }
  const onsubmit = async event => {
    event.preventDefault()
    error.textContent = ''
    for (const input of Object.values(inputs)) input.removeAttribute('aria-invalid')
    submit.disabled = true
    submit.textContent = 'Signing in…'
    const reply = await api.setup({ address: address.input.value, user: user.input.value, password: password.input.value }).catch(() => ({ error: 'Something went wrong' }))
    submit.disabled = false
    submit.textContent = 'Sign in'
    if (reply.ok) return
    error.textContent = reply.error
    const wrong = inputs[reply.field]
    if (wrong) {
      wrong.setAttribute('aria-invalid', 'true')
      wrong.focus()
      if (reply.field === 'password') wrong.select()
    }
  }
  const node = h('form', { class: 'panel', onsubmit },
    h('img', { src: 'vendor/icon.svg', alt: '' }),
    h('h1', null, 'Sign in to the home server'),
    address.node, user.node, password.node, error, submit,
    !state.keyring && h('p', { class: 'left small' }, 'No keyring: password not kept'))
  return { node, focus: state.address ? (state.user ? password.input : user.input) : address.input }
}

function signInPanel() {
  const q = state.question
  const sync = q.group === 'sync'
  const user = field('user', 'Name', { value: sync ? state.user ?? '' : '', autocomplete: 'username', autocapitalize: 'none', spellcheck: 'false', required: true })
  const which = sync ? 'Sync password' : q.group === 'calendar' ? 'Calendar password' : 'Password'
  const password = field('password', which, { type: 'password', autocomplete: 'current-password', required: true })
  const onsubmit = event => {
    event.preventDefault()
    api.signIn({ user: user.input.value, password: password.input.value })
  }
  const node = h('form', { class: 'panel', onsubmit },
    h('img', { src: 'vendor/icon.svg', alt: '' }),
    h('h1', null, `${q.label} asks for a password`),
    q.retry && h('p', { class: 'error', role: 'alert' }, 'Kept password didn\'t work'),
    user.node, password.node,
    h('div', { class: 'actions' },
      h('button', { class: 'quiet', type: 'button', onclick: () => api.signIn(null) }, 'Not now'),
      h('button', { class: 'primary', type: 'submit' }, 'Sign in')))
  return { node, focus: user.input.value ? password.input : user.input }
}

function settingsPanel() {
  const facts = h('dl', { class: 'facts' },
    h('div', null, h('dt', null, 'Server'), h('dd', null, state.address ?? '')),
    h('div', null, h('dt', null, 'Signed in as'), h('dd', null, state.user ?? '')),
    h('div', null, h('dt', null, 'Passwords'), h('dd', null, state.keyring ? 'In the system keyring' : 'Not kept (no keyring)')))
  const done = h('button', { class: 'primary', type: 'button', onclick: () => api.panel(null) }, 'Done')
  const node = h('div', { class: 'panel' },
    h('h1', null, 'Settings'),
    facts,
    h('div', { class: 'actions' },
      h('button', { class: 'quiet danger', type: 'button', onclick: () => api.signOut() }, 'Sign out'),
      done))
  return { node, focus: done }
}

function problemPanel() {
  const { problem } = state
  const away = UNREACHABLE.has(problem.code)
  const node = h('div', { class: 'panel' },
    h('img', { src: 'vendor/icon.svg', alt: '' }),
    h('h1', null, away ? 'Can\'t reach the server' : `${label(state.active)} didn't load`),
    h('p', { class: 'small' }, problem.description || `Error ${problem.code}`),
    h('div', { class: 'actions' }, h('button', { class: 'primary', type: 'button', onclick: () => api.retry() }, 'Try again')))
  return { node, focus: null }
}

function drawContent() {
  let key = ''
  let make = null
  if (state.panel === 'setup') [key, make] = ['setup', setupPanel]
  else if (state.panel === 'sign-in' && state.question) [key, make] = [`sign-in ${state.question.url} ${state.question.retry}`, signInPanel]
  else if (state.panel === 'settings') [key, make] = [`settings ${state.keyring}`, settingsPanel]
  else if (state.problem) [key, make] = [`problem ${state.problem.url} ${state.problem.code}`, problemPanel]
  if (key === shownPanel) return
  shownPanel = key
  if (!make) return content.replaceChildren(h('div', { class: 'loading', 'aria-label': 'Loading' }, h('span'), h('span'), h('span')))
  const { node, focus } = make()
  content.replaceChildren(node)
  focus?.focus()
}

/** The frame in the desktop theme's colours; the sheet and its panels in its light or dark. */
function drawTheme() {
  const { theme } = state
  if (!theme) return
  const root = document.documentElement
  root.dataset.mode = theme.mode
  for (const [name, value] of [['--frame', theme.frame], ['--text', theme.text], ['--accent', theme.accent], ['--sheet', theme.sheet]]) {
    root.style.setProperty(name, value)
  }
}

function drawHost() {
  // The server's first name (homeserver from homeserver.example.ts.net), beside the app's tile.
  let name = ''
  try {
    name = state.address ? new URL(state.address).hostname.split('.')[0] : ''
  } catch {
    name = ''
  }
  host.textContent = name
}

function draw(next) {
  state = next
  drawTheme()
  drawHost()
  drawNav()
  drawStatus()
  drawContent()
  settingsButton.disabled = !state.address || state.panel === 'setup' || state.panel === 'sign-in'
  settingsButton.setAttribute('aria-pressed', state.panel === 'settings' ? 'true' : 'false')
}

settingsButton.append(glyph('settings'))
settingsButton.addEventListener('click', () => api.panel(state?.panel === 'settings' ? null : 'settings'))
statusButton.addEventListener('click', () => api.open('server'))
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && state?.panel === 'settings') api.panel(null)
})
// The pages are drawn over the sheet, so the app is told exactly where it is, in window pixels.
let reported = ''
function reportSheet() {
  const box = sheet.getBoundingClientRect()
  const rect = { x: Math.round(box.left + sheet.clientLeft), y: Math.round(box.top + sheet.clientTop), width: sheet.clientWidth, height: sheet.clientHeight }
  const key = JSON.stringify(rect)
  if (key === reported) return
  reported = key
  api.sheet(rect)
}
new ResizeObserver(reportSheet).observe(sheet)
addEventListener('resize', reportSheet)

api.onChange(draw)
api.state().then(draw).then(reportSheet)
