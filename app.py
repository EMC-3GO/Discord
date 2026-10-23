import hashlib
import json
import os
from pathlib import Path
import secrets
import time

import certifi
from cryptography.fernet import Fernet
from flask import Flask, jsonify, request
from sqlalchemy import (Column, Float, Integer, LargeBinary, MetaData, String,
                        Table, create_engine, event, text)
from sqlalchemy.pool import NullPool
from werkzeug.security import generate_password_hash, check_password_hash


def create_app(database=None):
    app = Flask(__name__, static_folder='static')
    app.config['MAX_CONTENT_LENGTH'] = 100_000
    remote_url = os.environ.get('DATABASE_URL') if database is None else None
    key = os.environ.get('ENCRYPTION_KEY')
    if os.environ.get('VERCEL') and (not remote_url or not key):
        raise RuntimeError('Vercel requires DATABASE_URL and ENCRYPTION_KEY.')
    if remote_url and not key:
        raise RuntimeError('External database requires a persistent ENCRYPTION_KEY.')
    folder = Path(os.environ.get('DATA_DIR', Path(__file__).parent / 'data'))
    if not key:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        keyfile = folder / 'encryption.key'
        try:
            fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as f:
                f.write(Fernet.generate_key())
        key = keyfile.read_bytes()
    cipher = Fernet(key)
    if remote_url:
        if not remote_url.startswith(('postgresql://', 'postgres://', 'postgresql+psycopg://')):
            raise RuntimeError('DATABASE_URL must use PostgreSQL.')
        url = remote_url.replace('postgres://', 'postgresql+psycopg://', 1).replace('postgresql://', 'postgresql+psycopg://', 1)
        # Neon certificates are validated against the system trust store.
        engine = create_engine(url, poolclass=NullPool, hide_parameters=True,
                               connect_args={'sslmode': 'verify-full', 'sslrootcert': certifi.where(), 'connect_timeout': 10})
    else:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        engine = create_engine('sqlite:///' + str(database or folder / 'files.sqlite3'),
                               connect_args={'timeout': 15}, hide_parameters=True)
        @event.listens_for(engine, 'connect')
        def secure_delete(connection, _):
            connection.execute('PRAGMA secure_delete=ON')
    metadata = MetaData()
    Table('files', metadata,
          Column('number', String, primary_key=True), Column('payload', LargeBinary, nullable=False),
          Column('password', String, nullable=False), Column('manager', String, unique=True, nullable=False),
          Column('created', Float, nullable=False),
          # Kept for compatibility with earlier SQLite databases; never used for expiry.
          Column('expires', Float, nullable=False), Column('opened', Float))
    Table('attempts', metadata, Column('key', String, primary_key=True),
          Column('count', Integer), Column('until', Float))
    metadata.create_all(engine)
    app.db_engine = engine

    @app.before_request
    def protect():
        if request.method == 'POST':
            if request.headers.get('X-Requested-With') != 'OneTimeFile':
                return jsonify(error='ページを再読み込みしてください。'), 403
            if not isinstance(request.get_json(silent=True), dict):
                return jsonify(error='入力内容を確認してください。'), 400

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    def limited(db, prefix, maximum):
        now = time.time()
        # Vercel overwrites x-vercel-forwarded-for. Never trust generic forwarded headers.
        address = request.headers.get('x-vercel-forwarded-for', request.remote_addr) if os.environ.get('VERCEL') else request.remote_addr
        address = str(address).split(',')[0].strip()
        identity = fingerprint(prefix + ':' + address)
        db.execute(text('DELETE FROM attempts WHERE until<=:now'), {'now': now})
        count = db.execute(text('''INSERT INTO attempts (key,count,until) VALUES (:key,1,:until)
            ON CONFLICT (key) DO UPDATE SET count=attempts.count+1
            RETURNING count'''), {'key': identity, 'until': now+900}).scalar_one()
        return count > maximum

    def fingerprint(value):
        return hashlib.sha256(value.encode()).hexdigest()

    @app.get('/')
    def home():
        return app.send_static_file('index.html')

    @app.post('/api/files')
    def create():
        body = request.get_json()
        name, password, content = (body.get(x) for x in ('name', 'password', 'content'))
        if not all(isinstance(x, str) for x in (name, password, content)):
            return jsonify(error='入力内容を確認してください。'), 400
        if not 1 <= len(name.strip()) <= 100 or not 10 <= len(password) <= 200 or not 1 <= len(content.strip()) <= 20000:
            return jsonify(error='名前は100文字以内、パスワードは10〜200文字、本文は20,000文字以内で入力してください。'), 400
        with engine.begin() as db:
            if limited(db, 'create', 20):
                return jsonify(error='作成回数の上限です。15分後にお試しください。'), 429
        token = secrets.token_urlsafe(32)
        payload = cipher.encrypt(json.dumps(dict(name=name.strip(), content=content), ensure_ascii=False).encode())
        hashed = generate_password_hash(password)
        with engine.begin() as db:
            for _ in range(20):
                number = f'{secrets.randbelow(10**10):010d}'
                result = db.execute(text('''INSERT INTO files (number,payload,password,manager,created,expires)
                    VALUES (:number,:payload,:password,:manager,:created,0)
                    ON CONFLICT (number) DO NOTHING RETURNING number'''),
                    dict(number=number, payload=payload, password=hashed, manager=fingerprint(token), created=time.time())).scalar_one_or_none()
                if result is not None:
                    break
            else:
                return jsonify(error='番号を発行できませんでした。'), 503
        return jsonify(number=number, manager=token), 201

    @app.post('/api/open')
    def open_file():
        body = request.get_json()
        number, password = body.get('number'), body.get('password')
        if not isinstance(number, str) or not isinstance(password, str) or len(password)>200:
            return jsonify(error='番号とパスワードを確認してください。'), 400
        with engine.begin() as db:
            if limited(db, 'open', 30):
                return jsonify(error='試行回数の上限です。15分後にお試しください。'), 429
            row = db.execute(text('SELECT * FROM files WHERE number=:number'), {'number': number}).mappings().first()
        if not row or not check_password_hash(row['password'],password):
            return jsonify(error='番号・パスワードが違うか、ファイルが削除されています。'), 404
        return jsonify(**json.loads(cipher.decrypt(bytes(row['payload']))))

    @app.post('/api/manage')
    def manage():
        body = request.get_json()
        token = body.get('token')
        if not isinstance(token,str) or len(token)>100:
            return jsonify(error='管理URLが無効です。'), 404
        with engine.begin() as db:
            row = db.execute(text('SELECT * FROM files WHERE manager=:manager'), {'manager': fingerprint(token)}).mappings().first()
            if not row:
                return jsonify(error='ファイルは削除済みか、管理URLが無効です。'), 404
            if body.get('delete') is True:
                db.execute(text('DELETE FROM files WHERE number=:number'), {'number': row['number']})
                return jsonify(deleted=True)
            return jsonify(**json.loads(cipher.decrypt(bytes(row['payload']))), number=row['number'])

    return app


if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=int(os.environ.get('PORT', 3000)), debug=False)
