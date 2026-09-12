import copy
import unittest
import tempfile
import shutil
from pathlib import Path
from app import app, ROOT, build_standings, read_data


class WebsiteTests(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(app.config)
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        shutil.copytree(ROOT / 'data', root / 'data')
        shutil.copyfile(ROOT / 'tests/fixtures/standings.json', root / 'data/standings.json')
        app.config.update(DATA_DIR=root / 'data', COINS_DB=root / 'coins.sqlite3')
        self.client = app.test_client()
        self.houses = read_data('houses')
        self.data = copy.deepcopy(read_data('standings'))

    def tearDown(self):
        app.config.clear()
        app.config.update(self.original_config)
        self.temp.cleanup()

    def test_all_pages_and_assets(self):
        paths = ['/', '/events', '/events?view=results', '/about']
        paths += ['/houses/' + h['id'] for h in self.houses]
        paths += ['/static/' + h['logo'] for h in self.houses]
        for path in paths:
            with self.subTest(path=path):
                with self.client.get(path) as response:
                    self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/houses/unknown').status_code, 404)
        self.assertEqual(self.client.post('/').status_code, 405)

    def test_snapshot_comparisons(self):
        board = build_standings(self.houses, self.data)
        self.assertEqual([r['gain'] for r in board['rows']], [23, 16, 150, 0])
        self.assertEqual([r['movement'] for r in board['rows']], [0, 0, 1, -1])
        self.assertEqual(board, build_standings(self.houses, {**self.data, 'snapshots': self.data['snapshots'][::-1]}))

    def test_ties(self):
        self.data['snapshots'][-1]['totals']['castles'] = 1284
        board = build_standings(self.houses, self.data)
        self.assertTrue(board['tied'])
        self.assertEqual([r['rank'] for r in board['rows']], [1, 1, 3, 4])

    def test_missing_totals_are_not_zero_or_ranked(self):
        self.data['snapshots'][-1]['totals']['forests'] = None
        board = build_standings(self.houses, self.data)
        self.assertFalse(board['complete'])
        self.assertTrue(all(r['rank'] is None for r in board['rows']))
        forest = next(r for r in board['rows'] if r['id'] == 'forests')
        self.assertIsNone(forest['total'])
        self.assertIsNone(forest['gain'])

    def test_first_snapshot_has_no_fake_changes(self):
        self.data['snapshots'] = self.data['snapshots'][-1:]
        self.assertTrue(all(r['gain'] is None and r['movement'] is None for r in build_standings(self.houses, self.data)['rows']))
        self.data['snapshots'] = []
        self.assertFalse(build_standings(self.houses, self.data)['complete'])

    def test_invalid_totals_rejected(self):
        for invalid in [-5, True, '123', 1.5]:
            self.data['snapshots'][-1]['totals']['rivers'] = invalid
            with self.assertRaises(ValueError):
                build_standings(self.houses, self.data)

    def test_house_results_and_trophy_order(self):
        html = self.client.get('/houses/rivers').get_data(as_text=True)
        self.assertEqual(html.count('class="recent-result"'), 6)
        self.assertLess(html.index('id="teachers-title"'), html.index('id="trophies-title"'))
        self.assertNotIn('id="trophies-title"', self.client.get('/houses/forests').get_data(as_text=True))

    def test_event_totals_not_added_twice(self):
        rivers = next(r for r in build_standings(self.houses, self.data)['rows'] if r['id'] == 'rivers')
        self.assertEqual(rivers['total'], 1284)

    def test_rivers_3d_trophy(self):
        html = self.client.get('/houses/rivers').get_data(as_text=True)
        self.assertEqual(html.count('<model-viewer '), 1)
        self.assertIn('data-obj="/static/images/house-trophy/tinker.obj"', html)
        self.assertIn('data-mtl="/static/images/house-trophy/obj.mtl"', html)
        self.assertIn('Winners of the 2025/2026 House System', html)
        self.assertIn('camera-controls', html)
        self.assertIn('Winners of the 2025/2026 Sports Day', html)
        for path in ['/static/images/house-trophy/tinker.obj',
                     '/static/images/house-trophy/obj.mtl',
                     '/static/js/obj-trophies.js',
                     '/static/js/vendor/three/three.module.min.js',
                     '/static/js/model-viewer.min.js', '/static/js/trophies.js']:
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200)
        forest = self.client.get('/houses/forests').get_data(as_text=True)
        self.assertNotIn('model-viewer.min.js', forest)
        self.assertNotIn('obj-trophies.js', forest)

    def test_event_views_render_without_javascript(self):
        upcoming = self.client.get('/events').get_data(as_text=True)
        results = self.client.get('/events?view=results').get_data(as_text=True)
        self.assertIn('id="results" aria-label="Event results" hidden', upcoming)
        self.assertNotIn('id="upcoming" aria-label="Upcoming events" hidden', upcoming)
        self.assertIn('id="upcoming" aria-label="Upcoming events" hidden', results)
        self.assertNotIn('id="results" aria-label="Event results" hidden', results)


if __name__ == '__main__':
    unittest.main()
