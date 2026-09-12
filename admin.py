"""Password-only administration with ordinary HTML forms and atomic JSON saves."""
import fcntl
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import tempfile
import time
from collections import defaultdict, deque
from datetime import date, timedelta
from pathlib import Path

import click
from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, session, url_for)
from PIL import Image, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash

admin = Blueprint('admin', __name__, url_prefix='/admin')
EDITABLE = {'events', 'houses', 'standings', 'trophies'}
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}
MAX_COINS = 9007199254740991
attempts = defaultdict(deque)


def read_auth():
    path = Path(current_app.config['ADMIN_AUTH_FILE'])
    return json.loads(path.read_text()) if path.exists() else {}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.save-')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def data_path(name):
    if name not in EDITABLE:
        abort(404)
    return Path(current_app.config['DATA_DIR']) / f'{name}.json'


def load(name):
    return json.loads(data_path(name).read_text(encoding='utf-8'))


def version(name):
    return hashlib.sha256(data_path(name).read_bytes()).hexdigest()


def save(name, value):
    """Reject stale forms and avoid partial writes, including across workers."""
    lock_path = data_path(name).parent / '.admin-write.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if request.form.get('_version') != version(name):
            raise ValueError('Someone changed this page while you were editing. Reload it and try again.')
        atomic_json(data_path(name), value)


def csrf_token():
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_urlsafe(32)
    return session['_csrf']


@admin.before_request
def protect_admin():
    request.max_content_length = 40 * 1024 * 1024
    request.max_form_parts = 100
    request.max_form_memory_size = 64 * 1024
    auth = read_auth()
    if request.endpoint != 'admin.login':
        if not auth.get('password_hash') or session.get('admin_version') != auth.get('version'):
            return redirect(url_for('admin.login'))
    if request.method == 'POST':
        token = request.form.get('_csrf', '')
        if not token or not hmac.compare_digest(token, session.get('_csrf', '')):
            abort(400, 'Your session expired. Reload the page and try again.')


@admin.after_request
def private_response(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    return response


@admin.context_processor
def admin_helpers():
    return {'csrf_token': csrf_token, 'data_version': version}


@admin.errorhandler(400)
@admin.errorhandler(404)
@admin.errorhandler(413)
def admin_error(error):
    message = 'The files are too large. Keep each submission below 40 MB.' if error.code == 413 else error.description
    return render_template('admin/error.html', title='Unable to continue', message=message), error.code


@admin.route('', methods=['GET', 'POST'])
@admin.route('/', methods=['GET', 'POST'])
def login():
    auth = read_auth()
    if auth.get('password_hash') and session.get('admin_version') == auth.get('version'):
        return redirect(url_for('admin.dashboard'))
    error, status = None, 200
    if request.method == 'POST':
        now = time.monotonic()
        key = request.remote_addr or 'unknown'
        if len(attempts) > 1024:
            for address in list(attempts):
                if not attempts[address] or attempts[address][-1] < now - 300:
                    del attempts[address]
        failures = attempts[key]
        while failures and failures[0] < now - 300:
            failures.popleft()
        if len(failures) >= 5:
            error, status = 'Too many attempts. Please wait five minutes and try again.', 429
        elif auth.get('password_hash') and check_password_hash(auth['password_hash'], request.form.get('password', '')[:1024]):
            attempts.pop(key, None)
            session.clear()
            session['admin_version'] = auth['version']
            session.permanent = True
            return redirect(url_for('admin.dashboard'))
        else:
            failures.append(now)
            error, status = 'The password is incorrect. Please try again.', 401
    return render_template('admin/login.html', title='Admin login', error=error,
                           configured=bool(auth.get('password_hash'))), status


@admin.post('/logout')
def logout():
    session.clear()
    return redirect(url_for('admin.login'))


@admin.get('/dashboard')
def dashboard():
    counts = {'events': len(load('events')), 'houses': sum(len(h['teachers']) for h in load('houses')),
              'standings': len(load('standings')['snapshots']), 'trophies': len(load('trophies'))}
    from digital import db
    counts['accounts'] = db().execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
    counts['awards'] = db().execute('SELECT COUNT(*) FROM awards').fetchone()[0]
    return render_template('admin/dashboard.html', title='Dashboard', counts=counts)


def text(name, label, required=False, limit=200):
    value = request.form.get(name, '').strip()
    if required and not value:
        raise ValueError(f'{label} is required.')
    if len(value) > limit:
        raise ValueError(f'{label} must be {limit} characters or fewer.')
    return value


def integer(name, label, required=True, maximum=MAX_COINS):
    value = text(name, label, required)
    if not value and not required:
        return None
    if not re.fullmatch(r'\d+', value) or int(value) > maximum:
        raise ValueError(f'{label} must be a whole number between 0 and {maximum:,}.')
    return int(value)


def choice(name, label, choices):
    value = text(name, label, True)
    if value not in choices:
        raise ValueError(f'Choose a valid {label.lower()}.')
    return value


def calendar_date(name, label, required=False):
    value = text(name, label, required)
    if value:
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except ValueError:
            raise ValueError(f'Enter a valid {label.lower()}.')
    return value or None


def selected_house():
    return choice('house', 'House', [h['id'] for h in load('houses')])


def row_at(rows, index):
    if index < 0 or index >= len(rows):
        abort(404)
    return rows[index]


def confirmed():
    if request.form.get('confirmed') != 'yes':
        raise ValueError('Please confirm that you want to change the coin counts.')


def failure(error):
    flash(str(error), 'error')


def asset(field, extensions, staged, required=False, current=None):
    """Validate first; uploaded bytes are written only once the whole form is valid."""
    upload = request.files.get(field + '_upload')
    if upload and upload.filename:
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in extensions:
            raise ValueError(f'{field.upper()}: choose {", ".join(sorted(extensions))} files only.')
        content = upload.read()
        if not content:
            raise ValueError(f'The {field.upper()} file is empty.')
        if suffix in IMAGE_EXTENSIONS:
            try:
                with Image.open(io.BytesIO(content)) as picture:
                    picture.verify()
                with Image.open(io.BytesIO(content)) as picture:
                    if picture.format not in {'PNG', 'JPEG', 'WEBP'}:
                        raise ValueError('Choose a PNG, JPG, or WebP image.')
                    output = io.BytesIO()
                    picture.convert('RGB' if picture.format == 'JPEG' else 'RGBA').save(output, format='PNG')
                    content, suffix = output.getvalue(), '.png'
            except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError):
                raise ValueError('That image could not be read. Choose a PNG, JPG, or WebP image.')
        else:
            try:
                source = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                raise ValueError(f'Choose a text-based {field.upper()} file.')
            required_line = r'^v\s+[-+.\d]' if suffix == '.obj' else r'^newmtl\s+\S'
            if not re.search(required_line, source, re.M) or '\x00' in source or re.search(r'<\s*(?:script|html|svg|!doctype)', source, re.I):
                raise ValueError(f'That file does not look like a valid {field.upper()} file.')
        path = f'images/uploads/{secrets.token_hex(12)}/{field}{suffix}'
        staged.append((path, content))
        return path
    if current:
        root = Path(current_app.static_folder).resolve()
        path = (root / current).resolve()
        if path.is_relative_to(root) and path.is_file() and path.suffix.lower() in extensions:
            return current
    if required:
        raise ValueError(f'Upload the {field.upper()} file.')
    return None


def save_with_assets(name, data, staged):
    written = []
    try:
        for relative, content in staged:
            path = Path(current_app.static_folder) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            written.append(path)
        save(name, data)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise


@admin.get('/events')
def events():
    return render_template('admin/events.html', title='Events',
                           events=sorted(load('events'), key=lambda e: e.get('date') or '', reverse=True))


@admin.route('/events/new', methods=['GET', 'POST'])
@admin.route('/events/<event_id>/edit', methods=['GET', 'POST'])
def event_edit(event_id=None):
    events = load('events')
    event = next((e for e in events if e['id'] == event_id), None) if event_id else {}
    if event_id and event is None:
        abort(404)
    if request.method == 'POST':
        try:
            state = choice('status', 'Status', ['upcoming', 'completed'])
            updated = dict(event)
            updated.update(id=event_id or secrets.token_hex(8), name=text('name', 'Event name', True),
                           status=state, date=calendar_date('date', 'Event date', state == 'completed'),
                           time=text('time', 'Time', limit=5) or None,
                           location=text('location', 'Location') or None,
                           description=text('description', 'Description', state == 'upcoming', 3000))
            if updated['time'] and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', updated['time']):
                raise ValueError('Enter a valid time.')
            if state == 'upcoming':
                updated['prizes'] = []
                for place in range(1, 5):
                    coins = integer(f'prize_{place}', f'Prize for place {place}', required=False)
                    if coins is not None:
                        updated['prizes'].append({'place': place, 'coins': coins})
            else:
                updated['results'] = []
                for house in load('houses'):
                    place = choice(f'place_{house["id"]}', f'{house["name"]} place', ['1', '2', '3', '4'])
                    updated['results'].append({'house': house['id'], 'place': int(place),
                        'coins': integer(f'coins_{house["id"]}', f'{house["name"]} coins')})
            if event_id:
                events[events.index(event)] = updated
            else:
                events.append(updated)
            save('events', events)
            flash('Event saved.', 'success')
            return redirect(url_for('admin.events'))
        except ValueError as error:
            failure(error)
    return render_template('admin/event_form.html', title='Edit event' if event_id else 'New event', event=event)


@admin.post('/events/<event_id>/delete')
def event_delete(event_id):
    events = load('events')
    event = next((e for e in events if e['id'] == event_id), None)
    if event is None:
        abort(404)
    try:
        events.remove(event)
        save('events', events)
        flash('Event deleted.', 'success')
    except ValueError as error:
        failure(error)
    return redirect(url_for('admin.events'))


@admin.get('/houses')
def houses():
    return render_template('admin/houses.html', title='Teachers', teams=load('houses'))


@admin.route('/houses/<house_id>/teachers/new', methods=['GET', 'POST'])
@admin.route('/houses/<house_id>/teachers/<int:index>/edit', methods=['GET', 'POST'])
def teacher_edit(house_id, index=None):
    houses = load('houses')
    house = next((h for h in houses if h['id'] == house_id), None)
    if not house:
        abort(404)
    teacher = row_at(house['teachers'], index) if index is not None else {}
    if request.method == 'POST':
        try:
            staged = []
            name = text('name', 'Teacher name', True, 120)
            role = choice('role', 'Role', ['teacher', 'head'])
            image = asset('image', IMAGE_EXTENSIONS, staged,
                          current=None if request.form.get('remove_image') == 'yes' else teacher.get('image'))
            updated = {**teacher, 'name': name, 'image': image, 'is_head': role == 'head'}
            if updated['is_head']:
                for person in house['teachers']:
                    person['is_head'] = False
            if index is None:
                house['teachers'].append(updated)
            else:
                house['teachers'][index] = updated
            save_with_assets('houses', houses, staged)
            flash('Teacher saved.', 'success')
            return redirect(url_for('admin.houses'))
        except ValueError as error:
            failure(error)
    return render_template('admin/teacher_form.html', title='Edit teacher' if index is not None else 'Add teacher',
                           teacher=teacher, team=house)


@admin.post('/houses/<house_id>/teachers/<int:index>/delete')
def teacher_delete(house_id, index):
    houses = load('houses')
    house = next((h for h in houses if h['id'] == house_id), None)
    if house is None:
        abort(404)
    row_at(house['teachers'], index)
    try:
        house['teachers'].pop(index)
        save('houses', houses)
        flash('Teacher deleted.', 'success')
    except ValueError as error:
        failure(error)
    return redirect(url_for('admin.houses'))


def count_history(board):
    """Read old total-only snapshots as additions without changing stored data."""
    previous = {house['id']: 0 for house in load('houses')}
    entries = []
    for index, snapshot in sorted(enumerate(board['snapshots']), key=lambda item: item[1]['date']):
        added = snapshot.get('added')
        if added is None:
            added = {}
            for house_id, before in previous.items():
                total = snapshot['totals'].get(house_id)
                added[house_id] = total - before if type(total) is int and type(before) is int else None
        entries.append((index, {**snapshot, 'added': dict(added)}))
        previous = snapshot['totals']
    return entries


def recalculate_counts(board, entries):
    """An edited, moved, or deleted addition also updates all later totals."""
    totals = {house['id']: 0 for house in load('houses')}
    for entry in sorted(entries, key=lambda item: item['date']):
        for house_id in totals:
            added = entry['added'].get(house_id)
            if type(added) is not int:
                raise ValueError('An earlier count has missing coins. Complete that entry before adding more coins.')
            totals[house_id] += added
            if not 0 <= totals[house_id] <= MAX_COINS:
                raise ValueError('This change would make a house total negative or too large. Check the amounts and date.')
        entry['totals'] = dict(totals)
    board['snapshots'] = sorted(entries, key=lambda item: item['date'])


@admin.get('/standings')
def standings():
    board = load('standings')
    counts = list(reversed(count_history(board)))
    return render_template('admin/standings.html', title='Coin counts', board=board, counts=counts)


@admin.route('/standings/new', methods=['GET', 'POST'])
@admin.route('/standings/<int:index>/edit', methods=['GET', 'POST'])
def count_edit(index=None):
    board = load('standings')
    history = count_history(board)
    if index is not None:
        row_at(board['snapshots'], index)
    count = next((entry for original_index, entry in history if original_index == index), {'added': {}})
    current_totals = history[-1][1]['totals'] if history else {house['id']: 0 for house in load('houses')}
    if request.method == 'POST':
        try:
            confirmed()
            counted = calendar_date('date', 'Entry date', True)
            if counted > date.today().isoformat():
                raise ValueError('The entry date cannot be in the future.')
            if any(s['date'] == counted for i, s in enumerate(board['snapshots']) if i != index):
                raise ValueError('There is already an entry for this date. Edit that entry instead.')
            added = {h['id']: integer(h['id'], f'{h["name"]} coins added') for h in load('houses')}
            updated = {**count, 'date': counted, 'physical_counted_on': counted, 'added': added}
            entries = [entry for original_index, entry in history if original_index != index]
            entries.append(updated)
            recalculate_counts(board, entries)
            save('standings', board)
            flash('Coin entry saved. The house totals have been updated.', 'success')
            return redirect(url_for('admin.standings'))
        except ValueError as error:
            failure(error)
    return render_template('admin/count_form.html', title='Edit coins added' if index is not None else 'Add coins',
                           count=count, board=board, current_totals=current_totals, editing=index is not None,
                           today=date.today().isoformat())


@admin.post('/standings/<int:index>/delete')
def count_delete(index):
    board = load('standings')
    row_at(board['snapshots'], index)
    try:
        confirmed()
        entries = [entry for original_index, entry in count_history(board) if original_index != index]
        recalculate_counts(board, entries)
        save('standings', board)
        flash('Entry deleted. Its added coins have been removed from the house totals.', 'success')
    except ValueError as error:
        failure(error)
    return redirect(url_for('admin.standings'))


@admin.get('/trophies')
def trophies():
    return render_template('admin/trophies.html', title='Trophies', trophies=load('trophies'))


@admin.route('/trophies/new', methods=['GET', 'POST'])
@admin.route('/trophies/<int:index>/edit', methods=['GET', 'POST'])
def trophy_edit(index=None):
    trophies = load('trophies')
    trophy = row_at(trophies, index) if index is not None else {}
    years = [f'{year}/{year + 1}' for year in range(2020, date.today().year + 6)]
    if trophy.get('year') and trophy['year'] not in years:
        years.append(trophy['year'])
    if request.method == 'POST':
        try:
            staged = []
            house = selected_house()
            year = choice('year', 'School year', years)
            title = text('title', 'Trophy title', True, 120)
            kind = choice('kind', 'Display type', ['image', '3d'])
            updated = {'house': house, 'year': year, 'title': title, 'image': None}
            if kind == 'image':
                updated['image'] = asset('image', IMAGE_EXTENSIONS, staged, required=True, current=trophy.get('image'))
            else:
                updated['obj'] = asset('obj', {'.obj'}, staged, required=True, current=trophy.get('obj'))
                updated['mtl'] = asset('mtl', {'.mtl'}, staged, required=True, current=trophy.get('mtl'))
                updated['up_axis'] = trophy.get('up_axis', 'z')
            if index is None:
                trophies.append(updated)
            else:
                trophies[index] = updated
            save_with_assets('trophies', trophies, staged)
            flash('Trophy saved.', 'success')
            return redirect(url_for('admin.trophies'))
        except ValueError as error:
            failure(error)
    return render_template('admin/trophy_form.html', title='Edit trophy' if index is not None else 'New trophy',
                           trophy=trophy, years=sorted(years, reverse=True))


@admin.post('/trophies/<int:index>/delete')
def trophy_delete(index):
    trophies = load('trophies')
    row_at(trophies, index)
    try:
        trophies.pop(index)
        save('trophies', trophies)
        flash('Trophy deleted.', 'success')
    except ValueError as error:
        failure(error)
    return redirect(url_for('admin.trophies'))


def init_admin(app):
    app.config.setdefault('ADMIN_AUTH_FILE', Path(app.instance_path) / 'admin-auth.json')
    with app.app_context():
        auth = read_auth()
    app.secret_key = os.environ.get('SECRET_KEY') or auth.get('secret_key') or secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_NAME='wis_admin', SESSION_COOKIE_PATH='/admin',
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=os.environ.get('ADMIN_HTTPS') == '1',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    app.register_blueprint(admin)

    @app.after_request
    def uploaded_asset_headers(response):
        if request.path.startswith('/static/images/uploads/'):
            response.headers['X-Content-Type-Options'] = 'nosniff'
            if request.path.endswith(('.obj', '.mtl')):
                response.headers['Content-Type'] = 'text/plain; charset=utf-8'
        return response

    @app.cli.command('admin-password')
    @click.option('--generate', is_flag=True, help='Save a generated initial password in a private local file.')
    def admin_password(generate):
        """Set or reset the admin password; this signs out existing sessions."""
        password = secrets.token_urlsafe(20) if generate else click.prompt('New admin password', hide_input=True, confirmation_prompt=True)
        if len(password) < 12:
            raise click.ClickException('Use at least 12 characters.')
        with app.app_context():
            existing = read_auth()
        auth = {'password_hash': generate_password_hash(password), 'version': secrets.token_hex(16),
                'secret_key': existing.get('secret_key') or secrets.token_hex(32)}
        atomic_json(app.config['ADMIN_AUTH_FILE'], auth)
        initial = Path(app.config['ADMIN_AUTH_FILE']).parent / 'admin-initial-password.txt'
        if generate:
            descriptor = os.open(initial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, 'w') as output:
                output.write(f'Admin login: /admin\nPassword: {password}\n\nTo change it: .venv/bin/flask --app app admin-password\n')
            click.echo(f'Initial password saved to {initial}. Restart Flask before signing in.')
        else:
            initial.unlink(missing_ok=True)
            click.echo('Admin password updated. Restart Flask before signing in.')
