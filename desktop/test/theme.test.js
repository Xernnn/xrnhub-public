'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const theme = require('../theme')

// The shape of an Omarchy theme's colors.toml (this one made up).
const DARK = `mode = "dark"

accent = "#798186"
selection = "#343d41"

background = "#101315"   # the window
foreground = "#CACCCC"
red = "#565d60"
`

test('a theme\'s colors.toml gives the frame its background, text and accent', () => {
  const colours = theme.frameColours(theme.parseColors(DARK), false)
  assert.deepEqual(colours, { mode: 'dark', fromTheme: true, frame: '#101315', text: '#cacccc', accent: '#798186', sheet: theme.SHEET.dark })
  assert.equal(theme.themeSource(theme.parseColors(DARK)), 'dark', 'the pages follow the theme\'s dark')
})

test('a light theme makes the pages light, whatever the system says', () => {
  const colors = theme.parseColors('mode = "light"\nbackground = "#fdf6e3"\nforeground = "#586e75"\n')
  assert.equal(theme.themeSource(colors), 'light')
  const colours = theme.frameColours(colors, true)
  assert.equal(colours.mode, 'light')
  assert.equal(colours.sheet, theme.SHEET.light)
  assert.equal(colours.accent, '#586e75', 'no accent: the text colour stands in')
})

test('without a usable theme the frame is the hub\'s own, light or dark as the system is', () => {
  for (const colors of [null, {}, theme.parseColors('background = "#101315"'), theme.parseColors('background = "red"\nforeground = "#ffffff"')]) {
    const dark = theme.frameColours(colors, true)
    assert.equal(dark.fromTheme, false)
    assert.equal(dark.mode, 'dark')
    assert.equal(theme.frameColours(colors, false).mode, 'light')
    assert.equal(theme.themeSource(colors), 'system')
  }
  // Colours without a mode: the theme's colours, the system's light or dark.
  const colors = theme.parseColors('background = "#101315"\nforeground = "#cacccc"')
  assert.equal(theme.frameColours(colors, true).fromTheme, true)
  assert.equal(theme.themeSource(colors), 'system')
})

test('lines that aren\'t key = "value" are skipped, and a missing file is no theme', () => {
  assert.deepEqual(theme.parseColors('[section]\nmode = dark\n  accent = "#112233" # note\nbad line\n'), { accent: '#112233' })
  assert.equal(theme.readColors(path.join(os.tmpdir(), 'no-such-theme', 'colors.toml')), null)
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'xrnhub-theme-'))
  fs.writeFileSync(path.join(dir, 'colors.toml'), DARK)
  assert.equal(theme.readColors(path.join(dir, 'colors.toml')).background, '#101315')
  assert.equal(theme.themeFile('/u/alex'), '/u/alex/.local/state/omarchy/current/theme/colors.toml')
})
