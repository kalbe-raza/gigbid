import os

os.environ.setdefault("QDRANT_URL", "")
os.environ.setdefault("TRACING_DISABLED", "1")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "test-encryption")
os.environ.setdefault("APPROVAL_SIGNING_KEY", "test-approval-key")
os.environ.setdefault("LLM_CACHE", "0")
os.environ["DATABASE_URL"] = ""  # empty (not unset): .env must not re-inject it
os.environ["REDIS_URL"] = ""
