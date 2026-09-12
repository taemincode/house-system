# WIS Houses

A complete Flask website matching the supplied house system mockups: bold type, a large standings podium, colourful event cards, individual house pages and an About page. HTML is rendered with Jinja; CSS and vanilla JavaScript are local. No build tools, external fonts or JavaScript packages are required.

## Run locally

Requires Python 3.10 or later. Open a terminal inside this folder:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:5001**. Stop with Ctrl+C. On Windows, use `python` instead of `python3` and activate with `.venv\Scripts\activate` (Command Prompt) or `.venv\Scripts\Activate.ps1` (PowerShell).

If port 5001 is occupied, run `python -m flask --app app run --port 5050` and open http://127.0.0.1:5050.

The included server is for local development. For public hosting, use a Python-compatible host with a production WSGI server and the application target `app:app`. Hosting is not configured or purchased by this project.

## Files

```text
wis-houses/
├── app.py
├── requirements.txt
├── README.md
├── .gitignore
├── data/
│   ├── site.json
│   ├── houses.json
│   ├── standings.json
│   ├── events.json
│   └── trophies.json
├── templates/
│   ├── base.html
│   ├── standings.html
│   ├── house.html
│   ├── events.html
│   ├── about.html
│   ├── 404.html
│   └── partials/
│       ├── navigation.html
│       └── icons.html
├── static/
│   ├── css/
│   │   ├── base.css
│   │   ├── standings.css
│   │   ├── house.css
│   │   ├── events.css
│   │   └── about.css
│   ├── js/
│   │   ├── main.js
│   │   └── events.js
│   └── images/
│       ├── houses/
│       │   ├── rivers.png
│       │   ├── castles.png
│       │   ├── forests.png
│       │   └── bridges.png
│       ├── teachers/
│       └── trophies/
└── tests/
    └── test_app.py
```

The text brand **WIS HOUSES** matches the mockups. Your own `static/images/wis-logo.png` can stay in that directory, but is not required or displayed by this design. All four provided house logos are included. Their original artwork is preserved, including the singular wording inside the Rivers and Forests crests.

## Pages

- `/`: current standings with a three-house podium and a fourth-place bar. A tie uses equally styled rows so the layout does not imply an outright winner.
- `/houses/rivers`, `/houses/castles`, `/houses/forests`, `/houses/bridges`: coloured hero, recent event results, this week’s digital coins, teachers, then trophies when present.
- `/events`: upcoming events. Click a card or row to expand its details and prize breakdown.
- `/events?view=results`: past event placements and coins. Tabs support browser Back and work without JavaScript.
- `/about`: introduction, how coins work, donation, the two championships and the full Grandmaster message beside an image placeholder.

The Houses dropdown works by click, touch or keyboard. Escape closes it. Layouts adapt to phones and respect reduced motion preferences. Teachers have individual accounts backed by private SQLite storage. Teachers open `/teacher/login` directly; no login link appears in the public navigation. A separate password-only admin area is available at `/admin`, without a link in the public navigation.

## Admin dashboard

Open **http://127.0.0.1:5001/admin** (or `/admin` on whichever port your server uses). The login has only a password. `site.json` has no admin editor.

For the first login on this workspace, the generated password is in `instance/admin-initial-password.txt`. This private directory is excluded from version control and is not served by Flask. Restart your Flask server after installing the updated dependencies:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

To set or reset the password, run this command and enter a password of at least 12 characters when prompted:

```sh
.venv/bin/flask --app app admin-password
```

On a new checkout, use the same command to set up access. The password is stored as a hash in `instance/admin-auth.json`; changing it signs out existing admin sessions and removes the initial plaintext password file. Keep this directory private and persistent. For an HTTPS-hosted installation, set `ADMIN_HTTPS=1` so the session cookie is sent only over HTTPS.

The dashboard keeps the existing content editors and adds Accounts, Award history and Coin settings:

- **Events:** create, edit, and delete events. Choose Upcoming or Completed. Upcoming events need a description; completed events need a date, place, and coins for all four houses. Optional prizes can be left blank. Event awards are not automatically added to standings.
- **Teachers:** add, edit, or delete teachers within each house. Names are required, photos are optional, and the role selector chooses Teacher or Head of house. Choosing a new head replaces the previous head designation.
- **Coin counts:** enter the number of coins added to each house on a date (0 if none). The admin calculates cumulative totals automatically. Editing an entry replaces its original addition; deleting it removes that addition. Backdated entries and edits recalculate all later totals. Existing total-only history is preserved and its additions are inferred from consecutive totals, with the first count treated as the opening balance. Each save/delete requires a confirmation checkbox and, with JavaScript enabled, a confirmation prompt. Duplicate dates and negative/fractional added amounts are rejected. The school year is displayed from the existing standings data.
- **Trophies:** house, school year, title, and a display asset are required. Choose Image (PNG/JPG/WebP) or 3D model (both OBJ and MTL). The matching upload fields appear for the selected type. When editing, existing files are kept unless a replacement is uploaded. New GLB files are rejected. Existing legacy GLB awards remain visible publicly until explicitly replaced or deleted.

Uploaded images are validated and re-encoded; uploads are stored under `static/images/uploads/` with unique paths. A submission can contain up to 40 MB. Tinkercad color-based OBJ/MTL exports work directly. The admin uploader accepts color-based OBJ/MTL models; textured models with separate texture images need to be installed together manually. Removing an award or teacher does not delete its source files, which may be shared by other records.

Changes are validated on the server and JSON files are replaced atomically. Stale forms are rejected rather than overwriting a newer save. All admin forms use CSRF protection; login attempts are limited per IP in the running process. Admin pages are not indexed or cached. No admin feature edits `site.json`.

## Teacher accounts and digital coins

### Initialize and deploy

After updating this checkout, stop/restart the Flask or WSGI process and initialize the new storage once:

```sh
.venv/bin/flask --app app init-coins
```

This creates `instance/coins.sqlite3` without adding any accounts or awards. It also initializes automatically on first use, so existing installations need no JSON migration. Keep `instance/`, `data/` and uploaded images on a **persistent local disk**, writable by the application. SQLite uses WAL, foreign keys, unique submission keys and transactions; multiple workers on the same host share the database. Do not put it on an ephemeral filesystem, distribute replicas with separate disks, or serve `instance/` as static files. For a future multi-host deployment, use a shared transactional database before scaling across hosts.

Set `ADMIN_HTTPS=1` in production over HTTPS; it now protects both admin and teacher cookies. Keep the existing admin credential file and secret key stable across restarts/workers. No public registration or student accounts exist. The new code uses the Python standard library, Flask’s existing dependencies, and the OS Europe/Warsaw time-zone database. If the host does not provide IANA time-zone data, install it before starting the app.

### Create accounts and give coins

1. Sign in at `/admin`, open **Accounts → New account**, and enter a unique username, public teacher display name and a temporary password of 12–128 characters. Optionally select an existing teacher profile. That link is for reference; it does not restrict which houses the teacher may award. It matches the profile’s house and name; if that profile is later renamed or removed, relink it in the account editor.
2. Share the credentials privately. The teacher opens **`/teacher/login`** directly, then must replace the temporary password before giving awards. Passwords are stored only as hashes. Teacher sessions last eight hours. Saving an account, deactivating it, or resetting its password ends existing sessions. Reactivation does not restore an old session.
3. In **Give coins**, enter the student’s name, select a house, choose the number of coins and optionally enter a short reason. Default: 1 coin; limit: 10. Admin → **Coin settings** can set a maximum of 1–100 per submission.
4. Submit once. A successful save returns a confirmation and a fresh form. Retrying the same form is safe: its unique key can create at most one award, including across workers. The server determines the teacher from the session. Never also give a physical coin for the same reward. Names and reasons are public, as the form explains.

### Feeds, totals and corrections

**Today’s coins** is a non-collapsible panel below public navigation. Each page load starts with a randomly selected award from the whole day, then cycles through the day’s awards every five seconds. The compact card uses a light tint of the awarded house’s colour, a large coin amount, the award message and minutes elapsed. Student names, coin amounts, teachers and reasons are bold in the panel message. It has no heading, daily total or navigation controls. Rotation switches content without motion effects and pauses while hovered or keyboard-focused. One award remains visible continuously; no awards shows a compact empty state. House pages show **This week’s coins** between Recent results and Teachers, with six initial awards and Show more. Both feeds refresh every 25 seconds while the page is visible. The daily rotation keeps the current award when new entries arrive. In the weekly list, new entries do not push a reader away from expanded/older entries; an Update list control appears instead. Reversed entries disappear. Connection failures keep the last successfully loaded view and retry quietly.

UTC timestamps are stored as Unix seconds. Public day/week boundaries use Europe/Warsaw, including daylight-saving changes; weeks begin Monday at 00:00. Only awards from the selected competition year appear in its public feeds. Award IDs and pagination remain stable for submissions in the same second.

The accounting rule is always:

```text
House total = latest manual physical/event total + active digital awards in that school year
Amount raised = sum of known house totals, at 1 zł per coin
```

Do not enter digital awards in **Coin counts**. Event administration still does not automatically award coins: add event amounts manually as before. The About page’s amount raised is the current competition’s collected amount, not a record of a past payment to the orphanage. Digital awards never edit historical donation or trophy records.

Teachers can use **My awards → Undo award** with confirmation during the first 10 minutes (strictly before 600 seconds). They cannot undo another teacher’s award. After that, use **Admin → Award history**, filter by teacher, house or Warsaw date, select **Review**, and reverse the award with a reason and confirmation. The original record and its teacher/student/amount stay intact; the private record adds who reversed it, when and why. Reversed awards stop contributing to house/donation totals and public activity. The comparison baseline stays fixed, so a correction can produce a negative change.

### Backup and restore

For a live database backup, use SQLite’s backup API through the provided command. Choose a new filename outside publicly served folders:

```sh
.venv/bin/flask --app app backup-coins /private/path/wis-coins-backup.sqlite3
```

The backup includes accounts, password hashes, awards, reversals, settings and comparison baselines. Also back up `data/`, `instance/admin-auth.json`, `instance/manual-history/`, and `static/images/uploads/`. For a single consistent backup of everything, briefly stop the app while copying those files and taking the database backup. Do not copy only a running SQLite main file and omit its WAL.

To restore, stop the app, preserve the current installation as a backup, restore the matching JSON/assets/private files, and replace `instance/coins.sqlite3` with the saved database. Remove stale `coins.sqlite3-wal` and `coins.sqlite3-shm` sidecars only while the app is stopped and after retaining a backup. Keep private file permissions (database/auth 0600; instance directory preferably 0700), then restart. `instance/` is excluded from version control. Keep backups private because they contain student names and account data.

### Verification

`tests/test_digital.py` uses a temporary database and names explicitly marked DEMO. It covers account creation, first-login password change, resets/deactivation, session expiry, throttling/CSRF, role separation, field validation, retry and concurrent submission handling, daily/weekly pagination, public escaping, manual-plus-digital accounting, donation totals, baselines, ties/rank movement, unknown totals, teacher/admin reversals, time boundaries, school-year rollover and backup. Existing event/physical-count tests use a fixed standings fixture, so editing actual website data does not affect calculation expectations. Browser checks cover desktop/phone layouts, keyboard navigation, form reset and automatic updates.

## Edit content

Edit the JSON files in `data/`, save, then refresh the page. Flask rereads them on each request. Keep valid JSON: double quotes, no comments, no trailing commas. `null` means unknown, while `0` means a known zero.

**The bundled scores, dates, events and trophy awards are illustrative sample data from the visualizations.** They are not verified school records. `site.json` starts with `"demo_mode": true`, which displays one unobtrusive disclosure below the page. Replace the sample records with confirmed information before setting this to `false`. Changing the flag only removes the disclosure; it does not replace the sample data.

### Standings

`standings.json` contains a `school_year` and dated `snapshots`. Append one snapshot each time a new official count is published. Each `totals` object uses all four house IDs and contains **the manual cumulative total, already including separately recorded event coins, but excluding digital awards**. The website never adds the Events page values to it.

`date` is the publication date in `YYYY-MM-DD` format. `physical_counted_on` records when the boxes were last counted, if known. It is retained for recordkeeping. Each snapshot needs a unique publication date; for a correction to the same update, edit that snapshot. If no box count occurred for an event-only update, retain the previous physical count date.

Displayed totals add active digital awards for the same school year to the latest manual totals. The persisted comparison baseline lives in SQLite. On first use, it imports the previous manual snapshot (or the single available snapshot); the timestamp is midnight Warsaw time on that snapshot’s date. It remains fixed through manual edits, digital awards and reversals. Admin → Coin settings → Update comparison baseline explicitly captures the current combined totals and the server timestamp. Both coin changes and rank movement compare against this same baseline. The public page labels the comparison with its date and time.

Ties use competition ranks (1, 1, 3, 4). Negative changes can appear after corrections. Unknown manual totals remain unknown even when digital coins exist, and rankings and the combined donation total are withheld until every manual total is known. Digital weekly totals can still be displayed independently. Admin-managed snapshots store `added` values; use the admin to correct entries so additions and cumulative totals stay consistent.

Use Admin → Coin settings to start a new school year from 1 September. This archives the previous manual JSON under `instance/manual-history/` and starts the new manual totals at zero. Older digital awards remain in private history and never carry into the new competition. The current year follows 1 September–31 August in Europe/Warsaw. If the competition has not been advanced, new digital submissions pause with an explanation until the administrator starts the current year.

### Houses and teachers

In `houses.json`, keep the house IDs unchanged. Replace each teacher's `name` and optional `image`; mark the head with `"is_head": true`. Image paths are relative to `static/`, for example `"images/teachers/rivers-head.png"`. Each house starts with six unnamed teachers, including one head.

Missing or null images render a deliberate placeholder. Add photographs or approved illustrations later without changing the templates. Names and roles are separate from the images.

### Events

`events.json` is the single source for both Events and each house's recent results. Give each event a unique URL-friendly `id`. Upcoming events have `"status": "upcoming"`, an ISO `date` (or `null` for an unannounced date), `time`, `location`, `description` and a `prizes` list of `{ "place": 1, "coins": 20 }` objects. An empty prize list displays “Prizes to come”.

Completed events have `"status": "completed"`, a confirmed ISO date and a `results` list containing each house's `house` ID, `place` and `coins`. Example:

```json
{"house": "rivers", "place": 1, "coins": 20}
```

Set all four results, including zero awards. Placements may tie. When an event finishes, update that same event's status and add its results. Publication is explicit: simply reaching the scheduled date does not invent a result. Remove or reschedule postponed upcoming events manually. The six newest completed results for a house appear on its house page.

### Trophies

`trophies.json` lists historical awards independently of current standings. Each record contains `house`, `year`, `title` (`House System` or `Sports Day`) and optional `image`. The caption is generated as “Winners of the 2025/2026 House System”. The two Rivers trophies are demonstration records only; remove or replace them with confirmed winners.

If a house has no records, its Trophies section is omitted. Trophies always appear **below Teachers**. Trophies support interactive OBJ + MTL files, or GLB models. For a Tinkercad OBJ export, keep the `.obj`, `.mtl`, and any texture images together. Add `obj` and `mtl` paths relative to `static/` to the award; `up_axis` defaults to `z` for Tinkercad (use `y` for Y-up exports). Rivers’ House System award now uses:

```json
{
  "house": "rivers",
  "year": "2025/2026",
  "title": "House System",
  "obj": "images/house-trophy/tinker.obj",
  "mtl": "images/house-trophy/obj.mtl",
  "up_axis": "z",
  "image": null
}
```

OBJ materials load from the MTL, including texture paths relative to its folder. Visitors can rotate and zoom with mouse, touch, or keyboard. Models load lazily, with no automatic rotation or download link. Existing `model` paths still support GLB; OBJ + MTL takes priority when both files exist. Otherwise the site uses GLB, the optional image, or a placeholder. The local libraries are Three.js 0.180.0 (MIT, `static/js/vendor/three/LICENSE.txt`) and `@google/model-viewer` 4.1.0 (Apache-2.0, `static/js/model-viewer.LICENSE.txt`).

### About and Grandmaster

`site.json` contains the founding year and the Grandmaster's complete message. Set `grandmaster.image` to a relative image path when available; until then it shows “Image to come”. Content is escaped by Jinja rather than treated as HTML.

## Adjust the appearance

`base.css` controls the shared navigation, typography and the four house colours. Each page has its own CSS file. Keep image paths relative and use Flask's `url_for` when changing templates so the project remains portable.

## Checks

```sh
python -m unittest discover -s tests -v
```

Tests cover routes, logo assets, calculations, ties, incomplete counts, first updates and trophy placement. During development, the pages were also checked in Chrome at 1440, 1033, 390 and 320 pixels, including event tabs, browser Back, dropdown navigation, Escape, expanded details and the no-JavaScript fallback.
