import logging
from datetime import datetime
from pathlib import Path


def setup_logging() -> None:
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    info_file = logs_dir / f"app_info_{timestamp}.log"
    debug_file = logs_dir / "app_debug_latest.log"

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG)

    info_formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    debug_formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s:%(funcName)s:%(lineno)d] "
        "pid=%(process)d thread=%(threadName)s | %(message)s"
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(info_formatter)

    info_file_handler = logging.FileHandler(info_file, encoding="utf-8")
    info_file_handler.setLevel(logging.INFO)
    info_file_handler.setFormatter(info_formatter)

    debug_file_handler = logging.FileHandler(debug_file, mode="w", encoding="utf-8")
    debug_file_handler.setLevel(logging.DEBUG)
    debug_file_handler.setFormatter(debug_formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(info_file_handler)
    root_logger.addHandler(debug_file_handler)

    # 把 uvicorn 的访问日志从控制台挪到 debug_file，避免管理接口频繁刷屏。
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.setLevel(logging.DEBUG)
    access_logger.propagate = False
    access_logger.addHandler(debug_file_handler)

    logging.getLogger(__name__).info(
        "Logging initialized | info_file=%s | debug_file=%s",
        info_file.as_posix(),
        debug_file.as_posix(),
    )
