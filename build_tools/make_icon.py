"""
make_icon.py
============
互換用（r146）。app.ico は採用したデザインの原画から作る。
実体はリポジトリ直下の generate_icon.py。

使い方:
    python build_tools/make_icon.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generate_icon import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
