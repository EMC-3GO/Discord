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
    client=module.create_app().test_client()
    assert client.get('/').status_code==200
    assert client.get('/api/status').json['code']=='DATABASE_URL_MISSING'
    assert client.get('/api/status').status_code==503


def test_malformed_json(setup):
    _,client,_,_=setup
    assert post(client,'/api/open',[]).status_code==400


def test_serverless_initialization_uses_packaged_trust_store(tmp_path,monkeypatch):
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine
    import certifi
    from pathlib import Path
    captured={}
    local=create_engine('sqlite:///' + str(tmp_path/'serverless.db'))
    def engine_for_test(url,**kwargs):
        captured.update(kwargs)
        return local
    monkeypatch.setattr(module,'create_engine',engine_for_test)
    monkeypatch.setenv('VERCEL','1')
    monkeypatch.setenv('DATABASE_URL','postgresql://example.invalid/database')
    monkeypatch.setenv('ENCRYPTION_KEY',Fernet.generate_key().decode())
    monkeypatch.setenv('DATA_DIR',str(tmp_path/'must-not-write'))
    app=module.create_app()
    assert app.test_client().get('/').status_code==200
    assert captured=={}
    assert app.test_client().get('/api/status').json['ready'] is True
    assert not (tmp_path/'must-not-write').exists()
    assert captured['connect_args']['sslmode']=='verify-full'
    assert captured['connect_args']['sslrootcert']==certifi.where()
    assert Path(certifi.where()).is_file()


@pytest.mark.parametrize('configuration,code',[
    ({},'DATABASE_URL_MISSING'),
    ({'DATABASE_URL':'postgresql://example.invalid/db'},'ENCRYPTION_KEY_MISSING'),
    ({'DATABASE_URL':'postgresql://example.invalid/db','ENCRYPTION_KEY':'not-a-valid-key'},'ENCRYPTION_KEY_INVALID'),
])
def test_serverless_misconfiguration_preserves_page(monkeypatch,tmp_path,configuration,code):
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.delenv('ENCRYPTION_KEY',raising=False)
    monkeypatch.delenv('VERCEL',raising=False)
    monkeypatch.setenv('DATA_DIR',str(tmp_path/'read-only-storage'))
    for name,value in configuration.items():
        monkeypatch.setenv(name,value)
    client=module.create_app(require_external=True).test_client()
    assert client.get('/').status_code==200
    assert client.get('/static/app.js').status_code==200
    result=client.get('/api/status')
    assert result.status_code==503 and result.json['code']==code
    assert not (tmp_path/'read-only-storage').exists()
    for value in configuration.values():
        assert value not in result.get_data(as_text=True)


def test_database_failure_can_recover_without_restart(monkeypatch,tmp_path,caplog):
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError
    monkeypatch.setenv('DATABASE_URL','postgresql://username:private-test-password@example.invalid/db')
    monkeypatch.setenv('ENCRYPTION_KEY',Fernet.generate_key().decode())
    local=create_engine('sqlite:///'+str(tmp_path/'recover.db'))
    attempts=[0]
    def flaky_engine(*args,**kwargs):
        attempts[0]+=1
        if attempts[0]==1:
            raise OperationalError('private-test-password',{},Exception('private-test-password'))
        return local
    monkeypatch.setattr(module,'create_engine',flaky_engine)
    client=module.create_app(require_external=True).test_client()
    assert client.get('/').status_code==200
    failure=client.get('/api/status')
    assert failure.status_code==503
    assert failure.json['code']=='DATABASE_UNAVAILABLE'
    assert 'private-test-password' not in failure.get_data(as_text=True)
    assert 'private-test-password' not in caplog.text
    assert client.get('/api/status').json['ready'] is True
    saved=create(client)
    assert post(client,'/api/manage',{'token':saved['manager'],'delete':True}).json['deleted'] is True


def test_actual_vercel_entrypoint_without_configuration(monkeypatch):
    import importlib.util
    from pathlib import Path
    monkeypatch.delenv('DATABASE_URL',raising=False)
    monkeypatch.delenv('ENCRYPTION_KEY',raising=False)
    spec=importlib.util.spec_from_file_location('serverless_entry_test',Path(__file__).parent/'api/index.py')
    entry=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    assert entry.app.test_client().get('/').status_code==200
    assert entry.app.test_client().get('/api/status').json['code']=='DATABASE_URL_MISSING'
