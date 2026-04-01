import os

# Force demo-key before app modules are imported so Settings.from_env()
# picks it up regardless of what REQLY_INGEST_KEY is set to in CI.
os.environ["REQLY_INGEST_KEY"] = "demo-key"
