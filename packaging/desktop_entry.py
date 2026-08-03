import sys
import traceback
from pathlib import Path

from stock_harness.desktop import main


try:
    exit_code = main()
except Exception:
    log_path = Path(sys.executable).resolve().parent / "StockHarness.error.log"
    log_path.write_text(traceback.format_exc(), encoding="utf-8")
    raise
raise SystemExit(exit_code)
