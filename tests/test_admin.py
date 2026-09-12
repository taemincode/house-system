import copy
import io
import json
import re
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from PIL import Image
from werkzeug.security import generate_password_hash

from app import app, ROOT
from admin import attempts


class AdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = generate_password_hash('correct-test-password')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original_config = dict(app.config)
        self.original_static = app.static_folder
        shutil.copytree(ROOT / 'data', self.root / 'data')
        shutil.copyfile(ROOT / 'tests/fixtures/standings.json', self.root / 'data/standings.json')
        self.assets = self.root / 'static' / 'images'
        self.assets.mkdir(parents=True)
        (self.assets / 'test.obj').write_text('mtllib test.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
        (self.assets / 'test.mtl').write_text('newmtl gold\nKd 1 0.8 0\n')
        image = io.BytesIO()
        Image.new('RGB', (2, 2), 'red').save(image, 'PNG')
        self.png = image.getvalue()
        (self.assets / 'test.png').write_bytes(self.png)
        self.auth = self.root / 'auth.json'
        self.auth.write_text(json.dumps({'password_hash': self.password_hash, 'version': 'test-version'}))
        app.config.update(TESTING=True, DATA_DIR=self.root / 'data', ADMIN_AUTH_FILE=self.auth,
                          SECRET_KEY='test-only-secret', SESSION_COOKIE_SECURE=False, COINS_DB=self.root / 'coins.sqlite3')
        app.static_folder = str(self.root / 'static')
        attempts.clear()
        self.client = app.test_client()
        self.initial_site = (self.root / 'data/site.json').read_bytes()

    def tearDown(self):
        self.assertEqual((self.root / 'data/site.json').read_bytes(), self.initial_site)
        app.config.clear()
        app.config.update(self.original_config)
        app.static_folder = self.original_static
        self.temp.cleanup()

    def token(self):
        with self.client.session_transaction(path='/admin') as session:
            session.setdefault('_csrf', 'test-csrf')
            return session['_csrf']

    def login(self):
        response = self.client.post('/admin', data={'_csrf': self.token(), 'password': 'correct-test-password'})
        self.assertEqual(response.status_code, 302)
        return response

    def contents(self, name):
        return json.loads((self.root / 'data' / f'{name}.json').read_text())

    def form(self, dataset, **values):
        import hashlib
        return {'_csrf': self.token(), '_version': hashlib.sha256((self.root / 'data' / f'{dataset}.json').read_bytes()).hexdigest(), **values}

    def test_password_login_logout_and_protection(self):
        for path in ['/admin/dashboard', '/admin/events', '/admin/houses', '/admin/standings', '/admin/trophies']:
            self.assertEqual(self.client.get(path).status_code, 302)
        self.assertEqual(self.client.post('/admin/events/new').status_code, 302)
        self.assertEqual(self.client.post('/admin', data={'password': 'correct-test-password'}).status_code, 400)
        self.assertEqual(self.client.post('/admin', data={'_csrf': self.token(), 'password': 'wrong'}).status_code, 401)
        login = self.login()
        self.assertIn('HttpOnly', login.headers['Set-Cookie'])
        self.assertIn('SameSite=Lax', login.headers['Set-Cookie'])
        self.assertIn('Path=/admin', login.headers['Set-Cookie'])
        self.assertEqual(self.client.get('/admin/dashboard').status_code, 200)
        self.assertEqual(self.client.post('/admin/events/new', data={'name': 'Forbidden'}).status_code, 400)
        self.client.post('/admin/logout', data={'_csrf': self.token()})
        self.assertEqual(self.client.get('/admin/dashboard').status_code, 302)

    def test_rate_limit_and_password_rotation(self):
        for _ in range(5):
            self.client.post('/admin', data={'_csrf': self.token(), 'password': 'wrong'})
        self.assertEqual(self.client.post('/admin', data={'_csrf': self.token(), 'password': 'correct-test-password'}).status_code, 429)
        attempts.clear()
        self.login()
        self.auth.write_text(json.dumps({'password_hash': self.password_hash, 'version': 'changed'}))
        self.assertEqual(self.client.get('/admin/dashboard').status_code, 302)

    def test_all_editors_render_and_no_site_editor(self):
        self.login()
        paths = ['/admin/dashboard', '/admin/events', '/admin/events/new', '/admin/events/team-relay/edit',
                 '/admin/houses', '/admin/houses/rivers/teachers/new', '/admin/houses/rivers/teachers/0/edit',
                 '/admin/standings', '/admin/standings/new', '/admin/standings/0/edit',
                 '/admin/trophies', '/admin/trophies/new', '/admin/trophies/0/edit', '/admin/trophies/1/edit']
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.client.get('/admin/site').status_code, 404)
        self.assertNotIn('href="/admin', self.client.get('/').get_data(as_text=True))

    def test_events_create_edit_complete_delete(self):
        self.login()
        fields = dict(name='Test event', status='upcoming', description='Everyone can join.', date='', prize_1='20')
        self.assertEqual(self.client.post('/admin/events/new', data=self.form('events', **fields)).status_code, 302)
        event = self.contents('events')[-1]
        self.assertIsNone(event['date'])
        self.assertEqual(self.client.get('/admin/events').status_code, 200)
        fields.update(status='completed', date=date.today().isoformat())
        for index, house in enumerate(['rivers', 'castles', 'forests', 'bridges']):
            fields['place_' + house] = str(index + 1)
            fields['coins_' + house] = str(20 - index * 5)
        before = self.contents('standings')
        self.assertEqual(self.client.post('/admin/events/' + event['id'] + '/edit', data=self.form('events', **fields)).status_code, 302)
        self.assertEqual(len(self.contents('events')[-1]['results']), 4)
        self.assertEqual(self.contents('standings'), before)
        self.client.post('/admin/events/' + event['id'] + '/delete', data=self.form('events'))
        self.assertFalse(any(e['id'] == event['id'] for e in self.contents('events')))

    def test_invalid_event_and_stale_form_do_not_save(self):
        self.login()
        original = self.contents('events')
        self.client.post('/admin/events/new', data=self.form('events', name='Test', status='completed', date='not-date'))
        self.assertEqual(self.contents('events'), original)
        stale = self.form('events', name='Stale', status='upcoming', description='Test')
        self.client.post('/admin/events/new', data=self.form('events', name='New', status='upcoming', description='Test'))
        result = self.client.post('/admin/events/new', data=stale)
        self.assertIn('Someone changed', result.get_data(as_text=True))
        self.assertEqual(len(self.contents('events')), len(original) + 1)

    def test_teachers_require_name_and_preserve_house_metadata(self):
        self.login()
        original = self.contents('houses')
        route = '/admin/houses/rivers/teachers/new'
        self.client.post(route, data=self.form('houses', name='', role='teacher'))
        self.assertEqual(self.contents('houses'), original)
        self.assertEqual(self.client.post(route, data=self.form('houses', name='New teacher', role='head')).status_code, 302)
        river = self.contents('houses')[0]
        self.assertEqual(sum(t['is_head'] for t in river['teachers']), 1)
        self.assertEqual(river['teachers'][-1]['name'], 'New teacher')
        self.assertEqual(river['logo'], original[0]['logo'])
        self.client.post(f'/admin/houses/rivers/teachers/{len(river["teachers"])-1}/delete', data=self.form('houses'))
        self.assertEqual(len(self.contents('houses')[0]['teachers']), len(original[0]['teachers']))

    def test_count_confirmation_validation_and_history(self):
        self.login()
        original = self.contents('standings')
        fields = dict(date='2026-09-12', rivers='20', castles='0', forests='30', bridges='10')
        self.client.post('/admin/standings/new', data=self.form('standings', **fields))
        self.assertEqual(self.contents('standings'), original)
        fields['confirmed'] = 'yes'
        self.client.post('/admin/standings/new', data=self.form('standings', **{**fields, 'rivers': '-1'}))
        self.assertEqual(self.contents('standings'), original)
        self.assertEqual(self.client.post('/admin/standings/new', data=self.form('standings', **fields)).status_code, 302)
        self.assertEqual(len(self.contents('standings')['snapshots']), len(original['snapshots']) + 1)
        saved = self.contents('standings')['snapshots'][-1]
        self.assertEqual(saved['added'], {'rivers': 20, 'castles': 0, 'forests': 30, 'bridges': 10})
        self.assertEqual(saved['totals'], {'rivers': 1304, 'castles': 1106, 'forests': 1012, 'bridges': 875})
        self.client.post('/admin/standings/new', data=self.form('standings', **fields))
        self.assertEqual(len(self.contents('standings')['snapshots']), len(original['snapshots']) + 1)
        self.client.post('/admin/standings/0/delete', data=self.form('standings'))
        self.assertEqual(len(self.contents('standings')['snapshots']), len(original['snapshots']) + 1)
        self.client.post('/admin/standings/0/delete', data=self.form('standings', confirmed='yes'))
        self.assertEqual(len(self.contents('standings')['snapshots']), len(original['snapshots']))

    def test_backdated_addition_edit_move_and_delete_recalculate(self):
        self.login()
        original = self.contents('standings')
        fields = dict(date='2026-09-08', rivers='5', castles='0', forests='0', bridges='0', confirmed='yes')
        self.assertEqual(self.client.post('/admin/standings/new', data=self.form('standings', **fields)).status_code, 302)
        snapshots = self.contents('standings')['snapshots']
        self.assertEqual(snapshots[1]['totals']['rivers'], 1266)
        self.assertEqual(snapshots[2]['totals']['rivers'], 1289)
        fields['rivers'] = '10'
        self.client.post('/admin/standings/1/edit', data=self.form('standings', **fields))
        self.assertEqual(self.contents('standings')['snapshots'][-1]['totals']['rivers'], 1294)
        fields['date'] = '2026-09-12'
        self.client.post('/admin/standings/1/edit', data=self.form('standings', **fields))
        snapshots = self.contents('standings')['snapshots']
        self.assertEqual(snapshots[1]['totals'], original['snapshots'][-1]['totals'])
        self.assertEqual(snapshots[2]['totals']['rivers'], 1294)
        self.client.post('/admin/standings/2/delete', data=self.form('standings', confirmed='yes'))
        self.assertEqual(self.contents('standings')['snapshots'][-1]['totals'], original['snapshots'][-1]['totals'])

    def test_legacy_counts_edit_as_additions_and_read_only_views(self):
        self.login()
        original = self.contents('standings')
        html = self.client.get('/admin/standings/1/edit').get_data(as_text=True)
        self.assertIn('Rivers coins added', html)
        self.assertRegex(html, r'name="rivers" value="23"')
        self.assertEqual(self.contents('standings'), original)
        fields = dict(date='2026-09-12', rivers='10', castles='0', forests='0', bridges='0', confirmed='yes')
        self.client.post('/admin/standings/new', data=self.form('standings', **fields))
        fields.update(date='2026-09-11', rivers='30', castles='16', forests='150', bridges='0')
        self.client.post('/admin/standings/1/edit', data=self.form('standings', **fields))
        snapshots = self.contents('standings')['snapshots']
        self.assertEqual(snapshots[1]['totals']['rivers'], 1291)
        self.assertEqual(snapshots[2]['totals']['rivers'], 1301)
        self.assertEqual(snapshots[2]['added']['rivers'], 10)

    def test_first_addition_starts_at_zero_and_overflow_is_rejected(self):
        self.login()
        board = self.contents('standings')
        board['snapshots'] = []
        (self.root / 'data/standings.json').write_text(json.dumps(board))
        fields = dict(date='2026-09-11', rivers='10', castles='20', forests='0', bridges='5', confirmed='yes')
        self.client.post('/admin/standings/new', data=self.form('standings', **fields))
        current = self.contents('standings')
        self.assertEqual(current['snapshots'][0]['totals'], {'rivers': 10, 'castles': 20, 'forests': 0, 'bridges': 5})
        fields.update(date='2026-09-12', rivers='9007199254740991')
        response = self.client.post('/admin/standings/new', data=self.form('standings', **fields))
        self.assertIn('too large', response.get_data(as_text=True))
        self.assertEqual(self.contents('standings'), current)

    def test_trophy_uploads_and_required_fields(self):
        self.login()
        original = self.contents('trophies')
        fields = dict(house='rivers', year='2025/2026', title='House System', kind='3d', obj_existing='images/test.obj')
        self.client.post('/admin/trophies/new', data=self.form('trophies', **fields))
        self.assertEqual(self.contents('trophies'), original)
        fields['obj_upload'] = (io.BytesIO((self.assets / 'test.obj').read_bytes()), 'shape.obj')
        fields['mtl_upload'] = (io.BytesIO((self.assets / 'test.mtl').read_bytes()), 'colors.mtl')
        self.assertEqual(self.client.post('/admin/trophies/new', data=self.form('trophies', **fields)).status_code, 302)
        self.assertTrue(self.contents('trophies')[-1]['obj'].startswith('images/uploads/'))
        self.assertEqual(self.contents('trophies')[-1]['up_axis'], 'z')

    def test_trophy_uploads_reject_glb_and_traversal(self):
        self.login()
        original = self.contents('trophies')
        fields = dict(house='rivers', year='2025/2026', title='Test', kind='3d', mtl_existing='images/test.mtl')
        response = self.client.post('/admin/trophies/new', data=self.form('trophies', **fields, obj_upload=(io.BytesIO(b'glTF'), 'bad.glb')))
        self.assertIn('files only', response.get_data(as_text=True))
        self.assertEqual(self.contents('trophies'), original)
        self.client.post('/admin/trophies/new', data=self.form('trophies', **fields, obj_existing='../data/site.json'))
        self.assertEqual(self.contents('trophies'), original)
        response = self.client.post('/admin/trophies/new', data=self.form('trophies', **fields,
            obj_upload=(io.BytesIO(b'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'), '../../shape.obj'),
            mtl_upload=(io.BytesIO(b'newmtl gold\nKd 1 0.8 0\n'), 'colors.mtl')))
        self.assertEqual(response.status_code, 302)
        path = self.contents('trophies')[-1]['obj']
        self.assertTrue(path.startswith('images/uploads/'))
        self.assertTrue((self.root / 'static' / path).is_file())

    def test_edit_keeps_current_files_without_file_selection(self):
        self.login()
        trophies = self.contents('trophies')
        trophies[0].update(obj='images/test.obj', mtl='images/test.mtl')
        (self.root / 'data/trophies.json').write_text(json.dumps(trophies))
        fields = dict(house='rivers', year='2025/2026', title='Updated title', kind='3d',
                      obj_existing='../untrusted.obj')
        self.assertEqual(self.client.post('/admin/trophies/0/edit', data=self.form('trophies', **fields)).status_code, 302)
        saved = self.contents('trophies')[0]
        self.assertEqual(saved['obj'], 'images/test.obj')
        self.assertEqual(saved['mtl'], 'images/test.mtl')
        self.assertEqual(self.client.post('/admin/trophies/0/edit', data=self.form('trophies', **fields,
            obj_upload=(io.BytesIO((self.assets / 'test.obj').read_bytes()), 'replacement.obj'))).status_code, 302)
        saved = self.contents('trophies')[0]
        self.assertTrue(saved['obj'].startswith('images/uploads/'))
        self.assertEqual(saved['mtl'], 'images/test.mtl')
        for path in ['/admin/trophies/new', '/admin/trophies/0/edit', '/admin/houses/rivers/teachers/0/edit']:
            html = self.client.get(path).get_data(as_text=True)
            self.assertNotIn('Choose an existing file', html)
            self.assertNotIn('_existing', html)

    def test_teacher_photo_preserved_or_removed(self):
        self.login()
        houses = self.contents('houses')
        houses[0]['teachers'][0]['image'] = 'images/test.png'
        (self.root / 'data/houses.json').write_text(json.dumps(houses))
        fields = dict(name='Teacher', role='teacher')
        route = '/admin/houses/rivers/teachers/0/edit'
        self.client.post(route, data=self.form('houses', **fields))
        self.assertEqual(self.contents('houses')[0]['teachers'][0]['image'], 'images/test.png')
        self.client.post(route, data=self.form('houses', **fields, remove_image='yes'))
        self.assertIsNone(self.contents('houses')[0]['teachers'][0]['image'])

    def test_image_trophy_upload_and_delete(self):
        self.login()
        fields = dict(house='forests', year='2025/2026', title='Sports Day', kind='image')
        self.client.post('/admin/trophies/new', data=self.form('trophies', **fields,
            image_upload=(io.BytesIO(b'<html>bad</html>'), 'fake.png')))
        original_count = len(self.contents('trophies'))
        self.assertEqual(self.client.post('/admin/trophies/new', data=self.form('trophies', **fields,
            image_upload=(io.BytesIO(self.png), 'photo.png'))).status_code, 302)
        trophy = self.contents('trophies')[-1]
        self.assertTrue((self.root / 'static' / trophy['image']).is_file())
        self.assertNotIn('obj', trophy)
        self.client.post(f'/admin/trophies/{original_count}/delete', data=self.form('trophies'))
        self.assertEqual(len(self.contents('trophies')), original_count)


if __name__ == '__main__':
    unittest.main()
