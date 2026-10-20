import os, tempfile, time
os.environ['DATABASE_URL']='sqlite:///'+tempfile.mktemp(suffix='.db')
os.environ['JWT_SECRET']='test-only-secret-that-is-at-least-32-characters'
os.environ['TEACHER_CODE']='test-teacher-code'
import main as m
from fastapi.testclient import TestClient
c=TestClient(m.app)

def signup(phone, role='student'):
    r=c.post('/api/auth/register',json={'phone':phone,'username':'Test user','password':'original-password','role':role,'teacher_code':'test-teacher-code'})
    assert r.status_code==200,r.text
    return {'Authorization':'Bearer '+r.json()['token']}

def test_reset_workflow():
    teacher=signup('08000000001','teacher'); student=signup('08000000002')
    reset={'phone':'08000000002','password':'temporary-password'}
    assert c.post('/api/teacher/students/reset',headers=student,json=reset).status_code==403
    assert c.post('/api/teacher/students/reset',headers=teacher,json=reset).status_code==200
    assert c.get('/api/tests',headers=student).status_code==401
    r=c.post('/api/auth/login',json={'phone':reset['phone'],'password':reset['password']})
    assert r.status_code==200 and r.json()['user']['must_change_password']
    temporary={'Authorization':'Bearer '+r.json()['token']}
    assert c.get('/api/tests',headers=temporary).status_code==403
    r=c.post('/api/auth/change-password',headers=temporary,json={'current_password':reset['password'],'new_password':'new-personal-password'})
    assert r.status_code==200 and not r.json()['user']['must_change_password']
    assert c.get('/api/tests',headers=temporary).status_code==401
    assert c.get('/api/tests',headers={'Authorization':'Bearer '+r.json()['token']}).status_code==200
    with m.Session() as s:
        assert s.query(m.AuthAudit).count()==2
    c.post('/api/teacher/students/reset',headers=teacher,json=reset)
    with m.Session.begin() as s:
        u=s.query(m.User).filter_by(phone=reset['phone']).one();s.get(m.AuthState,u.id).reset_expires=time.time()-1
    assert c.post('/api/auth/login',json={'phone':reset['phone'],'password':reset['password']}).status_code==401

def test_publish_gate_and_scoring(monkeypatch):
    teacher=signup('08000000003','teacher'); student=signup('08000000004')
    monkeypatch.setattr(m,'ai_extract',lambda pdf:[{'q':'2 + 2?','o':['3','4'],'a':1,'review':True}])
    r=c.post('/api/teacher/tests/upload',headers=teacher,data={'title':'Math'},files={'file':('test.pdf',b'%PDF mock','application/pdf')})
    assert r.status_code==200 and not r.json()['published']
    tid=r.json()['id']
    assert c.post(f'/api/teacher/tests/{tid}/toggle',headers=teacher).status_code==409
    q=c.get(f'/api/teacher/tests/{tid}',headers=teacher).json()[0]
    assert c.put(f'/api/teacher/tests/{tid}/questions/{q["id"]}',headers=teacher,json={'a':1}).status_code==200
    assert c.post(f'/api/teacher/tests/{tid}/toggle',headers=teacher).status_code==200
    r=c.post(f'/api/tests/{tid}/start',headers=student);assert r.status_code==200
    assert 'a' not in r.json()['questions'][0]
    aid=r.json()['id']
    assert c.post(f'/api/attempts/{aid}/submit',headers=student,json={'answers':{'0':1}}).json()['total']==100
    assert c.post(f'/api/attempts/{aid}/submit',headers=student,json={'answers':{'0':0}}).json()['total']==100

def test_aloc_contract(monkeypatch):
    row={'id':'q1','text':'2 + 2?','options':{'A':'3','B':'4'},'correctAnswer':'B'}
    assert m.norm(row)['a']==1
    assert m.norm({**row,'correctAnswer':'E'}) is None
    def get(url,**kw):
        assert url.endswith('/api/v1/questions')
        assert kw['headers']['X-API-Key']=='test-key'
        assert kw['params']['examType']=='jamb'
        return m.httpx.Response(200,json={'data':[row]})
    monkeypatch.setattr(m,'ALOC_KEY','test-key');monkeypatch.setattr(m.httpx,'get',get)
    assert m.fetch_aloc('english',1)[0][0]['a']==1
