"""Optional integration test against a disposable local PostgreSQL, never Neon."""
import os
from concurrent.futures import ThreadPoolExecutor
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
import app as module

@pytest.mark.skipif(os.environ.get('TEST_POSTGRES') != '1', reason='Disposable local PostgreSQL not requested')
def test_postgres_lifecycle(monkeypatch):
    # Local, disposable loopback database with no real data or external network.
    engine = create_engine('postgresql+psycopg://postgres@127.0.0.1:55432/postgres')
    monkeypatch.setenv('DATABASE_URL','postgresql://unused.example/test')
    monkeypatch.setenv('ENCRYPTION_KEY',Fernet.generate_key().decode())
    monkeypatch.setattr(module,'create_engine',lambda *args,**kwargs:engine)
    app = module.create_app()
    assert app.test_client().get('/api/status').json['ready'] is True
    headers={'X-Requested-With':'OneTimeFile'}
    with engine.begin() as db:
        db.execute(text('DELETE FROM files'))
        db.execute(text('DELETE FROM attempts'))
    with app.test_client() as c:
        saved=c.post('/api/files',headers=headers,json=dict(name='銀行',content='秘密の振込先',password='test-password-123')).json
        assert saved['number'].isdigit()
        assert c.post('/api/manage',headers=headers,json={'token':saved['manager']}).json['content']=='秘密の振込先'
    def view(_):
        with app.test_client() as c:
            return c.post('/api/open',headers=headers,json=dict(number=saved['number'],password='test-password-123')).json
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(x['content']=='秘密の振込先' for x in pool.map(view,range(4)))
    with app.test_client() as c:
        assert c.post('/api/manage',headers=headers,json=dict(token=saved['manager'],delete=True)).json=={'deleted':True}
        assert c.post('/api/open',headers=headers,json=dict(number=saved['number'],password='test-password-123')).status_code==404
    with engine.begin() as db:
        assert db.execute(text('SELECT count(*) FROM files')).scalar_one()==0
    engine.dispose()
