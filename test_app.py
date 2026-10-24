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
    monkeypatch.setattr(m,'extract_objectives',lambda pdf:[{'q':'2 + 2?','o':['3','4'],'a':1,'review':True}])
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

def test_real_pdf_and_missing_key():
    import io
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    def pdf(lines):
        w=PdfWriter(); page=w.add_blank_page(width=600,height=800)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):w._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 40 750 Td 18 TL '+ ' '.join('('+s+') Tj T*' for s in lines)+' ET').encode())
        page[NameObject('/Contents')]=w._add_object(stream)
        buf=io.BytesIO();w.write(buf);return buf.getvalue()
    lines=['1. What is 2 + 2?','A. 3','B. 4','C. 5','D. 6']
    result=m.extract_objectives(pdf(lines+['Answer key','1. B']))
    assert result[0]['a']==1 and result[0]['o']==['3','4','5','6']
    import pytest
    with pytest.raises(m.HTTPException) as err:m.extract_objectives(pdf(lines))
    assert err.value.status_code==422


def test_provider_error_explains_rejection_without_exposing_token(monkeypatch):
    import pytest
    monkeypatch.setattr(m, 'ALOC_KEY', 'aloc_test_secret')
    monkeypatch.setattr(m.httpx, 'get', lambda *a, **k: m.httpx.Response(400, json={
        'message': 'Invalid subject. aloc_test_secret https://example.com/?key=secret'}))
    with pytest.raises(m.HTTPException) as err:
        m.fetch_aloc('english')
    assert 'HTTP 400 for english' in err.value.detail
    assert 'Invalid subject' in err.value.detail
    assert 'aloc_test_secret' not in err.value.detail
    assert 'key=secret' not in err.value.detail


def test_aloc_pagination_and_shared_bank(monkeypatch):
    calls = []
    def get(url, **kw):
        params = kw['params']; calls.append(params)
        assert 1 <= params['limit'] <= 15
        start = int(params.get('cursor', '0'))
        end = min(start + params['limit'], 60)
        rows = [{'id': str(i), 'text': f'Question {i}', 'options': {'a':'yes','b':'no'}, 'correctAnswer':'a'} for i in range(start,end)]
        return m.httpx.Response(200,json={'data':rows,'pagination':{'hasMore':end<60,'nextCursor':str(end)}})
    monkeypatch.setattr(m,'ALOC_KEY','test-key')
    monkeypatch.setattr(m.httpx,'get',get)
    with m.Session.begin() as db:
        db.query(m.QuestionBank).delete(); db.query(m.BankProgress).delete()
    assert len(m.get_q('english',60)) == 60
    assert len(calls) == 4
    assert len(m.get_q('english',60)) == 60
    assert len(calls) == 4


def test_aloc_comprehension_section():
    q=m.norm({'id':'p1','text':'What happened?', 'options':{'a':'A','b':'B'},'correctAnswer':'a','hasPassage':True,'section':'The passage.'})
    assert q['q'].startswith('The passage.')


def test_unseen_first_cursor_persistence_and_repeat_fallback(monkeypatch):
    calls=[]
    def page(slug, n, cursor=None):
        start=int(cursor or 0); end=min(start+n,150); calls.append(start)
        return ([{'id':str(i),'q':f'Variety {i}','o':['yes','no'],'a':0} for i in range(start,end)],
                {'nextCursor':str(end),'hasMore':end<150})
    monkeypatch.setattr(m,'_aloc_page',page)
    with m.Session.begin() as db:
        db.query(m.QuestionBank).delete(); db.query(m.BankProgress).delete()
    first=m.get_q('biology',60)
    seen={m.question_key(q):(1,1) for q in first}
    second=m.get_q('biology',60,seen)
    assert not set(map(m.question_key,first)) & set(map(m.question_key,second))
    assert calls == list(range(0,135,15))
    # Independent DB sessions retain the bank and paging cursor.
    with m.Session() as db:
        assert db.get(m.BankProgress,'biology').cursor=='135'
    seen.update({m.question_key(q):(1,2) for q in second})
    third=m.get_q('biology',60,seen)
    assert len({m.question_key(q) for q in third})==60
    assert sum(q['repeated'] for q in third)==30
    # Repeats come from the older sitting when usage counts match.
    assert {m.question_key(q) for q in third if q['repeated']} <= set(map(m.question_key,first))
    before=len(calls);m.get_q('biology',60,{})
    assert len(calls)==before


def test_provider_failure_uses_saved_bank_without_repeated_calls(monkeypatch):
    with m.Session.begin() as db:
        db.query(m.QuestionBank).delete();db.query(m.BankProgress).delete()
        for i in range(60):
            q={'id':str(i),'q':f'Cached {i}','o':['yes','no'],'a':0}
            db.add(m.QuestionBank(subject='physics',fingerprint=m.question_key(q),data=q))
    calls=[]
    def unavailable(*args):
        calls.append(1);raise m.HTTPException(502,'Question provider returned HTTP 429.')
    monkeypatch.setattr(m,'_aloc_page',unavailable)
    assert len(m.get_q('physics',60))==60
    assert len(m.get_q('physics',60))==60
    assert len(calls)==1


def test_history_is_per_student_and_answers_stay_private():
    teacher=signup('08000000008'); other=signup('08000000009')
    with m.Session.begin() as db:
        u=db.query(m.User).filter_by(phone='08000000008').one()
        v=db.query(m.User).filter_by(phone='08000000009').one()
        q={'s':'Physics','q':'History question','o':['x','y'],'a':0,'repeated':True}
        a=m.Attempt(user_id=u.id,kind='aloc',qdata=[q],answers={},deadline=time.time()+100,submitted=True)
        db.add(a);db.flush()
        assert m.question_history(db,u.id)['physics'][m.question_key(q)][0]==1
        assert not m.question_history(db,v.id)
        assert m.payload(a)['repeated_questions']==1
        assert 'a' not in m.payload(a)['questions'][0]
