from app import create_app

# Serverless deployments must never fall back to local, ephemeral storage.
app = create_app(require_external=True)
