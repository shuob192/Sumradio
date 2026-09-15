"""Generate stable, reviewable API and extraction contracts without starting devices."""

import json
from pathlib import Path

from sumradio.app import create_app
from sumradio.extraction import structured_schema

root = Path(__file__).resolve().parent.parent
(root / "schemas").mkdir(exist_ok=True)
for name, schema in [
    ("openapi.json", create_app().openapi()),
    ("extraction.json", structured_schema()),
]:
    (root / "schemas" / name).write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n")
