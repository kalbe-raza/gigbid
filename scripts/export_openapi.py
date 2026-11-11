"""Export FastAPI's OpenAPI schema for the typed web client: python -m scripts.export_openapi apps/web/openapi.json"""

import json
import sys

from apps.api.main import app

json.dump(app.openapi(), open(sys.argv[1] if len(sys.argv) > 1 else "apps/web/openapi.json", "w"), indent=1)
