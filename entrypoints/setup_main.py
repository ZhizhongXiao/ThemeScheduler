"""PyInstaller entry point for the windowed single-file Setup program."""

from theme_scheduler.setup_gui import launch_setup

if __name__ == "__main__":
    raise SystemExit(launch_setup())
