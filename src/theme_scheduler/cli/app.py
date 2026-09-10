"""Formal installed-product entry-point router.

This module deliberately imports neither the GUI nor pywebview at module load
time.  The Task Scheduler ``auto`` route therefore stays independent from the
on-demand control panel.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence

AutoHandler = Callable[[], int]
NotificationActionHandler = Callable[[str], int]
GuiHandler = Callable[..., int]


def _usage_error(message: str) -> int:
    if sys.stderr is not None:
        print(f"error: {message}", file=sys.stderr)
    return 2


def main(
    argv: Sequence[str] | None = None,
    *,
    auto_handler: AutoHandler | None = None,
    notification_action_handler: NotificationActionHandler | None = None,
    gui_handler: GuiHandler | None = None,
) -> int:
    """Dispatch the stable installed executable contract.

    - no arguments: open the normal installed GUI;
    - ``auto``: perform one scheduled run without importing GUI modules;
    - ``maintenance``: open the same GUI focused on its maintenance section.

    Handler injection is a test seam only; production callers use the delayed
    imports below.
    """

    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) > 2:
        return _usage_error(
            "ThemeScheduler accepts GUI, auto, maintenance, or one "
            "notification-action URI."
        )

    command = arguments[0].casefold() if arguments else "gui"
    if command == "notification-action":
        if len(arguments) != 2:
            return _usage_error("notification-action requires exactly one product URI.")
        if notification_action_handler is None:
            from .notification_action import (
                run_installed_notification_action,
            )

            notification_action_handler = run_installed_notification_action
        return notification_action_handler(arguments[1])

    if len(arguments) > 1:
        return _usage_error("Only notification-action accepts a second argument.")
    if command == "auto":
        if auto_handler is None:
            from .auto import run_installed_auto

            auto_handler = run_installed_auto
        return auto_handler()

    if command in {"gui", "maintenance"}:
        if arguments and command == "gui":
            return _usage_error(
                "Launch the GUI without arguments; 'gui' is not a public command."
            )
        if gui_handler is None:
            from ..gui import launch_gui

            gui_handler = launch_gui
        return gui_handler(
            data_root=None,
            installed=True,
            initial_section=("maintenance" if command == "maintenance" else None),
        )

    return _usage_error(
        "ThemeScheduler accepts no arguments, auto, maintenance, or "
        "notification-action."
    )


if __name__ == "__main__":
    raise SystemExit(main())
