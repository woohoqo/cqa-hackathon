"""Test-only runner for mocked async tests when Windows socketpair is blocked.

Uses Python's real task/timer/cancellation scheduler, but no I/O registration is
permitted. NOT a production event-loop or a real-network compatibility test.
"""
import asyncio
from pathlib import Path
import selectors
import sys
import time

import pytest

sys.path.insert(0, str(Path.cwd()))


class NoIOSelector(selectors.BaseSelector):
    def register(self, *args, **kwargs):
        raise RuntimeError('The offline test harness forbids all I/O registrations')

    def unregister(self, *args, **kwargs):
        raise RuntimeError('No I/O registered in offline test harness')

    def select(self, timeout=None):
        if timeout is None:
            raise RuntimeError('Test has no scheduled progress; no network I/O is available')
        if timeout:
            time.sleep(timeout)
        return []

    def get_map(self):
        return {}


class MockOnlyLoop(asyncio.SelectorEventLoop):
    def __init__(self):
        super().__init__(selector=NoIOSelector())

    def _make_self_pipe(self):
        pass

    def _close_self_pipe(self):
        pass

    def _write_to_self(self):
        pass


class MockOnlyPolicy(asyncio.DefaultEventLoopPolicy):
    def new_event_loop(self):
        return MockOnlyLoop()


asyncio.set_event_loop_policy(MockOnlyPolicy())
print('TEST ONLY: native asyncio scheduler; network/file-descriptor I/O forbidden', flush=True)
sys.exit(pytest.main(sys.argv[1:] or ["backend/app/ai/tests", "-q"]))
