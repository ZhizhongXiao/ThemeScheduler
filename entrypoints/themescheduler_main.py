"""PyInstaller launcher for the formal installed ThemeScheduler product."""

from theme_scheduler.cli.app import main
from theme_scheduler.windows_identity import (
    configure_current_process_identity,
)

if __name__ == "__main__":
    configure_current_process_identity()
    raise SystemExit(main())
