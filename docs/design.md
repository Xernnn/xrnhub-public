# How xrnhub works

## Why it reads files

The hub runs on the same machine as the services it shows, so it reads their files. That needs no
stored password and adds no load to them.

| App | What it reads | How |
|---|---|---|
| xrnloops | `xrnloops-sync/data/users/<user>/loop.sqlite` | SQLite, opened read-only |
| xrnrule | `xrnrule-sync/data/users/<user>/state.json`, `events.jsonl` | JSON, swapped in by rename |
| xrnwal | `xrnwal-sync/data/users/<user>/state.json` | JSON, swapped in by rename |
| xrnhome | `xrnhome-sync/data/users/<user>/state.json` | JSON, swapped in by rename |
| Calendar | `radicale/collections/collection-root/<user>/*/*.ics` | Parsed once, again when a file changes |
| xrninsta | `xrninsta/data/xrninsta.sqlite`, one list, the owner's | SQLite, opened read-only, for the API |

xrnwal was called xrnwallet until 2026-11-21, and its folder, runit service and log were all
`xrnwallet-sync`. Its own deploy moves them, and that can land before the hub's or after it, so
while only the old names are there the hub reads those (`machine.home_folder`,
`Machine.runit_path`), and the new ones win as soon as they appear, with no restart. The monitor's
history kept under the old name takes the new one the first time the hub starts (`metrics.RENAMED`).

A service that isn't set up, or a file that won't parse, reads as empty. The page shows less; it
doesn't fall over.

The cost is coupling to those formats. `server/hublib/sources.py` is the only file that knows them,
bar two small things the Apps page needs: which files' times say an app last wrote, and the update
folders' `app.json` and `updates.xml` (both in `hublib/apps.py`).

## Why ticks go through xrnloops

A habit ticked here has to be a real tick: on the phone at its next sync, and counted by xrnrule. So
the browser writes it, with `POST /xrnloops/api/changes`, the same call xrnloops' own web app makes.
The sign-in cookie is shared and the origin is the same, so this needs no stored password, and the
hub's server stays read-only on everything it doesn't own.

A write carries `max(now, the record's mtime + 1)`, and the record's notes, so it wins over what it
replaces and doesn't wipe a note. The tile changes first and comes back if the write fails. Writes go
one at a time, so quick taps can't land out of order. If a write loses to a newer record from another
device, the page reads the truth back.

xrnrule is different: its state is one merged document with tamper rules, so nothing here writes it.
The page shows what a block is waiting for, and unlocking stays on the devices. That is also why a
nudge can only show a card.

## What the hub keeps

Only these are its own, in `data/users/<user>/`:

- `hub.sqlite`: events apps send to `/api/events` and which nudges fired, kept 400 days; and the
  health records xrnfit sends to `/api/health`, kept for good. Health is the one source with no
  copy anywhere else: Health Connect on the phone keeps only what's recent, and the vendor apps it
  replaced are gone. It sits in `hub.sqlite` because the laptop's backup copies that file with
  SQLite's backup, so it's consistent even mid-write.
- `monitor.sqlite`: the monitor's history, one file for the machine. Losing it loses charts, nothing
  else, so it isn't backed up.
- `rules.json`: the nudge rules. Edits need `If-Match`, so two tabs can't overwrite each other.
- `tokens.json`: the read-only API tokens, as SHA-256 hashes. Losing it loses the tokens, nothing
  else.

Everything else is worked out from the sources on each request.

## Days

A day is a habit day, as in xrnloops and xrnrule: 01:30 belongs to the day before when the day
starts at 03:00. The start hour comes from xrnloops.

A day counts toward this week's score only if at least a third of its habits have an entry. Otherwise it
wasn't logged, which isn't the same as doing nothing. A habit that isn't archived hasn't stopped,
however long since its last entry, so its quiet days still count against "was this day logged".
Pages still show a thin day: a tick on an old day has to appear even if the day is too thin to count.

A habit's month shows its share of the days anything was logged, for the same reason.

Health keeps its own days, the calendar's: a reading counts on the day it starts, a sleep on the
day it ends (`hublib/health.py`). A page that shows a date shows that date's health, without moving
it to habit days. So between midnight and the day's start hour, while the pages still show
yesterday, the health they show is yesterday's calendar day too, and the steps walked after
midnight appear in the morning, when the page turns over. Nothing is counted twice or lost, and on
Review each column is the same date for the habit strips and the health strips beneath them.
Moving health onto habit days instead would make a page's Monday a different Monday from the
API's and from the one xrnfit shows on the phone.

## Health on the pages

Review has a card for it beside the habits: a strip per measure over the habit strips' 30 days,
with the month's average and the latest. Steps and sleep are amounts, drawn as bars from zero;
resting heart rate, weight, SpO2 and stress are levels, drawn as dots between the month's lowest
and highest, so a kilo shows. Each strip has its own scale and no two share an axis. A measure
that hasn't come in this month gets no row, and the card goes when nothing has. Today's steps are
left out of the steps average: a day still going would pull it down all morning. A tap on a day
opens the day sheet, which lists that day's health with the sleep's stages, where there's room for
them. The table under the card has every number, so nothing depends on hovering.

Distance and active energy (since 2026-11-17, sent by xrnfit from the watch's minutes) are kept and
served by the API, day by day and as today's totals, but no page draws them yet: they follow
steps so closely that a strip each would mostly repeat the steps one.

Today has a tile for each of four: the day's steps so far, the night that ended that morning (the
night before until it comes in), the newest resting heart rate and the newest weighing, each over
the last two weeks. Steps and sleep are bars against the fortnight's average, the other two dots.
When nothing has come in for six hours, the steps tile says when the last reading did instead of
the average. A weight's change is against the weighing nearest a week before it, from 4 to 14 days
back, and every source's weighings count, as they do for weight everywhere; with none in that span
the tile gives the weight alone. The phone app has no Today, so these are the page's only.

## Nudges

A rule is a list of conditions, all of which must hold. Its one effect is a card on Today, checked
each time the page asks, so a card goes the moment it stops being true. A card's menu hides it for
the rest of the day, or turns its rule off: the page reads the rules and saves them back with that
one off, under `If-Match`, and puts the switch onto the newer rules once if they changed in between.
Today's answer counts the rules that are off (`nudgesOff`), and while any is, the page lists every
rule with its switch at its foot, folded. Each rule that fires is written down once a day in
`hub.sqlite`, in the background too, and a dismissal is kept against that.

There was a Nudges page for writing rules, with a list of what fired lately. It went on 2026-11-20,
since only the cards were ever used. `PUT /api/rules` still takes a whole new list, so a rule can
still be written by hand.

## The monitor

`hublib/machine.py` is the only file that knows where the phone keeps things. Android shuts most of
`/proc` to apps: `/proc/stat`, `/proc/loadavg`, `/proc/uptime`, `/proc/net/*`, `/proc/diskstats` and
the battery files in `/sys` are all refused, even though the services run as one Linux user and can
read each other's `/proc/<pid>`. What's left:

| Wanted | Where it comes from |
| --- | --- |
| Uptime, load | `sysinfo(2)`, which needs no file |
| Overall CPU | Not available. Load average, core speeds and each service's own CPU stand in |
| Each service | `/proc/<pid>/stat` and `io`, with the pid from runit's `supervise/` |
| Supervision | Every `/proc/<pid>/cmdline` and parent the services' user can see: runsvdir, each runsv, each program |
| Temperature | `/sys/class/thermal`. Zones are found once, then only the CPU and battery ones read |
| Network | Tailscale's local API. All public traffic goes through it, so its totals are what matter |
| Battery, Wi-Fi | `termux-battery-status`, `termux-wifi-connectioninfo` |
| Requests | The services' logs. They hold no response sizes, so bytes per request are unknown |

Anything that gives no answer reads as None and the page shows less. Wi-Fi and Tailscale output is
cut down to the fields shown: no network name, MAC address, keys or peer addresses.

**Sampling.** A sample a minute goes to the database, always. An open page polls every 3 seconds
and that alone turns on sampling at that speed; 20 seconds after the last poll it stops. One sample
of everything took a few milliseconds on the phone, and the Termux commands about 75 ms each, so
those run once a minute at most.

**Liveness is an answer, not a connection.** Once a minute each service is asked for its health
page, with 3 seconds to reply, and anything short of a server error counts as up. It used to be a
TCP connect, which on 2026-11-15 called xrnloops and xrntube up for half an hour while the public
address gave 502: Android had killed runit's supervisor, and the old programs it left behind still
held the ports and took connections but never replied. The request leaves a line in the service's
log; the log reader drops that one line (the health path, asked from the phone itself), so the
check never shows as traffic.

**Supervision.** The same minute reads every process the services' user can see and checks runit
itself: exactly one runsvdir, a runsv for each service whose parent is that runsvdir, and no copy of
a service's program whose parent is pid 1. Without runsvdir a service that stops stays stopped; a
copy nobody supervises can hold the port so the supervised one can't start, and a restart from the
page won't reach it. These are the `supervisor` and `unsupervised` problems. They need a person:
the fix (stop the left-over pid, restart runit or the phone) is in the home-server README. The
laptop's `homeserver` report makes the same check, worded the same way.

**Keeping it small.** Minutes stay 8 days, then hours stay 400. An hour row keeps `n`, how many
minutes went into it, so uptime stays true after rolling up. Traffic is added to its hour row when
it's read, not rolled up later, because a log read for the first time carries minutes older than
the 8 days. Paths and addresses are capped per day: a scanner can make up as many as it likes.

**Logs.** `logscan.Follower` keeps an inode and offset per folder, saved with the counts they
produced, so a restart picks up at the same line. svlogd renames `current` when it rotates, so a
rotated file keeps its inode and can be finished. Query strings are stripped on the way in and
never leave `logscan`: they aren't needed and could hold anything.

The hub's own log keeps to what happened. Funnel's proxy holds kept-alive connections open, and
one closed for idling after a request isn't news, so it goes unlogged; a timeout before the first
request or partway through one is still a line. A caller that resets or hangs up mid-answer is one
line, `connection dropped`, where socketserver printed a traceback the log view counted as an
error. Every other exception keeps its traceback.

**Problems.** Each minute the page's conditions are checked and incidents opened and closed in
`incidents`. A service that stops answering only counts after 90 seconds, since a restart takes a
few. A reading that goes missing can't close a problem it would have to clear. A restart, a reboot
or a gap in the samples is noted as something that happened.

**Restart.** `sv restart` on a fixed list: Radicale, the six sync services and xrninsta. Never the hub, which
couldn't answer; never ssh or Tailscale, which could cut off the only way in. Once a minute per
service. It's the only thing here that acts on another service, and it touches no data.

**Names.** The page calls Radicale `xrncal`, after the app that syncs with it, though xrncal itself
runs on the laptop. The API and `monitor.sqlite` keep the name `calendar`, so renaming it on the page
leaves past charts whole. The logos in `web/apps/` are copies from each app's repo, so a new icon
there has to be copied here. Tailscale and ssh aren't xrn apps: they get a plain grey tile, not the
family's marked one and not Tailscale's own logo.

**xrnfit.** It has no service of its own: the health it sends lives in the hub's `hub.sqlite`. The
Data page still counts it as xrnfit's, since that's whose data it is: a row a reading (each
heart-rate, SpO2 or stress sample is one, as the API's `held` counts them), "came in" on the day a
row was written, so a resend counts again on its new day, and the bytes the health table takes,
from SQLite's `dbstat`, moved off the hub's share so nothing is counted twice. A reading a minute
from the watch makes it much the biggest bar on "Came in"; the table under the chart keeps the
small ones readable. Its logo is violet like xrninsta's, so as with xrnloops and xrnwal its line
is dashed and the stacks keep the two apart. Its traffic is counted the same way: a request in the
hub's log to `/api/health` (xrnfit sending) or under `/api/v1/health` (xrnfit, or a script, reading
health) counts as xrnfit's on the Server and Traffic pages, not the hub's. Before 2026-11-17 it
counted as the hub's, and the pages showed no trace of xrnfit at all.

**xrnfit on the Overview.** The Overview listed only services, so xrnfit never showed on the first
page anyone sees, though it sends more than everything else together. It sits in the Overview's
list of services and on the Services page right after the hub: a dot, and how long ago it last
sent; on Services its strip counts the readings that came in each day. It used to have a card of
its own under a status banner, with today's figures; the Server pages' redesign (#25) dropped both,
and Today and Review show those figures. It comes in `/api/server/now` as `health`, beside
`services` rather than among them, because everything that reads `services` takes a row for a runit
service that is up or down and can be restarted. `health` carries when it last sent something and
how many readings came in this week (the Apps page's Last sync and week, from the same code), and
today's steps, sleep, resting heart rate and weight as Today's tiles work them out, with the newest
SpO2 and stress. It is kept a minute, since the page asks every 3 seconds and "last sent" reads the
whole health table (a million rows take about 0.2 s on the laptop); a batch from xrnfit drops it, so
"12m ago" is never behind. After six hours with nothing the dot turns amber: xrnfit sends every few
minutes while the watch is in reach, so a quiet morning means it has stopped, most likely HyperOS
refusing its service without Autostart.

**Who sees it.** The monitor shows every visitor's address and can restart things, so it's for the
first account in the users file. Any other account gets 403 and keeps the other pages.

## Charts

Calendar hours use the colours set in the calendar, so a colour means the same thing here and there.
They fail a generated palette's lightness and chroma checks but pass colour-blind and normal-vision
separation, and the legend and table carry the names anyway. Habit tiles use xrnloops' colours.

Each service keeps one colour on every Server chart, from a palette checked for colour blindness in
both themes; two of them fall under 3:1 against the light surface, so every chart has a legend and a
table view. Green, amber and red mean status and nothing else, and always come with words. A gap in
a line means nothing was measured, so it stays a gap. There's no second axis anywhere: two measures
get two charts.

## The phone app

**Native, not the page in a box.** The app is Compose, drawing its own charts on a canvas, so a tap
answers at once and scrolling stays smooth. It repeats no logic the server owns: every reading comes
from the API, and only how numbers read is written twice, in `web/serverkit.js` and
`android/.../data/Words.kt`, and the Apps page's words in `web/apps.js` and `data/Apps.kt`. Each pair
has to stay in step.

**Charts on a touchscreen.** There's no hover, and a finger hides what's under it, so there's no
floating tooltip. A press or a drag moves a crosshair, and the numbers change in a line above the
plot, always in the same place. Each chart keeps its Table view, so nothing depends on colour alone.

**Asking only while looked at.** Polling runs while the app is on screen and a live page is open.
Reading `/api/server/now` is what switches the phone to sampling every few seconds, so leaving the app
lets the server go back to once a minute. Connections stay open between polls, which saves a TLS
handshake every three seconds.

**Alerts come from your phone asking, not the server telling.** A push needs a third party holding a
device token and the server's news, and it can't report the case that matters most: a server that's
off can't send anything. So a background job on the phone asks every 15 minutes, the shortest gap
Android gives ordinary apps, and an answer that doesn't come is itself the alert. The cost is delay:
Android puts jobs off further while a phone sits idle, so this tells you within the hour, not the
minute. It reads the same `problems` list as the Overview's alerts, so the two can't disagree.

The job is a plain JobScheduler job, not WorkManager, and that's load-bearing. While the app's
process is alive, even idle, WorkManager runs due work inside it rather than as a job, and Android
blocks the network for idle apps. The check then fails instantly and looks exactly like an outage,
which is how the first version raised a false alarm on the real phone. A real job gets the network.
If Android refuses it anyway, that's recorded as not having asked, never as the server being gone,
and six hours of that gets its own note, because a monitor that stops in silence is the worst kind.

One failed check is retried after two minutes, and only a second failure alerts, since a network
blink isn't an outage. Each problem is said once, and when it's over its note is replaced by a silent
one in a separate channel, so "back to normal" never wakes anyone and can be switched off alone.
Wording says "Server" where the page says "Phone": a notification reading "Battery low" lands on the
phone in your hand. The job also renews the session, so alerts outlast a month of not opening the
app, and if the session does end it says so once and stops, rather than failing in silence.

**The sign-in.** The password goes once, over https, to `POST /api/session`, and only the session
comes back to be kept, sealed with a keystore key. Sessions last 30 days and the same call renews a
live one, so an app in regular use never asks again. A failed renewal changes nothing. Redirects are
never followed, since a session isn't for wherever one points. Debug builds alone may use plain http,
and only to this device's own loopback, for the demo server.

**Read-only, bar one button.** The app can restart a service, as the page can, behind a confirmation.
It can't tick habits: the day sheet shows them, and fixing one stays on the web page.

## The personal API

`/api/v1` (`hublib/api.py`, described in [api.md](api.md)) answers with records, not pages: every
transaction in a range, each habit entry, the calendar with its repeats worked out. It's built on
the same `Sources` as the pages, so the coupling to each app's format stays in `sources.py`; what
it adds is choosing fields and naming them for someone who hasn't read the apps' code.

**Field by field.** Nothing passes through whole. xrnrule's settings hold the sentence that buys
minutes and xrnwal's hold the PIN's hash and salt; a record copied as it is would carry the next
field either app adds. Tests check for both.

**Balances are the wallet's arithmetic, copied.** An account's balance isn't stored: the app works
it out from the opening balance, every bill, fees and savings deposits, with amounts in another
currency fixed at the rate they were saved with. `api.Wallet` follows `Snapshot.kt` line for line,
rounding halves up as Kotlin's `roundToLong` does. A change to how the app sums has to be made here
too.

**Tokens, not the password.** The sync password writes to every service, so it's the wrong thing
to give a script or an assistant. A token is checked only on `GET /api/v1/...`; everywhere else it
reads as a wrong password. Only a SHA-256 of its 256-bit secret is kept: nothing guesses that, so
there's no need for bcrypt's cost on every request. Tokens are made on the server's command line,
not over the network, so a stolen session can't mint one. Wrong ones count toward the same lockout
as wrong passwords.

**The calendar keeps a little more.** Events now hold their location and description, for the API,
cut at 200 and 1,000 characters: there are some 9,500 in memory on the phone, and an invitation can
carry pages of joining instructions.

## The Apps page

Fourteen projects, each with where it runs and whether its repo is public, and then whatever the
hub can see of it right now: figures and short labels, no description of what each app is for.

**The catalogue is written by hand.** Where an app runs lives nowhere a program could read, so
`hublib/apps.py` lists it: the places, the repo, the monitor's name for its server, the
inventory's name for what it holds, its Android package and its own page on this host. A new
project is a new line there, a logo in `web/apps/` and `res/drawable-nodpi/`, and, if it's a phone
app, its package in the manifest's `<queries>`; `tests/test_apps.py` fails until all of those are
in. The products some apps are modelled on aren't named on the cards: the
family keeps other products' names out of its apps, and each README says what it follows.

**Joined, not scanned.** `/api/apps` reads nothing the hub wasn't reading already:

| On a card | From |
|---|---|
| Server up or down, for how long | The monitor's last look, as on the Server page |
| What it holds, how much came in this week | The inventory's last sweep, every 10 minutes, by the Data page's names; dated records only for the week |
| Last sync | The newest of the app's own files' times here, the newest event it sent the hub, xrnfit's newest health record |
| Last backup (home-server) | The inventory's newest backup copy |
| Released | The service's `data/update/`: a phone app's `app.json`, an extension's `updates.xml` |

xrnfit's count comes from the inventory once it counts health records, under the name `xrnfit`; the
page needs no change for it. An answer is kept 30 seconds, and the release files are read again
only when they change, so a page polling every minute costs a few `stat`s.

**Versions are only where something hands them out.** On 2026-11-17 the server holds three update
channels: xrnrule's phone app and extension, xrnhome's phone app and xrntube's extension. Every
other version lives only on a device. The phone app reads its own phone's with PackageManager,
which from Android 11 answers only for packages the manifest names. An update counts by
versionCode when both sides have one, as Android's installer does, and by the dotted name
otherwise. The desktop apps and Zen's extensions aren't seen at all: nothing on the laptop reports
a version.

**Who sees what.** The live part reads the owner's files and sums every account's records, so it's
the owner's, like the monitor. Anyone else signed in gets the catalogue, up or down, and the
releases.

## Security

- The origin is shared with the other services on the host, so the page never builds HTML from
  data: everything goes in through `textContent`, and a test greps for the ways round that. The CSP
  allows no inline scripts or styles.
- Login is the shared `xrn_session` cookie, which counts only with an `X-Xrn-Client` header, or Basic
  auth for devices. Ten wrong passwords from one address in 15 minutes get a 429. A right password
  clears only the mistakes made under its own name from that address; a password the hub already
  knows is right (a device syncing), a token and a cookie clear nothing.
- Signing out (`DELETE /api/session`, the page's sign-out button) ends that cookie on the server,
  not only in the browser, and on every server that takes it: a SHA-256 of it is kept in
  `signed-out.json` beside the users file and `session.key` until it would have run out, and the
  hub, xrnloops-sync, xrnrule-sync and xrninsta all refuse what it lists (each signs out into it
  too, under a lock on `signed-out.json.lock`). The cookie's format is unchanged. The hub's own
  `<data>/signed-out.json` from before is moved onto that list at start and renamed
  `signed-out.json.migrated`. A new sync password ends every session everywhere.
- xrnrule's settings hold the sentence that buys extra minutes. `block_view` picks the fields it
  passes on, so they stay on the server. A test checks.
- `/api/events` keeps metadata. xrnmail reports how many messages are waiting, never who from.
  A kind starting `state.` says where an app stands: its card reads the newest one, and a day's log
  leaves it out.
- `/api/apps` passes on counts, times and versions, nothing from inside a file: a release whose
  version isn't digits and dots is left out, and a release file over 64 KB isn't read.
- `/api/health` takes the sync password or the cookie, never a read-only token, so a script that can
  read your heart rate can't write one. It refuses a whole batch for one implausible reading (a
  bpm of 300, a weight of 900 kg, a time in 2100), so a bad sender can't quietly skew a chart.
  Read-only tokens do read health, like everything else under `/api/v1`.

## Not built yet

- Logging time from the hub: the calendar has its own password, and the hub stores none.
- One backup and one update system across the apps.
- A sync layer the apps could share in place of their own servers.
