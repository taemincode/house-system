"""Teacher-only authentication and a fast, ordinary form for digital awards."""
import fcntl
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from pathlib import Path

from flask import (Blueprint, abort, current_app, g, jsonify, redirect, render_template,
                   request, url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from digital import db, feed, manual_data, max_coins, now, public_award, school_year, transaction

teachers = Blueprint('teachers', __name__, url_prefix='/teacher')
public_coins = Blueprint('public_coins', __name__, url_prefix='/api/coins')
COOKIE = 'wis_teacher'
LOGIN_CSRF = 'wis_teacher_login'
HOUSES = ('rivers', 'castles', 'forests', 'bridges')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt='teacher-login-csrf')


def cookie_options():
    return dict(httponly=True, secure=current_app.config['SESSION_COOKIE_SECURE'], samesite='Lax', path='/')


def teacher_csrf():
    return g.teacher_session['csrf'] if g.get('teacher_session') else g.get('login_csrf', '')


def authenticated_teacher():
    g.teacher = g.teacher_session = None
    token = request.cookies.get(COOKIE, '')
    if token and len(token) <= 128:
        row = db().execute('''SELECT s.*, a.username, a.display_name, a.must_change FROM teacher_sessions s
            JOIN accounts a ON a.id=s.teacher_id WHERE s.token_hash=? AND s.expires_at>?
            AND a.active=1 AND a.version=s.version''', (digest(token), now())).fetchone()
        if row:
            g.teacher_session = row
            g.teacher = {'id': row['teacher_id'], 'display_name': row['display_name'], 'username': row['username'], 'must_change': row['must_change']}


@teachers.before_request
def protect():
    request.max_content_length = 32 * 1024
    if request.endpoint != 'teachers.login':
        if not g.teacher:
            return redirect(url_for('teachers.login'))
        if g.teacher['must_change'] and request.endpoint not in ('teachers.password', 'teachers.logout'):
            return redirect(url_for('teachers.password'))
    else:
        token = request.cookies.get(LOGIN_CSRF, '')
        try:
            serializer().loads(token, max_age=3600)
        except (BadSignature, SignatureExpired):
            token = serializer().dumps(secrets.token_urlsafe(24))
        g.login_csrf = token
    if request.method == 'POST':
        expected = g.login_csrf if request.endpoint == 'teachers.login' else teacher_csrf()
        submitted = request.form.get('_csrf', '')
        if not submitted or not hmac.compare_digest(expected, submitted):
            abort(400, 'Your session expired. Reload the page and try again.')


@teachers.after_request
def private_response(response):
    response.headers.update({'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex, nofollow',
                             'X-Frame-Options': 'DENY', 'X-Content-Type-Options': 'nosniff'})
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    if g.get('login_csrf'):
        response.set_cookie(LOGIN_CSRF, g.login_csrf, max_age=3600, **cookie_options())
    return response


@teachers.errorhandler(400)
@teachers.errorhandler(403)
@teachers.errorhandler(404)
@teachers.errorhandler(413)
def error_page(error):
    return render_template('teacher/error.html', page_title='Unable to continue', active='', message=error.description), error.code


def clean(name, label, required=True, length=120):
    value = request.form.get(name, '').strip()
    if (required and not value) or len(value) > length or any(ord(c) < 32 for c in value):
        raise ValueError(f'Enter {label.lower()} using {length} characters or fewer.')
    return value


def password_value():
    value = request.form.get('password', '')
    if not 8 <= len(value) <= 128:
        raise ValueError('Use a password with 8–128 characters.')
    return value


def new_session(conn, account):
    token = secrets.token_urlsafe(32)
    conn.execute('DELETE FROM teacher_sessions WHERE expires_at<=?', (now(),))
    conn.execute('INSERT INTO teacher_sessions VALUES (?,?,?,?,?)',
                 (digest(token), account['id'], account['version'], secrets.token_urlsafe(32), now() + 8 * 3600))
    return token


@teachers.route('/login', methods=['GET', 'POST'])
def login():
    if g.teacher:
        return redirect(url_for('teachers.password' if g.teacher['must_change'] else 'teachers.give'))
    error, status = None, 200
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()[:50]
        password = request.form.get('password', '')[:129]
        buckets = [digest('ip:' + (request.remote_addr or 'unknown')), digest('user:' + username)]
        token = None
        with transaction() as conn:
            conn.execute('DELETE FROM login_attempts WHERE attempted_at<?', (now() - 300,))
            blocked = any(conn.execute('SELECT COUNT(*) FROM login_attempts WHERE bucket=?', (b,)).fetchone()[0] >= 5 for b in buckets)
            account = conn.execute('SELECT * FROM accounts WHERE username=?', (username,)).fetchone()
            if blocked:
                error, status = 'Too many attempts. Try again in five minutes.', 429
            elif account and account['active'] and check_password_hash(account['password_hash'], password):
                conn.executemany('DELETE FROM login_attempts WHERE bucket=?', [(b,) for b in buckets])
                token = new_session(conn, account)
            else:
                # A fixed dummy hash keeps unknown usernames from being a fast path.
                if not account or not account['active']:
                    check_password_hash(current_app.config['TEACHER_DUMMY_HASH'], password)
                conn.executemany('INSERT INTO login_attempts VALUES (?,?)', [(b, now()) for b in buckets])
                error, status = 'Username or password is incorrect.', 401
        if token:
            response = redirect(url_for('teachers.password' if account['must_change'] else 'teachers.give'))
            response.set_cookie(COOKIE, token, max_age=8 * 3600, **cookie_options())
            return response
    return render_template('teacher/login.html', page_title='Teacher login', active='', error=error), status


@teachers.route('/password', methods=['GET', 'POST'])
def password():
    error = None
    if request.method == 'POST':
        try:
            value = password_value()
            if value != request.form.get('confirm_password'):
                raise ValueError('The two passwords do not match.')
            with transaction() as conn:
                account = require_account(conn)
                if not check_password_hash(account['password_hash'], request.form.get('current_password', '')[:129]):
                    raise ValueError('Your current password is incorrect.')
                if check_password_hash(account['password_hash'], value):
                    raise ValueError('Choose a different password from your temporary or current password.')
                conn.execute('UPDATE accounts SET password_hash=?, must_change=0, version=version+1 WHERE id=?',
                             (generate_password_hash(value), account['id']))
                conn.execute('DELETE FROM teacher_sessions WHERE teacher_id=?', (account['id'],))
                account = conn.execute('SELECT * FROM accounts WHERE id=?', (account['id'],)).fetchone()
                token = new_session(conn, account)
            response = redirect(url_for('teachers.give'))
            response.set_cookie(COOKIE, token, max_age=8 * 3600, **cookie_options())
            return response
        except ValueError as error_value:
            error = str(error_value)
    return render_template('teacher/password.html', page_title='Change password', active='', error=error)


def require_account(conn):
    """Recheck permissions while holding the write lock (reset/deactivation races)."""
    row = conn.execute('SELECT * FROM accounts WHERE id=?', (g.teacher['id'],)).fetchone()
    if not row or not row['active'] or row['version'] != g.teacher_session['version'] or g.teacher_session['expires_at'] <= now():
        abort(403, 'Your session ended. Please log in again.')
    return row


@teachers.post('/logout')
def logout():
    db().execute('DELETE FROM teacher_sessions WHERE token_hash=?', (g.teacher_session['token_hash'],))
    response = redirect(url_for('teachers.login'))
    response.delete_cookie(COOKIE, path='/')
    return response


@teachers.route('/give', methods=['GET', 'POST'])
def give():
    error, status = None, 200
    key = request.form.get('submission_key', '') if request.method == 'POST' else secrets.token_urlsafe(24)
    if request.method == 'POST':
        try:
            student = clean('student', 'Student name')
            reason = clean('reason', 'Reason', False, 240)
            house = clean('house', 'House')
            if house not in HOUSES:
                raise ValueError('Choose a house.')
            amount_text = request.form.get('amount', '')
            if not re.fullmatch(r'[0-9]{1,3}', amount_text):
                raise ValueError('Enter a whole number of coins.')
            amount = int(amount_text)
            if not re.fullmatch(r'[A-Za-z0-9_-]{24,64}', key):
                raise ValueError('Reload the form before giving coins.')
            fingerprint = digest(json.dumps([student, house, amount, reason], ensure_ascii=False))
            # Same lock as manual JSON saves: a new-school-year change cannot split an award.
            lock_path = Path(current_app.config['DATA_DIR']) / '.admin-write.lock'
            with lock_path.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                with transaction() as conn:
                    account = require_account(conn)
                    if account['must_change']:
                        abort(403)
                    existing = conn.execute('SELECT * FROM awards WHERE teacher_id=? AND submission_key=?', (account['id'], key)).fetchone()
                    if existing:
                        if existing['fingerprint'] != fingerprint:
                            raise ValueError('This form was already used. Reload it to give a different award.')
                        award_id = existing['id']
                    else:
                        if not 1 <= amount <= max_coins():
                            raise ValueError(f'Give between 1 and {max_coins()} whole coins.')
                        year = manual_data()['school_year']
                        if year != school_year():
                            raise ValueError('The administrator needs to start the current school year before you can give coins.')
                        award_id = secrets.token_hex(16)
                        conn.execute('''INSERT INTO awards
                            (id,teacher_id,teacher_name,student,house,amount,reason,created_at,school_year,submission_key,fingerprint)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                            (award_id, account['id'], account['display_name'], student, house, amount, reason, now(), year, key, fingerprint))
            return redirect(url_for('teachers.give', saved=award_id), code=303)
        except ValueError as error_value:
            error, status = str(error_value), 400
        except sqlite3.OperationalError:
            error, status = 'The award could not be confirmed. Try again with this form; it will only be counted once.', 503
    saved = None
    if request.args.get('saved'):
        saved = db().execute('SELECT * FROM awards WHERE id=? AND teacher_id=?', (request.args['saved'], g.teacher['id'])).fetchone()
    return render_template('teacher/give.html', page_title='Give coins', active='', error=error, saved=saved,
                           submission_key=key, limit=max_coins()), status


@teachers.get('/awards')
def awards():
    page = max(1, min(request.args.get('page', 1, type=int), 100000))
    rows = db().execute('SELECT * FROM awards WHERE teacher_id=? ORDER BY created_at DESC,id DESC LIMIT 21 OFFSET ?',
                        (g.teacher['id'], (page-1)*20)).fetchall()
    return render_template('teacher/awards.html', page_title='My awards', active='', awards=rows[:20], more=len(rows)>20, page=page, clock=now())


@teachers.post('/awards/<award_id>/undo')
def undo(award_id):
    if request.form.get('confirmed') != 'yes':
        abort(400, 'Confirm that you want to undo this award.')
    with transaction() as conn:
        require_account(conn)
        award = conn.execute('SELECT * FROM awards WHERE id=? AND teacher_id=?', (award_id, g.teacher['id'])).fetchone()
        if not award:
            abort(404)
        if not award['reversed_at']:
            if not 0 <= now() - award['created_at'] < 600:
                abort(403, 'The 10-minute undo window has ended. Ask the administrator to correct this award.')
            conn.execute('UPDATE awards SET reversed_at=?, reversed_by=?, reversal_reason=? WHERE id=?',
                         (now(), 'teacher:' + g.teacher['id'], 'Undone by awarding teacher within 10 minutes.', award_id))
    return redirect(url_for('teachers.awards'), code=303)


@public_coins.get('/today')
def today():
    return feed_response('day')


@public_coins.get('/week/<house>')
def week(house):
    if house not in HOUSES:
        abort(404)
    return feed_response('week', house)


def feed_response(period, house=None):
    try:
        data = feed(period, house, limit=min(100, max(1, request.args.get('limit', 3 if period == 'day' else 6, type=int))), cursor=request.args.get('cursor'))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    if period == 'day' and request.args.get('summary'):
        from app import standings_data
        board = standings_data()
        data['summary'] = {'rows': [{k: row[k] for k in ('id','total','rank','gain','movement')} for row in board['rows']],
                           'donation_total': board['donation_total'], 'baseline_at': board['baseline_at'], 'date': board['date']}
        if request.args['summary'] == 'board':
            data['standings_html'] = render_template('partials/standings_board.html', board=board)
    response = jsonify(data)
    response.headers['Cache-Control'] = 'no-store'
    return response


def init_teachers(app):
    app.config['TEACHER_DUMMY_HASH'] = generate_password_hash(secrets.token_urlsafe(32))
    app.before_request(authenticated_teacher)
    app.register_blueprint(teachers)
    app.register_blueprint(public_coins)

    @app.after_request
    def personalized_pages(response):
        if g.get('teacher'):
            response.headers['Cache-Control'] = 'private, no-store'
            response.vary.add('Cookie')
        return response

    @app.context_processor
    def helpers():
        return {'teacher_account': g.get('teacher'), 'teacher_csrf': teacher_csrf, 'public_award': public_award}
