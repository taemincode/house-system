"""Admin controls for teacher accounts, award corrections and digital coin settings."""
import json
import re
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from admin import admin, atomic_json, choice, failure, load, save, text
from digital import WARSAW, baseline, db, max_coins, now, school_year, transaction


def profiles():
    return {json.dumps([h['id'], t['name']], ensure_ascii=False): f"{t['name']} · {h['name']}"
            for h in load('houses') for t in h['teachers'] if t.get('name')}


@admin.get('/accounts')
def accounts():
    page = max(1, min(request.args.get('page', 1, type=int), 100000))
    rows = db().execute('SELECT * FROM accounts ORDER BY display_name,id LIMIT 21 OFFSET ?', ((page - 1) * 20,)).fetchall()
    return render_template('admin/accounts.html', title='Teacher accounts', accounts=rows[:20], more=len(rows)>20, page=page)


@admin.route('/accounts/new', methods=['GET', 'POST'])
@admin.route('/accounts/<account_id>/edit', methods=['GET', 'POST'])
def account_edit(account_id=None):
    account = db().execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone() if account_id else None
    if account_id and not account:
        abort(404)
    options = profiles()
    if account and account['profile'] and account['profile'] not in options:
        options[account['profile']] = 'Previously linked profile (no longer in the public teacher list)'
    if request.method == 'POST':
        try:
            username = text('username', 'Username', True, 50).lower()
            if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,49}', username):
                raise ValueError('Use 3–50 letters, numbers, dots, hyphens or underscores for the username.')
            name = text('display_name', 'Teacher display name', True, 120)
            active = int(choice('active', 'Account status', ['1', '0']))
            profile = text('profile', 'Teacher profile', limit=300)
            if profile and profile not in options:
                raise ValueError('Choose a teacher profile from the list.')
            password = request.form.get('password', '')
            if (not account or password) and not 12 <= len(password) <= 128:
                raise ValueError('Use a temporary password with 12–128 characters.')
            with transaction() as conn:
                if account:
                    current = conn.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
                    if request.form.get('version') != str(current['version']):
                        raise ValueError('This account changed while you were editing. Reload and try again.')
                    conn.execute('''UPDATE accounts SET username=?,display_name=?,active=?,profile=?,password_hash=?,
                        must_change=?,version=version+1 WHERE id=?''',
                        (username,name,active,profile or None,generate_password_hash(password) if password else current['password_hash'],
                         1 if password else current['must_change'],account_id))
                    conn.execute('DELETE FROM teacher_sessions WHERE teacher_id=?', (account_id,))
                else:
                    conn.execute('''INSERT INTO accounts(id,username,display_name,password_hash,active,profile,created_at)
                        VALUES (?,?,?,?,?,?,?)''', (secrets.token_hex(16),username,name,generate_password_hash(password),active,profile or None,now()))
            flash('Account saved. Share the username and temporary password privately with the teacher.' if password else 'Account saved. Existing teacher sessions have ended.', 'success')
            return redirect(url_for('admin.accounts'))
        except sqlite3.IntegrityError:
            failure('That username is already taken.')
        except ValueError as error:
            failure(error)
    return render_template('admin/account_form.html', title='Edit account' if account else 'New teacher account', account=dict(account) if account else {}, profiles=options)


@admin.get('/awards')
def awards():
    conditions, args = [], []
    teacher = request.args.get('teacher', '')
    house = request.args.get('house', '')
    day = request.args.get('date', '')
    if teacher:
        conditions.append('teacher_id=?')
        args.append(teacher)
    if house:
        conditions.append('house=?')
        args.append(house)
    if day:
        try:
            from datetime import timedelta
            start = datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=WARSAW)
            conditions.extend(['created_at>=?', 'created_at<?'])
            args.extend([int(start.timestamp()), int((start + timedelta(days=1)).timestamp())])
        except ValueError:
            abort(400, 'Choose a valid date.')
    page = max(1, min(request.args.get('page', 1, type=int), 100000))
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    rows = db().execute('SELECT * FROM awards' + where + ' ORDER BY created_at DESC,id DESC LIMIT 21 OFFSET ?', [*args,(page-1)*20]).fetchall()
    accounts = db().execute('SELECT id,display_name,username FROM accounts ORDER BY display_name').fetchall()
    return render_template('admin/awards.html', title='Award history', awards=rows[:20], more=len(rows)>20, page=page,
                           accounts=accounts, filters={'teacher':teacher, 'house':house, 'date':day})


@admin.route('/awards/<award_id>', methods=['GET', 'POST'])
def award_detail(award_id):
    award = db().execute('SELECT * FROM awards WHERE id=?', (award_id,)).fetchone()
    if not award:
        abort(404)
    if request.method == 'POST':
        try:
            reason = text('reason', 'Reason for reversing', True, 500)
            if request.form.get('confirmed') != 'yes':
                raise ValueError('Confirm that you want to reverse this award.')
            with transaction() as conn:
                conn.execute('UPDATE awards SET reversed_at=?,reversed_by=?,reversal_reason=? WHERE id=? AND reversed_at IS NULL',
                             (now(),'administrator',reason,award_id))
            flash('Award reversed. Its coins no longer count towards totals or the donation.', 'success')
            return redirect(url_for('admin.award_detail', award_id=award_id))
        except ValueError as error:
            failure(error)
    return render_template('admin/award_detail.html', title='Award details', award=award)


@admin.route('/coin-settings', methods=['GET', 'POST'])
def coin_settings():
    board = load('standings')
    comparison = baseline(board, [h['id'] for h in load('houses')])
    if request.method == 'POST':
        try:
            action = choice('action', 'Action', ['limit', 'baseline', 'year'])
            if action == 'limit':
                limit = choice('max_coins', 'Maximum coins', [str(i) for i in range(1,101)])
                db().execute("UPDATE settings SET value=? WHERE key='max_coins'", (limit,))
                flash('Award limit updated.', 'success')
            elif action == 'baseline':
                if request.form.get('confirmed') != 'yes':
                    raise ValueError('Confirm the new comparison baseline.')
                from app import standings_data
                # Serialize with manual saves and awards for one consistent baseline.
                import fcntl
                with (Path(current_app.config['DATA_DIR']) / '.admin-write.lock').open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    with transaction() as conn:
                        current = standings_data()
                        conn.execute('INSERT OR REPLACE INTO baselines VALUES (?,?,?)',
                                     (current['school_year'],json.dumps({r['id']:r['total'] for r in current['rows']}),now()))
                flash('Comparison baseline updated. Future changes will be measured from now.', 'success')
            else:
                if request.form.get('confirmed') != 'yes':
                    raise ValueError('Confirm that you want to start a new school year.')
                year = school_year()
                if board['school_year'] >= year:
                    raise ValueError('The competition is already set to the current school year.')
                # Archive first; a stale form can leave an extra safe backup, never lost data.
                archive = Path(current_app.config['COINS_DB']).parent / 'manual-history'
                atomic_json(archive / f'{board["school_year"].replace("/", "-")}-{secrets.token_hex(6)}.json', board)
                zero = {h['id']:0 for h in load('houses')}
                save('standings', {'school_year':year, 'snapshots':[{'date':year[:4]+'-09-01','physical_counted_on':None,'totals':zero,'added':zero}]})
                flash('New school year started at zero. Previous awards and a backup of manual counts are retained.', 'success')
            return redirect(url_for('admin.coin_settings'))
        except ValueError as error:
            failure(error)
    return render_template('admin/coin_settings.html', title='Coin settings', limit=max_coins(), board=board, comparison=comparison, calendar_year=school_year())
