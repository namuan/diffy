from __future__ import annotations

import sys

from diffy.core.logging import configure_logging, get_logger


logger = get_logger("main")


def main() -> int:
    log_file = configure_logging()
    logger.info("Starting diffy arguments=%s log_file=%s", sys.argv[1:], log_file)
    from diffy.ui.main_window import create_application

    application, window = create_application(sys.argv)
    logger.info("Main window created title=%s", window.windowTitle())
    try:
        exit_code = application.exec()
        logger.info("Application event loop ended exit_code=%d", exit_code)
        return exit_code
    except Exception:
        logger.exception("Application event loop failed")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
