"""Write the API's OpenAPI schema to a file, deterministically, for client codegen.

Usage: python -m scripts.export_openapi <output-path>
"""

import json
import sys
from pathlib import Path
from typing import Any

from app.main import app


def render_schema() -> str:
    schema: dict[str, Any] = app.openapi()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m scripts.export_openapi <output-path>")
    Path(sys.argv[1]).write_text(render_schema())


if __name__ == "__main__":
    main()
