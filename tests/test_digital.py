"""Isolated, explicitly fictional data; never writes awards to the website database."""
from contextlib import closing
import hashlib
import json
import re
import secrets
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from werkzeug.security import generate_password_hash
from app import app, ROOT, standings_data
from digital import WARSAW, bounds, db, feed, school_year
from admin import attempts


class DigitalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_password = 'Demo-temporary-password!'
        cls.new_password = 'Demo-personal-password!'
        cls.password_hash = generate_password_hash(cls.temp_password)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = dict(app.config)
        shutil.copytree(ROOT / 'data', self.root / 'data')
        shutil.copyfile(ROOT / 'tests/fixtures/standings.json', self.root / 'data/standings.json')
        self.clock = int(datetime(2026,9,12,12,tzinfo=WARSAW).timestamp())
        auth = self.root / 'auth.json'
        auth.write_text(json.dumps({'password_hash':self.password_hash,'version':'demo-admin'}))
        app.config.update(TESTING=True, DATA_DIR=self.root/'data', COINS_DB=self.root/'coins.sqlite3', ADMIN_AUTH_FILE=auth,
                          SECRET_KEY='isolated-digital-tests-only', SESSION_COOKIE_SECURE=False, COINS_CLOCK=lambda:self.clock)
        self.admin = app.test_client()
        self.teacher = app.test_client()
        attempts.clear()
        with app.app_context():
            db()

    def tearDown(self):
        app.config.clear()
        app.config.update(self.config)
        self.temp.cleanup()

    def csrf(self, response):
        return re.search(r'name="_csrf" value="([^"]+)"', response.get_data(as_text=True)).group(1)

    def admin_login(self):
        page = self.admin.get('/admin')
        self.assertEqual(self.admin.post('/admin', data={'_csrf':self.csrf(page),'password':self.temp_password}).status_code,302)

    def admin_post(self, path, **fields):
        with self.admin.session_transaction(path='/admin') as session:
            token = session['_csrf']
        return self.admin.post(path,data={'_csrf':token,**fields})

    def create(self, username='demo.teacher', active=True, changed=True):
        with app.app_context():
            identifier = secrets.token_hex(16)
            db().execute('INSERT INTO accounts(id,username,display_name,password_hash,active,must_change,created_at) VALUES (?,?,?,?,?,?,?)',
                         (identifier,username,'DEMO Teacher',self.password_hash,int(active),int(not changed),self.clock))
            return identifier

    def login(self, client=None, username='demo.teacher', password=None):
        client = client or self.teacher
        page = client.get('/teacher/login')
        return client.post('/teacher/login',data={'_csrf':self.csrf(page),'username':username,'password':password or self.temp_password})

    def award_fields(self, client=None, **values):
        client = client or self.teacher
        page = client.get('/teacher/give')
        key = re.search(r'name="submission_key" value="([^"]+)"',page.get_data(as_text=True)).group(1)
        return {'_csrf':self.csrf(page),'submission_key':key,'student':'DEMO Student','house':'rivers','amount':'1','reason':'helping a demo classmate',**values}

    def give(self, **values):
        response = self.teacher.post('/teacher/give',data=self.award_fields(**values))
        self.assertEqual(response.status_code,303,response.get_data(as_text=True))
        return response.location.split('saved=')[1]

    def rows(self, query='SELECT * FROM awards', args=()):
        with app.app_context():
            return [dict(r) for r in db().execute(query,args)]

    def board(self):
        with app.app_context():
            return standings_data()

    def totals(self):
        return {r['id']:r['total'] for r in self.board()['rows']}

    def test_admin_account_create_first_login_change_and_private_cookie(self):
        self.admin_login()
        self.admin.get('/admin/accounts/new')
        response = self.admin_post('/admin/accounts/new',username='demo.new',display_name='DEMO New Teacher',password=self.temp_password,active='1',profile='')
        self.assertEqual(response.status_code,302)
        account = self.rows('SELECT * FROM accounts')[0]
        self.assertNotEqual(account['password_hash'],self.temp_password)
        self.assertEqual(account['must_change'],1)
        response = self.login(username='demo.new')
        self.assertTrue(response.location.endswith('/teacher/password'))
        self.assertIn('HttpOnly',response.headers['Set-Cookie'])
        self.assertIn('SameSite=Lax',response.headers['Set-Cookie'])
        self.assertEqual(self.teacher.get('/teacher/give').status_code,302)
        page = self.teacher.get('/teacher/password')
        response = self.teacher.post('/teacher/password',data={'_csrf':self.csrf(page),'current_password':self.temp_password,'password':self.new_password,'confirm_password':self.new_password})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.teacher.get('/teacher/give').status_code,200)
        self.assertEqual(self.rows('SELECT * FROM accounts')[0]['must_change'],0)
        self.assertEqual(self.teacher.get('/admin/accounts').status_code,302)
        self.assertEqual(self.admin.get('/teacher/give').status_code,302)

    def test_reset_deactivate_reactivate_invalidates_all_sessions(self):
        identifier = self.create()
        other = app.test_client()
        self.login(); self.login(other)
        self.admin_login(); self.admin.get('/admin/accounts/'+identifier+'/edit')
        fields = dict(username='demo.teacher',display_name='DEMO Teacher',password='',active='0',profile='',version='1')
        response = self.admin_post('/admin/accounts/'+identifier+'/edit',**fields)
        self.assertEqual(response.status_code,302)
        for client in (self.teacher,other):
            self.assertEqual(client.get('/teacher/give').status_code,302)
        self.assertEqual(self.login().status_code,401)
        fields.update(active='1',version='2',password='DEMO reset password!')
        self.admin_post('/admin/accounts/'+identifier+'/edit',**fields)
        self.assertEqual(self.login(password='DEMO reset password!').location,'/teacher/password')
        self.assertEqual(self.rows('SELECT * FROM teacher_sessions')[0]['version'],3)

    def test_login_throttle_csrf_expiry_and_no_registration(self):
        self.create()
        self.assertEqual(self.teacher.post('/teacher/login',data={'username':'demo.teacher','password':self.temp_password}).status_code,400)
        for _ in range(5):
            self.assertEqual(self.login(password='wrong').status_code,401)
        self.assertEqual(self.login().status_code,429)
        self.clock += 301
        self.assertEqual(self.login().status_code,302)
        self.assertEqual(self.teacher.post('/teacher/give',data={'student':'DEMO'}).status_code,400)
        self.clock += 8*3600
        self.assertEqual(self.teacher.get('/teacher/give').status_code,302)
        self.assertEqual(self.teacher.get('/teacher/register').status_code,404)

    def test_award_session_identity_duplicate_prg_and_cleared_form(self):
        identifier = self.create(); self.login()
        fields = self.award_fields(teacher_id='spoofed',amount='3')
        response = self.teacher.post('/teacher/give',data=fields)
        self.assertEqual(response.status_code,303)
        self.assertEqual(self.teacher.post('/teacher/give',data=fields).location,response.location)
        rows = self.rows()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['teacher_id'],identifier)
        self.assertEqual(rows[0]['amount'],3)
        self.assertEqual(rows[0]['school_year'],'2026/2027')
        html = self.teacher.get(response.location).get_data(as_text=True)
        self.assertIn('3 coins given to DEMO Student',html)
        self.assertIn('name="student" required maxlength="120" autocomplete="off" value=""',html)
        self.assertIn('name="amount" min="1" max="10" step="1" required value="1"',html)
        self.assertEqual(self.teacher.post('/teacher/give',data={**fields,'amount':'2'}).status_code,400)
        self.assertEqual(len(self.rows()),1)

    def test_server_validation_and_configurable_limit(self):
        self.create(); self.login()
        for override in ({'amount':'0'},{'amount':'-1'},{'amount':'1.5'},{'amount':'11'}, {'student':''},{'house':'invalid'}, {'reason':'x'*241}, {'student':'x'*121}):
            with self.subTest(override=override):
                self.assertEqual(self.teacher.post('/teacher/give',data=self.award_fields(**override)).status_code,400)
        self.assertEqual(len(self.rows()),0)
        self.admin_login(); self.admin.get('/admin/coin-settings')
        self.admin_post('/admin/coin-settings',action='limit',max_coins='20')
        self.give(amount='20',house='bridges',reason='')
        self.assertEqual(self.rows()[0]['amount'],20)
        response = self.admin_post('/admin/coin-settings',action='limit',max_coins='10000')
        self.assertEqual(response.status_code,200)
        self.assertIn('Choose a valid',response.get_data(as_text=True))

    def test_concurrent_unique_and_duplicate_submissions(self):
        self.create(); self.login()
        fields = self.award_fields()
        cookie = self.teacher.get_cookie('wis_teacher').value
        def submit(key):
            with app.test_client() as client:
                client.set_cookie('wis_teacher',cookie)
                return client.post('/teacher/give',data={**fields,'submission_key':key}).status_code
        shared = fields['submission_key']
        keys = [shared]*4 + [secrets.token_urlsafe(24) for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(list(pool.map(submit,keys)),[303]*10)
        self.assertEqual(len(self.rows()),7)
        self.assertEqual(self.totals()['rivers'],1291)

    def test_daily_weekly_public_allowlist_grammar_and_pagination(self):
        self.create(); self.login()
        for i in range(8):
            self.give(amount='2' if i%2 else '1',reason='' if i%2 else 'helping')
        client = app.test_client()
        payload = client.get('/api/coins/today').json
        self.assertEqual(len(payload['items']),3)
        self.assertEqual(payload['total'],12)
        self.assertNotIn('password_hash',json.dumps(payload))
        self.assertNotIn('teacher_id',json.dumps(payload))
        self.assertNotIn('reversal_reason',json.dumps(payload))
        self.assertTrue(all(item['message'].endswith('.') for item in payload['items']))
        second = client.get('/api/coins/today',query_string={'cursor':payload['next']}).json
        self.assertFalse(set(i['id'] for i in second['items']) & set(i['id'] for i in payload['items']))
        weekly = client.get('/api/coins/week/rivers').json
        self.assertEqual(weekly['total'],12)
        self.assertEqual(len(weekly['items']),6)
        self.assertEqual(client.get('/api/coins/week/forests').json['total'],0)
        self.assertEqual(client.get('/api/coins/today?cursor=bad').status_code,400)
        html=client.get('/houses/rivers').get_data(as_text=True)
        self.assertLess(html.index('id="recent-title"'),html.index('id="weekly-title"'))
        self.assertLess(html.index('id="weekly-title"'),html.index('id="teachers-title"'))
        self.assertLess(html.index('id="teachers-title"'),html.index('id="trophies-title"'))
        self.clock+=86400
        self.assertEqual(client.get('/api/coins/today').json['total'],0)
        self.assertEqual(client.get('/api/coins/week/rivers').json['total'],12)

    def test_escape_public_student_reason_and_private_history(self):
        self.create(); self.login()
        identifier=self.give(student='<script>alert(1)</script>',reason='<img src=x onerror=alert(1)>')
        html=self.teacher.get('/').get_data(as_text=True)
        self.assertNotIn('<script>alert(1)</script>',html)
        self.assertIn('&lt;script&gt;',html)
        self.admin_login()
        for path in ['/admin/accounts','/admin/accounts/new','/admin/awards','/admin/awards/'+identifier,'/admin/coin-settings']:
            self.assertEqual(self.admin.get(path).status_code,200)
        self.assertNotIn('<img src=x onerror=alert(1)>',self.admin.get('/admin/awards/'+identifier).get_data(as_text=True))

    def test_manual_digital_donation_baseline_and_ties(self):
        self.create(); self.login()
        before=self.board()
        self.give(amount='5')
        after=self.board()
        self.assertEqual(after['donation_total'],before['donation_total']+5)
        self.assertEqual(after['baseline_at'],before['baseline_at'])
        self.assertEqual(next(r['gain'] for r in after['rows'] if r['id']=='rivers'),28)
        manual=json.loads((self.root/'data/standings.json').read_text())
        self.assertEqual(manual['snapshots'][-1]['totals']['rivers'],1284)
        manual['snapshots'][-1]['totals']['castles']=1289
        (self.root/'data/standings.json').write_text(json.dumps(manual))
        self.assertTrue(self.board()['tied'])
        self.assertEqual([r['rank'] for r in self.board()['rows']],[1,1,3,4])
        self.admin_login(); self.admin.get('/admin/coin-settings')
        self.admin_post('/admin/coin-settings',action='baseline',confirmed='yes')
        self.assertTrue(all(r['gain']==0 and r['movement']==0 for r in self.board()['rows']))
        self.give(house='castles',amount='1')
        after=self.board()
        self.assertEqual(after['rows'][0]['id'],'castles')
        self.assertEqual(after['rows'][0]['gain'],1)
        rivers=next(r for r in after['rows'] if r['id']=='rivers')
        self.assertEqual(rivers['movement'],-1)
        self.assertIn('Raised this school year',self.teacher.get('/about').get_data(as_text=True))
        api=self.teacher.get('/api/coins/today?summary=board').json
        self.assertEqual(api['summary']['donation_total'],after['donation_total'])
        self.assertIn('House standings',api['standings_html'])

    def test_unknown_manual_totals_remain_unknown(self):
        self.create(); self.login(); self.give(house='forests')
        path=self.root/'data/standings.json'; data=json.loads(path.read_text())
        data['snapshots'][-1]['totals']['forests']=None; path.write_text(json.dumps(data))
        board=self.board()
        self.assertIsNone(board['donation_total'])
        self.assertIsNone(next(r for r in board['rows'] if r['id']=='forests')['total'])
        self.assertTrue(all(r['rank'] is None for r in board['rows']))
        self.assertEqual(self.teacher.get('/api/coins/week/forests').json['total'],1)

    def test_teacher_undo_ownership_window_confirmation_and_retention(self):
        identifier=self.create(); self.login()
        award=self.give()
        token=self.csrf(self.teacher.get('/teacher/awards'))
        self.assertEqual(self.teacher.post('/teacher/awards/'+award+'/undo',data={'_csrf':token}).status_code,400)
        second=self.create('demo.other'); other=app.test_client(); self.login(other,'demo.other')
        other_token=self.csrf(other.get('/teacher/awards'))
        self.assertEqual(other.post('/teacher/awards/'+award+'/undo',data={'_csrf':other_token,'confirmed':'yes'}).status_code,404)
        self.clock+=599
        self.assertEqual(self.teacher.post('/teacher/awards/'+award+'/undo',data={'_csrf':token,'confirmed':'yes'}).status_code,303)
        self.assertEqual(len(self.rows()),1)
        self.assertEqual(self.rows()[0]['reversed_by'],'teacher:'+identifier)
        self.assertEqual(self.totals()['rivers'],1284)
        self.assertEqual(self.teacher.get('/api/coins/today').json['total'],0)
        award=self.give()
        self.clock+=600
        self.assertEqual(self.teacher.post('/teacher/awards/'+award+'/undo',data={'_csrf':token,'confirmed':'yes'}).status_code,403)
        self.assertIsNone(self.rows('SELECT * FROM awards WHERE id=?',(award,))[0]['reversed_at'])

    def test_admin_reversal_reason_filters_and_donation(self):
        teacher=self.create(); self.login(); award=self.give(amount='4')
        total=self.board()['donation_total']
        self.admin_login(); self.admin.get('/admin/awards/'+award)
        self.admin_post('/admin/awards/'+award,reason='',confirmed='yes')
        self.assertIsNone(self.rows()[0]['reversed_at'])
        self.admin_post('/admin/awards/'+award,reason='DEMO duplicate physical reward',confirmed='yes')
        row=self.rows()[0]
        self.assertEqual(row['reversed_by'],'administrator')
        self.assertEqual(row['reversal_reason'],'DEMO duplicate physical reward')
        self.assertEqual(self.board()['donation_total'],total-4)
        self.assertEqual(self.teacher.get('/api/coins/week/rivers').json['total'],0)
        self.assertIn('DEMO Student',self.admin.get('/admin/awards?teacher='+teacher+'&house=rivers&date=2026-09-12').get_data(as_text=True))
        self.assertNotIn('DEMO Student',self.admin.get('/admin/awards?house=forests').get_data(as_text=True))
        self.admin_post('/admin/awards/'+award,reason='Try overwrite',confirmed='yes')
        self.assertEqual(self.rows()[0]['reversal_reason'],row['reversal_reason'])

    def test_warsaw_midnight_monday_dst_and_school_year(self):
        with app.app_context():
            for month,day,hours in [(3,29,23),(10,25,25)]:
                stamp=int(datetime(2026,month,day,12,tzinfo=WARSAW).timestamp())
                start,end=bounds('day',stamp)
                self.assertEqual(end-start,hours*3600)
                self.assertEqual(datetime.fromtimestamp(start,WARSAW).hour,0)
            sunday=int(datetime(2026,9,13,23,59,59,tzinfo=WARSAW).timestamp())
            monday=sunday+1
            self.assertNotEqual(bounds('week',sunday),bounds('week',monday))
            self.assertEqual(bounds('week',monday)[0],monday)
            august=int(datetime(2026,8,31,23,59,59,tzinfo=WARSAW).timestamp())
            self.assertEqual(school_year(august),'2025/2026')
            self.assertEqual(school_year(august+1),'2026/2027')
        self.create(); self.login(); self.give()
        self.clock=int(datetime(2026,9,14,0,tzinfo=WARSAW).timestamp())
        self.assertEqual(self.teacher.get('/api/coins/week/rivers').json['total'],0)

    def test_new_year_archives_manual_and_separates_awards(self):
        self.create(); self.login(); self.give(amount='2')
        self.clock=int(datetime(2027,9,1,9,tzinfo=WARSAW).timestamp())
        self.login()
        response=self.teacher.post('/teacher/give',data=self.award_fields())
        self.assertEqual(response.status_code,400)
        self.assertIn('start the current school year',response.get_data(as_text=True))
        self.admin_login(); page=self.admin.get('/admin/coin-settings')
        path=self.root/'data/standings.json'
        version=hashlib.sha256(path.read_bytes()).hexdigest()
        self.admin_post('/admin/coin-settings',action='year',confirmed='yes',_version=version)
        self.assertEqual(len(list((self.root/'manual-history').glob('*.json'))),1)
        self.assertEqual(set(self.totals().values()),{0})
        self.give(amount='3')
        self.assertEqual(self.totals()['rivers'],3)
        self.assertEqual(len(self.rows()),2)
        self.assertEqual(self.teacher.get('/api/coins/today').json['total'],3)

    def test_manual_edit_keeps_digital_separate_and_cli_backup(self):
        self.create(); self.login(); self.give(amount='3')
        self.admin_login(); self.admin.get('/admin/standings/new')
        path=self.root/'data/standings.json'
        self.admin_post('/admin/standings/new',_version=hashlib.sha256(path.read_bytes()).hexdigest(),
                        date='2026-09-12',rivers='10',castles='0',forests='0',bridges='0',confirmed='yes')
        self.assertEqual(self.totals()['rivers'],1297)
        self.assertEqual(json.loads(path.read_text())['snapshots'][-1]['totals']['rivers'],1294)
        backup=self.root/'backup.sqlite3'
        result=app.test_cli_runner().invoke(args=['backup-coins',str(backup)])
        self.assertEqual(result.exit_code,0,result.output)
        self.assertEqual(backup.stat().st_mode & 0o777,0o600)
        import sqlite3
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM awards').fetchone()[0],1)


if __name__=='__main__':
    unittest.main()
