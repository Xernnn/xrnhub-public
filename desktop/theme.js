'use strict'
// The window's frame takes the desktop's colours: Omarchy keeps the current theme's palette in
// ~/.local/state/omarchy/current/theme/colors.toml, and rewrites it when the theme changes. The
// pages keep their own look, as rounded sheets inside the frame, and follow the theme's light or
// dark. Off Omarchy, the frame uses the hub page's own greys.

const fs = require('node:fs')
const path = require('node:path')

const HEX = /^#[0-9a-f]{6}$/i
// The hub page's background (web/style.css --bg): the sheet a page sits on, so nothing flashes
// while it loads.
const SHEET = { dark: '#1f1f23', light: '#f2f2f4' }
const FALLBACK = {
  dark: { frame: '#141417', text: '#ececee', accent: '#22c55e' },
  light: { frame: '#e4e4e9', text: '#1b1b1e', accent: '#16a34a' }
}

function themeFile(home) {
  return path.join(home, '.local', 'state', 'omarchy', 'current', 'theme', 'colors.toml')
}

/** The `key = "value"` lines of a colors.toml; anything else in it is skipped. */
function parseColors(text) {
  const found = {}
  for (const line of String(text ?? '').split('\n')) {
    const pair = /^\s*([a-z_]+)\s*=\s*"([^"]*)"\s*(#.*)?$/.exec(line)
    if (pair) found[pair[1]] = pair[2]
  }
  return found
}

/**
 * The frame's colours from a theme's palette, or the hub's own when there is none (or it doesn't
 * name usable ones). `systemDark` decides light or dark when the theme doesn't say.
 */
function frameColours(colors, systemDark) {
  const mode = colors?.mode === 'light' || colors?.mode === 'dark' ? colors.mode : systemDark ? 'dark' : 'light'
  const fallback = FALLBACK[mode]
  const pick = (name, otherwise) => (HEX.test(colors?.[name] ?? '') ? colors[name].toLowerCase() : otherwise)
  const frame = pick('background', null)
  const text = pick('foreground', null)
  // Both or neither: a theme's text on the fallback's frame could be unreadable.
  const own = frame && text
  return {
    mode,
    fromTheme: !!own,
    frame: own ? frame : fallback.frame,
    text: own ? text : fallback.text,
    accent: own ? pick('accent', text) : fallback.accent,
    sheet: SHEET[mode]
  }
}

/** The current theme's palette from its colors.toml, or null when there is none. Never throws. */
function readColors(file) {
  try {
    return parseColors(fs.readFileSync(file, 'utf8'))
  } catch {
    return null
  }
}

/** What the pages' light or dark follows: the theme's own mode when it names one, else the system. */
function themeSource(colors) {
  return frameColours(colors, true).fromTheme && (colors.mode === 'dark' || colors.mode === 'light') ? colors.mode : 'system'
}

module.exports = { SHEET, frameColours, parseColors, readColors, themeFile, themeSource }
