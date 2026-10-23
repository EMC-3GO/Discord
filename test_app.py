import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
import app as module

HEADERS={'X-Requested-With':'OneTimeFile'}

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    clock=[1_800_000_000.0]
    monkeypatch.setattr(module.time,'time',lambda:clock[0])
    path=tmp_path/'test.db'
    app=module.create_app(path)
    return app,app.test_client(),path,clock

def post(client,path,data):
    return client.post(path,json=data,headers=HEADERS)

def create(client):
    r=post(client,'/api/files',dict(name='振込先',password='a-strong-password',content='銀行1234567'))
    assert r.status_code==201
    return r.json

def test_lifecycle(setup):
    app,c,path,clock=setup
    saved=create(c)
    assert len(saved['number'])==10 and saved['number'].isdigit()
    data=post(c,'/api/manage',{'token':saved['manager']}).json
    assert data['content']=='銀行1234567'
    assert post(c,'/api/open',dict(number=saved['number'],password='wrong')).status_code==404
    opened=post(c,'/api/open',dict(number=saved['number'],password='a-strong-password')).json
    assert opened['content']=='銀行1234567'
    assert 'expires' not in opened
    clock[0]+=365*86400
    again=post(c,'/api/open',dict(number=saved['number'],password='a-strong-password')).json
    assert again['content']==opened['content']
    assert post(c,'/api/manage',{'token':saved['manager']}).status_code==200
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM files').fetchone()[0]==1

def test_unopened_and_encryption(setup):
    app,c,path,clock=setup
    saved=create(c)
    with sqlite3.connect(path) as db:
        row=db.execute('SELECT payload,password,manager FROM files').fetchone()
        assert '銀行'.encode() not in row[0]
        assert row[1]!='a-strong-password' and row[2]!=saved['manager']
    clock[0]+=7*86400
    assert post(c,'/api/open',dict(number=saved['number'],password='a-strong-password')).status_code==200

def test_immediate_delete(setup):
    _,c,_,_=setup
    saved=create(c)
    assert post(c,'/api/manage',{'token':'incorrect','delete':True}).status_code==404
    assert post(c,'/api/manage',{'token':saved['manager'],'delete':True}).json=={'deleted':True}
    assert post(c,'/api/open',dict(number=saved['number'],password='a-strong-password')).status_code==404

def test_rate_limit_and_csrf(setup):
    _,c,_,_=setup
    assert c.post('/api/files',json={}).status_code==403
    for _ in range(30):
        assert post(c,'/api/open',dict(number='0000000000',password='wrong')).status_code==404
    assert post(c,'/api/open',dict(number='0000000000',password='wrong')).status_code==429

def test_concurrent_open_returns_content(setup):
    app,c,path,clock=setup
    saved=create(c)
    def open_it(_):
        with app.test_client() as client:
            return post(client,'/api/open',dict(number=saved['number'],password='a-strong-password')).json['content']
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(open_it,range(4)))
    assert set(results)=={'銀行1234567'}

def test_page_and_validation(setup):
    _,c,_,_=setup
    assert 'ワンタイムファイル' in c.get('/').get_data(as_text=True)
    assert c.get('/static/app.js').status_code==200
    assert post(c,'/api/files',dict(name='x',password='short',content='x')).status_code==400


def test_restart_preserves_content(setup):
    _,client,path,clock=setup
    saved=create(client)
    clock[0]+=365*86400
    restarted=module.create_app(path).test_client()
    assert post(restarted,'/api/open',dict(number=saved['number'],password='a-strong-password')).json['content']=='銀行1234567'
    assert post(restarted,'/api/manage',{'token':saved['manager'],'delete':True}).status_code==200
    assert post(client,'/api/open',dict(number=saved['number'],password='a-strong-password')).status_code==404


def test_vercel_requires_persistent_configuration(monkeypatch):
    monkeypatch.setenv('VERCEL','1')
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.delenv('ENCRYPTION_KEY',raising=False)
    with pytest.raises(RuntimeError,match='requires DATABASE_URL'):
        module.create_app()


def test_malformed_json(setup):
    _,client,_,_=setup
    assert post(client,'/api/open',[]).status_code==400
