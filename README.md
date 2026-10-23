# ワンタイムファイル

10桁の番号とパスワードでテキストを共有するWebアプリ。白と青のスマホ対応デザインです。

## 現在の仕様

- 名前と本文をFernetで暗号化、パスワードをscryptでハッシュ化して保存。
- 自動削除・閲覧期限はありません。作成者が管理URLで削除するまで保存します。
- 作成者専用の管理URLで内容確認・即時削除。管理トークンのDB保存はハッシュのみ。
- 管理URLを知っている人は確認・削除できます。紛失した管理URLは再発行できません。
- IPごとに15分で閲覧試行30回、作成20回まで。DB上で原子的に制限。
- ファイル名100文字、本文20,000文字、パスワード10〜200文字。

## ローカル起動

```sh
cd /workspace/Discord
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/gunicorn 'app:create_app()' --bind 0.0.0.0:3000 --workers 1 --threads 4
```

ローカルではSQLiteと暗号鍵を `data/`（Git対象外）に保存します。以前の期限付きSQLite DBも読み込みできますが、以前の期限は無視して保持します。すでに削除されたデータは復元できません。

テスト: `.venv/bin/python -m pytest -q`。通常は15件実行、使い捨てPostgreSQL向け1件はスキップします。`test_postgres.py`のローカルDBを用意して`TEST_POSTGRES=1`を付けると計16件を実行します。

## Vercel + Neon

VercelのPython FunctionとNeon PostgreSQLに対応。自動削除の定期実行や有料ディスクは不要です。無料利用は各サービスの用途・容量・通信量などの制限内に限ります。料金や利用条件は各サービスの現行プランを確認してください。

1. Neonで無料のPostgreSQLを作成し、プール接続URLを取得。
2. Vercelでこのディレクトリをプロジェクトにする。`vercel.json`にPythonのエントリーポイントを指定済みです。
3. VercelのProductionと必要なPreview環境に次を設定。
   - `DATABASE_URL`: NeonのPostgreSQL接続URL（資格情報を含むため秘密情報として扱う）。
   - `ENCRYPTION_KEY`: Fernet鍵。`.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`を**自分の端末**で実行し、Vercelに直接入力。チャットやGitには載せない。
4. `vercel --prod`またはVercelの画面からデプロイ。
5. 発行されたHTTPS URLでファイル作成→管理→受取人として閲覧→管理画面で削除→閲覧不可を確認。

Vercelではトップページ表示時にDB接続やディスク書き込みを行いません。保存機能は最初のAPIアクセス時に初期化し、環境変数の不足・形式エラー・DB接続障害は秘密情報を含めない503応答で知らせます。トップ画面も `/api/status` の結果を表示します。ローカルファイルへのフォールバックはしません。NeonへのTLS接続はホスト名と同梱CA証明書で検証します。新規DBのテーブル作成は初回APIアクセス時に行い、PostgreSQLのトランザクションロックで同時起動時の競合を防ぎます。

暗号鍵は再デプロイしても同じ値を維持してください。紛失・変更すると保存済みファイルを復号できません。ローカルSQLiteからNeonへのデータ移行は行いません。

## 保護の範囲

サーバーで暗号化するため、エンドツーエンド暗号化ではありません。削除は稼働DBのレコードを削除します。DB事業者のバックアップや、閲覧者のコピー・スクリーンショットまでは消せません。

Vercelではプラットフォームが設定する`x-vercel-forwarded-for`を使用。それ以外では接続元IPを使用します。Vercel以外のプロキシを使う場合は試行制限のIP識別を確認してください。

`Dockerfile`でも起動可能です。SQLite利用時は`DATA_DIR`に永続ディスクを指定してください。
