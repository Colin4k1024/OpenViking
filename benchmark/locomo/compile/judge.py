"""Judge Compile LoCoMo answers with the existing VikingBot judge and prompt."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
LOCOMO_DIR = SCRIPT_DIR.parent
REPO_ROOT = LOCOMO_DIR.parents[1]
VIKINGBOT_DIR = LOCOMO_DIR / "vikingbot"
for path in (REPO_ROOT, VIKINGBOT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from benchmark.locomo.vikingbot.judge import main  # noqa: E402

DEFAULT_INPUT = SCRIPT_DIR / "result" / "locomo_compile_qa_result.csv"


if __name__ == "__main__":
    has_input = any(
        argument == "--input" or argument.startswith("--input=")
        for argument in sys.argv[1:]
    )
    if not has_input and not {"-h", "--help"}.intersection(sys.argv):
        sys.argv.extend(["--input", str(DEFAULT_INPUT)])
    asyncio.run(main())
