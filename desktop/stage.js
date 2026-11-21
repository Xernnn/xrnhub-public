'use strict'
// Copies what the sidebar shows from the hub's own page (../web): its icon, the apps' logos and the
// MiSans font, so both look the same and there is one copy of each in git. `--icon` also draws the
// launcher icon for packing. npm start, test:window and pack run it first.

const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const HERE = __dirname
const WEB = path.join(HERE, '..', 'web')
const VENDOR = path.join(HERE, 'shell', 'vendor')
const LOGOS = ['xrnloops.svg', 'xrnrule.svg', 'xrnhome.png', 'xrninsta.svg', 'xrnphotos.svg']

fs.rmSync(VENDOR, { recursive: true, force: true })
fs.mkdirSync(path.join(VENDOR, 'apps'), { recursive: true })
fs.mkdirSync(path.join(VENDOR, 'fonts'), { recursive: true })
fs.copyFileSync(path.join(WEB, 'icon.svg'), path.join(VENDOR, 'icon.svg'))
for (const logo of LOGOS) fs.copyFileSync(path.join(WEB, 'apps', logo), path.join(VENDOR, 'apps', logo))
for (const font of fs.readdirSync(path.join(WEB, 'fonts')).filter(name => name.endsWith('.woff2'))) {
  fs.copyFileSync(path.join(WEB, 'fonts', font), path.join(VENDOR, 'fonts', font))
}

if (process.argv.includes('--icon')) {
  fs.mkdirSync(path.join(HERE, 'build'), { recursive: true })
  execFileSync('rsvg-convert', ['-w', '512', '-h', '512', path.join(WEB, 'icon.svg'), '-o', path.join(HERE, 'build', 'icon.png')])
}
