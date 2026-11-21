# The personal API

Everything the xrn apps know about you, as plain JSON, under `/xrnhub/api/v1`. It only reads: each
answer is worked out when it's asked for, from the services' files, or for health from what xrnfit
posts to the hub. It's meant for scripts, and later for an assistant.

What's in it: habits (xrnloops), the calendar (Radicale), money (xrnwal), the home inventory
(xrnhome), the song-of-the-day list (xrninsta), blocks and what the devices logged (xrnrule), what
apps report to the hub, and health: steps, heart rate, sleep, weight, SpO2 and stress (xrnfit). What
isn't: xrntube's blocklist, mail (it stays on the laptop; xrnmail only sends counts), and the
server monitor, which keeps its own pages under `/api/server`.

## Signing in

Use a read-only token:

```sh
curl -H "Authorization: Bearer xrnhub_…" https://<server>/xrnhub/api/v1
```

A token reads `/api/v1` and nothing else. It can't tick a habit, post an event, see the monitor or
sign a browser in. The sync password, which works here too, can write to every service, so give a
script a token instead.

Tokens are made on the server, which keeps only their hash:

```sh
cd ~/xrnhub
python xrnhub.py token add "laptop scripts" --data ~/xrnhub/data --users ~/.config/xrn-sync/users
python xrnhub.py token list ...       # id, when it was made, its name
python xrnhub.py token revoke <id> ...
```

`add` prints the token once. A token reads its user's data: the first user in the users file
unless `--user` says otherwise. It stops working when it's revoked, or when its user leaves the
users file, and the running hub notices either at once. A new sync password doesn't end it: after a
leak, revoke the tokens too. Wrong tokens count toward the same limit as
wrong passwords: ten from one address in 15 minutes, then 429. Using a right token clears none of
them.

## How answers read

- **Times** are ISO 8601 with the server's offset: `"2026-11-15T16:04:00+11:00"`. A whole day is
  `"2026-11-15"`.
- **Ranges** are `from` and `to`, both days, both included. Each endpoint says what it does without
  them. A habit day starts at xrnloops' day start hour (`dayStartHour`, 3 by default), so 01:30
  belongs to the day before. Everything else counts days on the server's clock.
- **Money** is whole cents, with the currency code beside it. `baseCents` and `…BaseCents` are in
  the wallet's base currency (`currency` on `/money`).
- **Every answer is an object**, so new fields can appear. A caller should ignore what it doesn't know.
- **Errors** are plain text: 400 for a bad parameter (`from must be a day, as 2026-11-15`), 401 with
  no or a wrong login, 404 for a path that isn't here, 429 after too many wrong logins.
- A source that isn't set up reads as empty, not as an error.

## Endpoints

### `GET /api/v1`

What's here. `sources` says how much each app holds and when its files last changed (`updated`);
`endpoints` lists every path below with a line about it and its parameters.

### `GET /api/v1/habits`

Every habit in xrnloops' order, archived ones included.

```json
{"today": "2026-11-15", "dayStartHour": 3, "habits": [
  {"id": "…", "name": "Gym", "question": "Did you go?", "description": "", "kind": "yesno",
   "frequency": {"times": 3, "days": 7}, "archived": false,
   "today": {"done": true, "mark": "yes"}, "streak": 4},
  {"id": "…", "name": "Water", "kind": "measurable", "target": {"value": 8, "unit": "glasses", "atMost": false},
   "today": {"done": false, "amount": 5.0}, "streak": 0, "…": "…"}]}
```

- `kind` is `yesno` or `measurable`. A measurable habit has a `target`; `atMost` means staying under
  it is the goal.
- `today` is null when nothing is entered yet.
- `streak` counts days in a row for daily habits (0 for the others). A skip neither breaks it nor
  adds to it, and today not done yet doesn't end it.

### `GET /api/v1/habits/entries?from=&to=&habit=`

Entries day by day; by default the last 30 habit days. `habit` is an id from `/habits`.

```json
{"from": "2026-10-17", "to": "2026-11-15", "entries": [
  {"habit": "…", "name": "Gym", "date": "2026-11-14", "done": true, "mark": "yes", "note": "legs"},
  {"habit": "…", "name": "Water", "date": "2026-11-14", "done": true, "amount": 9.0}]}
```

`mark` is `yes` (ticked), `auto` (filled in by xrnloops because the habit's frequency was already
met), `no` or `skip`. A day with no entry isn't listed. `note` appears only when there is one.

### `GET /api/v1/calendar?from=&to=`

Events in the range, each repeat worked out; by default today and the next 30 days, at most 400
days at once.

```json
{"from": "2026-11-15", "to": "2026-12-15", "ready": true,
 "calendars": [{"name": "Work", "color": "#1c6fd8"}],
 "events": [
  {"title": "Standup", "calendar": "Work", "allDay": false,
   "start": "2026-11-16T09:00:00+11:00", "end": "2026-11-16T09:15:00+11:00",
   "location": "Room 4", "description": "…"},
  {"title": "Holiday", "calendar": "Personal", "allDay": true, "start": "2026-11-20", "end": "2026-11-22"}]}
```

- An all-day event's `end` is the day after its last, as iCalendar writes it: the holiday above is
  the 20th and the 21st.
- `ready` is false for the first few seconds after the hub starts, while it reads the calendar; the
  list is empty until then.
- `location` and `description` are cut at 200 and 1,000 characters.

### `GET /api/v1/money`

The wallet's standing: accounts and their balances, net worth, categories, budgets, repeating bills
and savings plans.

```json
{"currency": "AUD", "monthStartsOn": 1,
 "netWorth": {"assetsCents": 520000, "debtsCents": -15000, "netCents": 505000},
 "accounts": [{"id": 1, "name": "Everyday", "kind": "savings", "currency": "AUD", "balanceCents": 120000,
               "balanceBaseCents": 120000, "inNetWorth": true, "archived": false, "note": ""}],
 "categories": [{"id": 11, "name": "Coffee", "type": "expense", "parent": "Food", "active": true}],
 "budgets": [{"category": null, "amountCents": 150000}, {"category": "Coffee", "amountCents": 3000}],
 "recurring": [{"type": "expense", "amountCents": 2000, "currency": "AUD", "category": "Rent", "account": "Everyday",
                "note": "", "starts": "2026-01-05", "repeats": {"kind": "MONTHLY", "value": 5, "every": 1},
                "times": null, "made": 3}],
 "savings": [{"name": "Trip", "targetCents": 200000, "currency": "AUD", "savedCents": 2500, "account": "Everyday", "note": ""}],
 "ledgers": [{"id": 1, "name": "Daily"}],
 "rates": {"VND": 0.00006}}
```

- Balances are worked out as the app does: the opening balance, every bill, transfers and their
  fees, and savings deposits, with amounts in another currency at the rate they were saved with.
- Net worth counts accounts marked to count, not archived. A credit account, or any below zero, is a
  debt.
- A budget with a null `category` is the whole month's. A month starts on `monthStartsOn`.
- `recurring.times` is null for a bill that repeats for ever; `made` is how many have been entered.
- `rates` turn one unit of each currency into the base currency.

### `GET /api/v1/money/transactions?from=&to=`

Every expense, income and transfer in the range, oldest first; by default the last 30 days. There's
no limit on the range: `from=2000-01-01` gets them all.

```json
{"from": "2026-10-17", "to": "2026-11-15", "currency": "AUD",
 "totals": {"expenseCents": 84250, "incomeCents": 500000},
 "transactions": [
  {"id": 2, "at": "2026-11-14T08:12:00+11:00", "type": "expense", "amountCents": 550, "currency": "AUD",
   "baseCents": 550, "category": "Coffee", "parentCategory": "Food", "account": "Card", "ledger": "Daily",
   "note": "flat white", "tags": ["work"], "inBudget": true, "repeating": false, "hasPhoto": false},
  {"id": 3, "type": "transfer", "account": "Everyday", "toAccount": "Dong", "feeCents": 300,
   "toAmountCents": 170000000, "…": "…"}]}
```

- `totals` are in the base currency, transfers left out.
- `toAmountCents` is what landed in an account of another currency, in that account's currency.
- Photos stay on the server; `hasPhoto` says there is one.

### `GET /api/v1/inventory`

Everything in xrnhome, by room and category.

```json
{"items": [
  {"id": "…", "name": "Milk", "room": "food", "category": "Fridge", "quantity": 0, "unit": "bottle",
   "openedPercent": null, "expires": null, "onShoppingList": true, "restocksItself": true, "note": "",
   "created": "2026-01-01T00:00:00.000Z", "updated": "2026-11-14T08:00:00.000Z", "hasPhoto": true,
   "history": [{"date": "2026-11-14T08:00:00.000Z", "action": "Used 1 bottle", "quantityBefore": 1, "quantityAfter": 0}]}]}
```

`onShoppingList` leaves out what was snoozed. `created`, `updated` and history dates are as the app
wrote them, in UTC.

### `GET /api/v1/songs`

The song-of-the-day list kept by the xrninsta bot, oldest day first, and what's on hold.

```json
{"latestDay": 291, "genres": {"VPOP": 125, "USUK": 71},
 "songs": [{"id": 2, "day": -5, "title": "…", "artists": "…, …", "genre": "VPOP", "added": null},
           {"id": 300, "day": 291, "title": "…", "artists": "…", "genre": "JPOP", "added": "2026-11-16T21:04:11+11:00"}],
 "onHold": [{"id": 2, "title": "…", "artists": "", "note": "…", "added": null}]}
```

- `artists` is as the bot keeps it, several separated by commas.
- `added` is null for songs brought over from the Google Sheet, which kept no times.
- It's one person's list, so it's the server owner's (the first user in the users file); anyone
  else gets empty lists.

### `GET /api/v1/blocks`

xrnrule's blocks: what each one covers, whether it's open now, today's use, and what it waits for.

```json
{"day": "2026-11-15", "blocks": [
  {"id": "…", "name": "Social", "enabled": true, "mode": "allowance", "state": "open",
   "usedMs": 600000, "limitMs": 1800000, "until": null,
   "apps": ["com.example.feed"], "sites": ["example.com"], "exceptSites": [], "desktopApps": [],
   "needs": [{"text": "Go to the gym", "habit": "…", "habitName": "Gym", "done": true}]}]}
```

- `mode` is `habit`, `allowance`, `schedule` or `lockdown`; `state` is `open`, `blocked` or `off`.
  `until` is when an open session, pause or lockdown ends.
- This is a reading, as the hub's Today page shows it. The devices' own engine decides what's
  actually blocked.
- xrnrule's settings, the sentence that buys extra minutes among them, never leave the server.

### `GET /api/v1/blocks/events?from=&to=`

What the devices logged, oldest first; by default the last 7 days. xrnrule keeps 120.

```json
{"from": "2026-11-09", "to": "2026-11-15", "events": [
  {"at": "2026-11-14T21:03:00+11:00", "type": "blocked", "device": "phone-…", "name": "Feed", "target": "com.example.feed"},
  {"at": "2026-11-15T07:00:00+11:00", "type": "unlock", "device": "laptop-…", "blockId": "…", "block": "Games",
   "ms": 3600000, "start": "2026-11-15T07:00:00+11:00"}]}
```

`type` is `blocked` (something blocked was opened), `unlock`, `habit` (a habit done toward a block),
`break` or `bought` (minutes bought with the sentence). `block` is null for a block that has since
been deleted.

### `GET /api/v1/events?from=&to=&app=`

What apps reported to the hub with `POST /api/events`, oldest first; by default the last 7 days.
Today that's xrnmail's counts: how many messages are waiting, and when the next batch lands.

```json
{"from": "2026-11-09", "to": "2026-11-15", "events": [
  {"at": "2026-11-15T09:00:00+11:00", "app": "xrnmail", "kind": "state.held", "title": "", "value": 4,
   "detail": {"held": 4, "nextBatch": 1763200800000}}]}
```

At most 2,000 events come back at once; ask for a shorter range to see the rest.

## Health

Steps, distance, active energy, heart rate, sleep, weight, SpO2 and stress. xrnfit sends them from
the phone: the watch's,
which it fetches from the watch itself (before 2026-11-16 Gadgetbridge did), and the scale's
weighings. The history importer sends what the old apps held. The hub keeps them for good, since
there's no other copy (see "Sending health records" below).

- **One source a day.** Sources can overlap (the watch through xrnfit and through Gadgetbridge on
  the day xrnfit took over; the watch and an old app's export), so for each day and kind (steps,
  distance, active energy, heart rate, resting heart rate, sleep, SpO2, stress) one source counts: the first of `xrnfit-watch`,
  `gadgetbridge`, `xrnfit-scale`, `import-mifitness`, `import-renpho`, `import-googlefit` that has
  something that day, then any other by name. Weighings don't add up, so every source's are listed.
- **Days** are on the server's clock. A reading belongs to the day it starts on, and a sleep to the
  day it ends on: Monday night's sleep is Tuesday's.
- **Asleep** is every stage but `awake`: `light`, `deep`, `rem`, and `asleep` (a source that doesn't
  say which). A sleep without stages counts whole as `asleep`. Time between stages isn't counted.

### `GET /api/v1/health`

Today at a glance.

```json
{"today": "2026-11-15", "steps": 4321, "distance": 3180, "activeEnergy": 164,
 "sleep": {"day": "2026-11-15", "start": "2026-11-14T23:10:00+11:00", "end": "2026-11-15T06:40:00+11:00",
           "minutes": 412, "source": "gadgetbridge", "stages": [{"start": "…", "end": "…", "stage": "light"}]},
 "heartRate": {"latest": {"at": "2026-11-15T16:02:00+11:00", "bpm": 72}, "resting": 56},
 "weight": {"at": "2026-11-15T07:05:00+11:00", "kg": 72.35, "impedance": 512},
 "spo2": {"at": "2026-11-15T04:12:00+11:00", "percent": 96},
 "stress": {"at": "2026-11-15T15:55:00+11:00", "level": 31},
 "held": {"steps": 2911, "heartRate": 86400, "sleep": 120, "weight": 40, "spo2": 3100, "stress": 2400,
          "distance": 2600, "activeEnergy": 2600, "first": "2026-06-12", "last": "2026-11-15"}}
```

- `steps` is null before anything has come in today, and so are `distance` (metres) and
  `activeEnergy` (kcal burnt moving, resting metabolism left out). All three are today's totals.
- `sleep` is last night's: the longest that ends today, or yesterday's until today's lands. Null if neither.
- `heartRate.latest` is the newest sample from any source. `resting` is the newest day's resting rate.
- `weight` is the newest weighing. `impedance` is there when the scale measured one.
- `spo2` and `stress` are the newest sample from any source: blood oxygen in percent, and the watch's
  stress score from 1 to 100. Null until one has come in.
- `held` counts rows (each heart-rate, SpO2 or stress sample is one, and so is each steps, distance
  or active-energy record), and gives the first and last day with anything.

### `GET /api/v1/health/days?from=&to=`

Day by day, every day in the range, empty ones too; by default the last 30, at most 400 at once.

```json
{"from": "2026-11-14", "to": "2026-11-15", "days": [
  {"date": "2026-11-14", "steps": 8123,
   "sleep": {"minutes": 412, "deep": 80, "light": 250, "rem": 60, "awake": 22, "asleep": 0,
             "start": "2026-11-13T23:10:00+11:00", "end": "2026-11-14T06:54:00+11:00"},
   "heartRate": {"min": 48, "avg": 71, "max": 152, "resting": 56}, "weightKg": 72.3,
   "spo2": {"min": 92, "avg": 96, "max": 99}, "stress": {"min": 8, "avg": 31, "max": 77},
   "distance": 6040, "activeEnergy": 312},
  {"date": "2026-11-15", "steps": null, "sleep": null, "heartRate": null, "weightKg": null, "spo2": null, "stress": null,
   "distance": null, "activeEnergy": null}]}
```

- Sleep minutes are whole minutes per stage, and `minutes` is the time asleep. A nap and the night
  from the same source add up, and `start`/`end` span both.
- `heartRate` has the day's samples and its resting rate. Either may be null when only the other came in.
- `weightKg` is the day's last weighing.
- `spo2` and `stress` are the lowest, mean and highest of the day's samples, whole numbers, or null
  when none came that day.
- `distance` (metres) and `activeEnergy` (kcal) are the day's totals, whole numbers, from the source
  that counts that day, as steps are; null when none came.

### `GET /api/v1/health/heart-rate?from=&to=`

Every sample, oldest first; by default today, at most 7 days at once (a sample a minute is 10,000 a week).

```json
{"from": "2026-11-15", "to": "2026-11-15", "samples": [{"at": "2026-11-15T00:00:00+11:00", "bpm": 58}]}
```

### `GET /api/v1/health/spo2?from=&to=` and `GET /api/v1/health/stress?from=&to=`

The same as heart rate: every sample from the source that counts each day, oldest first; by default
today, at most 7 days at once.

```json
{"from": "2026-11-15", "to": "2026-11-15", "samples": [{"at": "2026-11-15T04:12:00+11:00", "percent": 96}]}
{"from": "2026-11-15", "to": "2026-11-15", "samples": [{"at": "2026-11-15T15:55:00+11:00", "level": 31}]}
```

### `GET /api/v1/health/steps?from=&to=&every=`

Steps summed in buckets of `every` minutes (1 to 1440, default 60), counted from each day's local
midnight, oldest first; by default today. A day's steps come from the source that counts that day,
as in `/days`.

```json
{"from": "2026-11-15", "to": "2026-11-15", "every": 30,
 "buckets": [[1794697200000, 412], [1794699000000, 1306]]}
```

- Each bucket is `[start in epoch ms, steps]`, kept as numbers because a day by the minute is 1,440
  of them. Buckets with no steps are left out.
- A record longer than a minute (an old export's hour) is shared out over the buckets it covers by
  how much of it falls in each. A day's buckets add up to its `steps` in `/days`.
- At most 10,000 buckets at once (days × buckets a day): a week by the minute, or 56 days by the
  half hour, fits.

### `GET /api/v1/health/sleep?from=&to=`

Each sleep with its stages, oldest first; by default the last 30 days.

```json
{"from": "2026-10-17", "to": "2026-11-15", "sessions": [
  {"day": "2026-11-15", "start": "2026-11-14T23:10:00+11:00", "end": "2026-11-15T06:40:00+11:00", "minutes": 412,
   "source": "gadgetbridge", "stages": [{"start": "2026-11-14T23:10:00+11:00", "end": "2026-11-14T23:55:00+11:00", "stage": "light"}]}]}
```

`stages` is empty when the source didn't say.

### `GET /api/v1/health/weight?from=&to=`

Every weighing, every source's, oldest first; by default the last 365 days, with no limit:
`from=2000-01-01` gets them all.

```json
{"from": "2025-11-16", "to": "2026-11-15", "weighings": [
  {"at": "2026-11-15T07:05:00+11:00", "kg": 72.35, "impedance": 512, "source": "xrnfit-scale"},
  {"at": "2026-06-20T07:30:00+10:00", "kg": 74.1, "source": "import-renpho",
   "body": {"fat": 21.4, "musclePercent": 74.5, "waterPercent": 55.1, "visceral": 9, "bmr": 1712, "metabolicAge": 34}}]}
```

`body` is there when the weighing came with a scale app's own body-composition figures (Renpho's
export has them). It's kept as that app worked it out; xrnfit works out its own from `impedance`.

### Sending health records

Not part of the read-only API, and a token can't do it: `POST /api/health` takes the sync password
(or the page's sign-in cookie), as `/api/events` does.

```json
{"source": "gadgetbridge",
 "upsert": [
   {"id": "r1", "kind": "steps", "start": 1794700000000, "end": 1794700060000, "value": 42},
   {"id": "r2", "kind": "heart_rate", "samples": [[1794700000000, 71], [1794700060000, 74]]},
   {"id": "r3", "kind": "resting_heart_rate", "start": 1794700000000, "value": 56},
   {"id": "r4", "kind": "sleep", "start": 1794650000000, "end": 1794680000000,
    "stages": [[1794650000000, 1794655000000, "light"], [1794655000000, 1794660000000, "deep"]]},
   {"id": "r5", "kind": "weight", "start": 1794700000000, "value": 72.35, "impedance": 512},
   {"id": "r6", "kind": "spo2", "samples": [[1794690000000, 96], [1794693600000, 94]]},
   {"id": "r7", "kind": "stress", "samples": [[1794700000000, 31], [1794700300000, 44]]},
   {"id": "r8", "kind": "distance", "start": 1794700000000, "end": 1794700060000, "value": 38.5},
   {"id": "r10", "kind": "active_energy", "start": 1794700000000, "end": 1794700060000, "value": 2.4}],
 "delete": ["r9"]}
```

- A record is replaced whole by its `(source, id)`, so sending it again changes nothing, and a
  delete takes all of it. Upserts land before deletes, so an id in both ends up gone.
- `source` matches `[a-z][a-z0-9.-]{0,59}`, and `id` matches `[A-Za-z0-9._:-]{1,100}`. Times are
  whole epoch milliseconds from 2000 to a day from now.
- `distance` (metres) and `active_energy` (kcal burnt moving, resting metabolism left out) are
  shaped like `steps`: an amount over `start`..`end`, which add up over a day.
- `spo2` and `stress` are shaped like `heart_rate`: a run of `[epoch ms, value]` samples, kept a row
  each. An SpO2 sample is blood oxygen in percent; a stress sample is the watch's score.
- Plausible values only: at most 100,000 steps, 200,000 m or 10,000 kcal in a record of at most a
  day, 20–250 bpm (resting
  20–200), 2–300 kg, an impedance of 0–5,000 Ω, SpO2 of 50–100 %, stress of 1–100 (the watch's 0
  means it didn't measure, so leave those out), and a sleep of at most a day whose stages lie inside it.
- A weighing may carry `body`, the scale app's own figures, each within its range:
  - `fat` 1–75 (%), `fatKg`, `fatFreeKg`, `muscleKg`, `skeletalKg` 0–300 (kg);
  - `musclePercent`, `skeletalPercent`, `bonePercent`, `proteinPercent`, `waterPercent`,
    `subcutaneousPercent` 0–100 (%);
  - `boneKg` 0–20, `visceral` 0–60 (rating), `bmr` 300–10,000 (kcal), `metabolicAge` 5–120, `bmi` 5–100.

  Any other name refuses the batch.
- At most 1,000 records and 1,000 deletes in a request, 2,000 samples in a heart-rate, SpO2 or stress
  record, 200 stages in a sleep, and 256 KB in all.
- One bad record refuses the batch with a 400 that says why, and nothing lands. The answer is
  `{"upserted": 5, "deleted": 1}`, counting records; a delete of an id that isn't there counts 0.

### xrnfit on the Server page

Not part of the read-only API either: the monitor's `GET /api/server/now`, which only the first
account in the users file can read and a token can't, carries `health`, which the Overview and
Services pages draw as xrnfit's row, since xrnfit has no service to list among `services`.

```json
"health": {"lastIn": 1794904178, "late": false, "week": 4870, "day": "2026-11-17",
           "steps": 7342, "sleep": {"day": "2026-11-17", "minutes": 412, "start": 1794828960000, "end": 1794856980000},
           "resting": {"day": "2026-11-17", "bpm": 61},
           "weight": {"at": 1794859800000, "kg": 78.4, "change": -0.3, "since": 1794341400000},
           "spo2": {"at": 1794857400000, "percent": 97}, "stress": {"at": 1794902400000, "level": 31}}
```

- `lastIn` is when xrnfit last sent anything, in epoch seconds, as `/api/apps` has it: the newest
  health record or event. Null if nothing ever came. `late` is true once that's over six hours ago.
- `week` is how many readings came in over the last seven days, as the Apps page counts them, from
  the inventory's sweep every 10 minutes; null before its first.
- The rest is the Today page's health tiles for the habit day `day`, with the newest SpO2 and stress
  sample from any day. Times inside are epoch milliseconds, and each figure is null until one is in.
- It's kept for a minute, and a batch to `/api/health` drops it.

## Not built yet

- Mail: what xrnmail would copy to the server, and so what this could serve, is still to be decided.
- Writing anything through the API. Ticks still go through xrnloops' own API, as the hub's page
  does, and health comes in through `POST /api/health`, not `/api/v1`.
