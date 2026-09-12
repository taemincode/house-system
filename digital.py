"""Transactional digital coins. JSON remains the source of manual coin totals."""
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager, closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import click
from flask import current_app, g

WARSAW = ZoneInfo('Europe/Warsaw')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS accounts (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
 password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 must_change INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL DEFAULT 1,
 profile TEXT, created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS teacher_sessions (
 token_hash TEXT PRIMARY KEY, teacher_id TEXT NOT NULL REFERENCES accounts(id),
 version INTEGER NOT NULL, csrf TEXT NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS session_teacher ON teacher_sessions(teacher_id);
CREATE TABLE IF NOT EXISTS awards (
 id TEXT PRIMARY KEY, teacher_id TEXT NOT NULL REFERENCES accounts(id),
 teacher_name TEXT NOT NULL, student TEXT NOT NULL, house TEXT NOT NULL,
 amount INTEGER NOT NULL CHECK(amount > 0), reason TEXT NOT NULL,
 created_at INTEGER NOT NULL, school_year TEXT NOT NULL,
 submission_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
 reversed_at INTEGER, reversed_by TEXT, reversal_reason TEXT,
 UNIQUE(teacher_id, submission_key)
);
CREATE INDEX IF NOT EXISTS award_feed ON awards(school_year, created_at DESC, id DESC) WHERE reversed_at IS NULL;
CREATE INDEX IF NOT EXISTS award_house ON awards(school_year, house, created_at DESC, id DESC) WHERE reversed_at IS NULL;
CREATE INDEX IF NOT EXISTS award_teacher ON awards(teacher_id, created_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO settings VALUES ('max_coins', '10');
CREATE TABLE IF NOT EXISTS baselines (school_year TEXT PRIMARY KEY, totals TEXT NOT NULL, captured_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts (bucket TEXT NOT NULL, attempted_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS attempts_bucket ON login_attempts(bucket, attempted_at);
PRAGMA user_version = 1;
'''


def now():
    return int(current_app.config.get('COINS_CLOCK', time.time)())


def school_year(at=None):
    local = datetime.fromtimestamp(now() if at is None else at, WARSAW)
    start = local.year if local.month >= 9 else local.year - 1
    return f'{start}/{start + 1}'


def bounds(period, at=None):
    local = datetime.fromtimestamp(now() if at is None else at, WARSAW)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == 'week':
        start -= timedelta(days=start.weekday())
    end = start + timedelta(days=7 if period == 'week' else 1)
    return int(start.timestamp()), int(end.timestamp())


def db():
    if 'coins_db' not in g:
        path = Path(current_app.config['COINS_DB'])
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Pre-create with private permissions before SQLite writes any data.
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        os.chmod(path, 0o600)
        conn = sqlite3.connect(path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=15000')
        conn.execute('PRAGMA journal_mode=WAL')
        if conn.execute('PRAGMA user_version').fetchone()[0] == 0:
            conn.executescript(SCHEMA)
        g.coins_db = conn
    return g.coins_db


@contextmanager
def transaction():
    conn = db()
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def manual_data():
    return json.loads((Path(current_app.config['DATA_DIR']) / 'standings.json').read_text())


def max_coins():
    return int(db().execute("SELECT value FROM settings WHERE key='max_coins'").fetchone()[0])


def active_sums(year):
    return {r['house']: r['total'] for r in db().execute(
        'SELECT house, SUM(amount) AS total FROM awards WHERE school_year=? AND reversed_at IS NULL GROUP BY house', (year,))}


def baseline(board, ids):
    """Persist the old comparison on first use. Only an explicit admin reset moves it."""
    year = board['school_year']
    row = db().execute('SELECT * FROM baselines WHERE school_year=?', (year,)).fetchone()
    if row is None:
        snapshots = sorted(board['snapshots'], key=lambda s: s['date'])
        previous = snapshots[-2] if len(snapshots) > 1 else (snapshots[-1] if snapshots else None)
        totals = {key: previous['totals'].get(key) if previous else None for key in ids}
        captured = int(datetime.fromisoformat(previous['date']).replace(tzinfo=WARSAW).timestamp()) if previous else now()
        db().execute('INSERT OR IGNORE INTO baselines VALUES (?,?,?)', (year, json.dumps(totals), captured))
        row = db().execute('SELECT * FROM baselines WHERE school_year=?', (year,)).fetchone()
    return {'totals': json.loads(row['totals']), 'date': datetime.fromtimestamp(row['captured_at'], WARSAW).date().isoformat(),
            'captured_at': row['captured_at']}


def combined_board(houses, board, build):
    build(houses, board)  # Validate original manual values before adding digital integers.
    ids = [h['id'] for h in houses]
    comparison = baseline(board, ids)
    snapshots = sorted(board['snapshots'], key=lambda s: s['date'])
    manual = snapshots[-1] if snapshots else {'date': None, 'totals': {}}
    sums = active_sums(board['school_year'])
    totals = {key: manual['totals'].get(key) + sums.get(key, 0)
              if manual['totals'].get(key) is not None else None for key in ids}
    # build_standings owns ranking and unknown/tied behavior. A synthetic comparison
    # avoids altering either the manual JSON history or individual digital records.
    result = build(houses, {'school_year': board['school_year'], 'snapshots': [
        {'date': '0001-01-01', 'totals': comparison['totals']},
        {'date': '9999-12-31', 'totals': totals}]})
    result.update(date=manual['date'], previous_date=comparison['date'], baseline_at=comparison['captured_at'],
                  digital_totals=sums, donation_total=sum(totals.values()) if all(v is not None for v in totals.values()) else None)
    return result


def public_award(row):
    # Explicit allowlist: no IDs for accounts, session details or reversal metadata.
    names = {'rivers': 'Rivers', 'castles': 'Castles', 'forests': 'Forests', 'bridges': 'Bridges'}
    noun = 'coin' if row['amount'] == 1 else 'coins'
    message = f"{row['student']} ({names[row['house']]}) received {row['amount']} {noun} from {row['teacher_name']}"
    if row['reason']:
        message += f" for {row['reason']}"
    parts = [
        {'text': row['student'], 'bold': True},
        {'text': f" ({names[row['house']]}) received ", 'bold': False},
        {'text': f"{row['amount']} {noun}", 'bold': True},
        {'text': ' from ', 'bold': False},
        {'text': row['teacher_name'], 'bold': True},
    ]
    if row['reason']:
        parts.extend([{'text': ' for ', 'bold': False}, {'text': row['reason'], 'bold': True}])
    parts[-1]['text'] = parts[-1]['text'].rstrip('.')
    parts.append({'text': '.', 'bold': False})
    return {'id': row['id'], 'house': row['house'], 'amount': row['amount'],
            'message': message.rstrip('.') + '.', 'message_parts': parts, 'timestamp': row['created_at'],
            'time': datetime.fromtimestamp(row['created_at'], WARSAW).strftime('%H:%M'),
            'datetime': datetime.fromtimestamp(row['created_at'], timezone.utc).isoformat()}


def feed(period='day', house=None, limit=3, cursor=None, random_start=False):
    start, end = bounds(period)
    year = manual_data()['school_year']
    where = 'school_year=? AND reversed_at IS NULL AND created_at>=? AND created_at<?'
    args = [year, start, end]
    if house:
        where += ' AND house=?'
        args.append(house)
    total = db().execute(f'SELECT COALESCE(SUM(amount),0) FROM awards WHERE {where}', args).fetchone()[0]
    revision = db().execute(f'SELECT id, reversed_at FROM awards WHERE school_year=? AND created_at>=? AND created_at<? ORDER BY id',
                            (year, start, end)).fetchall()
    revision_rows = revision
    revision = hashlib.sha256(json.dumps([tuple(r) for r in revision]).encode()).hexdigest()[:20]
    if cursor:
        try:
            timestamp, award_id = cursor.split(':', 1)
            timestamp = int(timestamp)
            if len(award_id) > 40:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError('Invalid page cursor.')
        where += ' AND (created_at, id) < (?, ?)'
        args += [timestamp, award_id]
    rows = db().execute(f'SELECT * FROM awards WHERE {where} ORDER BY created_at DESC, id DESC LIMIT ?', [*args, limit + 1]).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = f"{rows[-1]['created_at']}:{rows[-1]['id']}" if more else None
    if random_start and rows:
        # Choose from the entire current day, not just the initial page of awards.
        first = db().execute(f'SELECT * FROM awards WHERE {where} ORDER BY RANDOM() LIMIT 1', args).fetchone()
        if first:
            rows = [first, *[row for row in rows if row['id'] != first['id']]]
    return {'items': [public_award(r) for r in rows], 'total': total, 'period': f'{year}:{start}', 'revision': revision,
            'next': next_cursor, 'server_time': now(),
            'active_ids': [r[0] for r in revision_rows if r[1] is None]}


def init_digital(app):
    app.config.setdefault('COINS_DB', Path(app.instance_path) / 'coins.sqlite3')

    @app.teardown_appcontext
    def close_db(error):
        conn = g.pop('coins_db', None)
        if conn is not None:
            conn.close()

    @app.cli.command('init-coins')
    def init_coins():
        db()
        click.echo('Private teacher and digital coin storage is ready. No demo awards were added.')

    @app.cli.command('backup-coins')
    @click.argument('destination', type=click.Path(path_type=Path))
    def backup_coins(destination):
        if destination.exists():
            raise click.ClickException('Choose a new backup filename.')
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(descriptor)
        with closing(sqlite3.connect(destination)) as backup:
            db().backup(backup)
        click.echo(f'Database backed up to {destination}. Also back up data/, instance/ and uploaded images.')
