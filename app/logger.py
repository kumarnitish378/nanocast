# Copyright (c) 2026 Nitish NS <nitish.ns378@gmail.com>. All rights reserved.
# Unauthorized copying, modification, or distribution of this file is prohibited.
"""
app/logger.py — Per-run file logger.

Creates logs/<role>_YYYYMMDD_HHMMSS.log for each run.
Use RunLogger.info() to write a line; call .close() on shutdown.
"""
import logging
import os
import time
from datetime import datetime


class RunLogger:
    def __init__(self, role: str):
        os.makedirs('logs', exist_ok=True)
        ts        = datetime.now().strftime('%Y%m%d_%H%M%S')
        self._path = os.path.join('logs', f'{role}_{ts}.log')
        self._t0   = time.time()

        self._log = logging.getLogger(f'run.{role}.{ts}')
        self._log.setLevel(logging.DEBUG)
        self._log.propagate = False

        fh = logging.FileHandler(self._path, encoding='utf-8')
        fh.setFormatter(logging.Formatter(
            '%(asctime)s  %(message)s', datefmt='%H:%M:%S'
        ))
        self._log.addHandler(fh)
        print(f'[LOG] {self._path}')

    def info(self, msg: str) -> None:
        self._log.info(msg)

    def close(self, summary: str = '') -> None:
        elapsed = time.time() - self._t0
        self._log.info(f'[END]  duration={elapsed:.1f}s' +
                       (f'  {summary}' if summary else ''))
        for h in self._log.handlers[:]:
            h.close()
            self._log.removeHandler(h)
