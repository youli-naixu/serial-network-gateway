import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if len(sys.argv) == 4 and sys.argv[1] == "--vcom-apply-job":
    from serial_gateway.vcom import main as vcom_main

    raise SystemExit(vcom_main(sys.argv[1:]))

from serial_gateway.cli import main

raise SystemExit(main())
