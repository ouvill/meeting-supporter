"""アプリケーション共通ロギング設定.

使い方:
    from app.core.logging_setup import setup_logging
    setup_logging(log_dir)          # main.py で一度だけ呼ぶ

各モジュールでは:
    import logging
    logger = logging.getLogger(__name__)
"""

import atexit
import faulthandler
import logging
import logging.handlers
import signal
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s:%(lineno)d — %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


def setup_logging(log_dir: Path, *, debug: bool = False) -> None:
    """ルートロガー・faulthandler・例外フックをまとめて設定する.

    - server.log: ローテーティングファイル (5MB × 3世代) — 常にDEBUGレベル
    - stderr: コンソール — debug=True なら DEBUG、通常は INFO
    - crash.log: faulthandler (セグフォルト/C拡張クラッシュ) + 未捕捉例外
    """
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # ── コンソール (stderr) ─────────────────────────────────────────────────
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.setLevel(logging.DEBUG if debug else logging.INFO)

    # ── ファイル (server.log) ───────────────────────────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "server.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file_handler)

    # uvicorn の独自ハンドラが重複しないよう伝播を止める
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).propagate = False
        logging.getLogger(name).addHandler(console)
        logging.getLogger(name).addHandler(file_handler)

    # ── faulthandler (セグフォルト・ctranslate2 クラッシュ) ────────────────
    crash_log = log_dir / "crash.log"
    _fault_fd = crash_log.open("a")
    faulthandler.enable(file=_fault_fd)

    logger = logging.getLogger(__name__)

    # ── 未捕捉例外フック ────────────────────────────────────────────────────
    _orig_excepthook: Callable[[type[BaseException], BaseException, TracebackType | None], object] = sys.excepthook

    def _excepthook(
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is None or exc_value is None:
            return
        _ = _orig_excepthook(exc_type, exc_value, exc_tb)
        logging.getLogger("uncaught").critical("未捕捉例外", exc_info=exc_value)

    sys.excepthook = _excepthook

    _orig_thread_excepthook: Callable[[threading.ExceptHookArgs], object] = threading.excepthook

    def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
        _ = _orig_thread_excepthook(args)
        logging.getLogger("uncaught.thread").critical(
            "スレッド未捕捉例外 thread=%s", args.thread, exc_info=args.exc_value
        )

    _ = threading.excepthook = _thread_excepthook

    # ── 終了ログ ────────────────────────────────────────────────────────────
    _ = atexit.register(lambda: logger.info("サーバー終了"))

    def _signal_handler(signum: int, _frame: object) -> None:
        logger.info("シグナル受信: %s", signal.Signals(signum).name)
        sys.exit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            _ = signal.signal(sig, _signal_handler)
        except OSError:
            pass

    logger.info("ログ設定完了  server.log=%s  crash.log=%s", log_dir / "server.log", crash_log)
