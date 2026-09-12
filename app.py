"""WIS Houses: public pages and a separate password-protected admin area."""
import json
from datetime import date
from pathlib import Path

from flask import Flask, abort, render_template, request
from digital import combined_board, feed, init_digital, WARSAW
from datetime import datetime

ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
app.config['DATA_DIR'] = ROOT / 'data'


def read_data(name):
    return json.loads((Path(app.config['DATA_DIR']) / f'{name}.json').read_text(encoding='utf-8'))


def ranks(totals):
    """Competition ranking: tied totals receive the same rank (1, 1, 3)."""
    return {key: 1 + sum(other > value for other in totals.values())
            for key, value in totals.items()}


def build_standings(houses, data):
    snapshots = sorted(data['snapshots'], key=lambda s: date.fromisoformat(s['date']))
    current = snapshots[-1] if snapshots else {'date': None, 'totals': {}}
    previous = snapshots[-2] if len(snapshots) > 1 else None
    ids = [h['id'] for h in houses]

    def valid_totals(snapshot):
        totals = snapshot.get('totals', {}) if snapshot else {}
        for value in totals.values():
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError('House totals must be non-negative integers or null.')
        return {key: totals[key] for key in ids if totals.get(key) is not None}

    totals, old_totals = valid_totals(current), valid_totals(previous)
    complete = len(totals) == len(ids)
    positions = ranks(totals) if complete else {}
    old_positions = ranks(old_totals) if len(old_totals) == len(ids) else {}
    rows = []
    for house in houses:
        key = house['id']
        rows.append({**house, 'total': totals.get(key), 'rank': positions.get(key),
                     'gain': totals[key] - old_totals[key] if key in totals and key in old_totals else None,
                     'movement': old_positions[key] - positions[key] if key in old_positions and key in positions else None})
    if complete:
        rows.sort(key=lambda row: (-row['total'], row['name']))
    return {'rows': rows, 'complete': complete,
            'tied': complete and len(set(totals.values())) != len(ids),
            'date': current['date'], 'previous_date': previous['date'] if previous else None,
            'school_year': data['school_year']}


@app.template_filter('number')
def number(value):
    return f'{value:,}' if value is not None else '—'


@app.template_filter('signed')
def signed(value):
    return f'{value:+,}'


@app.template_filter('ordinal')
def ordinal(value):
    if value is None:
        return '—'
    suffix = 'th' if 10 <= value % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(value % 10, 'th')
    return f'{value}{suffix}'


@app.template_filter('dateformat')
def dateformat(value, fmt='%d %b'):
    return date.fromisoformat(value).strftime(fmt) if value else 'Date to come'


@app.template_global()
def image_exists(path):
    if not path:
        return False
    root = Path(app.static_folder).resolve()
    candidate = (root / path).resolve()
    return candidate.is_relative_to(root) and candidate.is_file()


@app.context_processor
def shared():
    context = {'site': read_data('site'), 'houses': read_data('houses')}
    if not (request.endpoint or '').startswith('admin.'):
        context['daily'] = feed(random_start=True)
    return context


def standings_data():
    return combined_board(read_data('houses'), read_data('standings'), build_standings)


def events_data():
    events = read_data('events')
    # A result is published explicitly, not inferred from an event's date.
    results = sorted((e for e in events if e['status'] == 'completed'), key=lambda e: e['date'], reverse=True)
    upcoming = sorted((e for e in events if e['status'] == 'upcoming'), key=lambda e: e['date'] or '9999')
    return upcoming, results


@app.get('/')
def standings():
    return render_template('standings.html', active='standings', page_title='House standings', board=standings_data())


@app.get('/houses/<slug>')
def house(slug):
    board = standings_data()
    selected = next((h for h in board['rows'] if h['id'] == slug), None)
    if selected is None:
        abort(404)
    _, results = events_data()
    recent = [{'event': event, **result} for event in results for result in event['results'] if result['house'] == slug][:6]
    trophies = [t for t in read_data('trophies') if t['house'] == slug]
    has_obj_trophies = any(image_exists(t.get('obj')) and image_exists(t.get('mtl')) for t in trophies)
    has_glb_trophies = any(image_exists(t.get('model')) for t in trophies)
    return render_template('house.html', active='houses', page_title=selected['name'],
                           house=selected, recent=recent, trophies=trophies,
                           has_obj_trophies=has_obj_trophies, has_glb_trophies=has_glb_trophies, weekly=feed('week', slug, 6))


@app.get('/events')
def events():
    upcoming, results = events_data()
    view = 'results' if request.args.get('view') == 'results' else 'upcoming'
    lookup = {h['id']: h for h in read_data('houses')}
    return render_template('events.html', active='events', page_title='Events',
                           upcoming=upcoming, results=results, view=view, house_lookup=lookup)


@app.get('/about')
def about():
    return render_template('about.html', active='about', page_title='About', board=standings_data())


@app.errorhandler(404)
def not_found(error):
    return render_template('404.html', active='', page_title='Page not found'), 404


@app.template_filter('warsawtime')
def warsawtime(value):
    return datetime.fromtimestamp(value, WARSAW).strftime('%d %b %Y, %H:%M')


init_digital(app)
from admin import init_admin
import digital_admin  # Register the additional admin routes before the blueprint.
init_admin(app)
from teachers import init_teachers
init_teachers(app)


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5001)
