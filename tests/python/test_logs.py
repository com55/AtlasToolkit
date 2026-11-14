# SPDX-FileCopyrightText: 2026 com55
# SPDX-License-Identifier: Apache-2.0
import logging
import re
import sys
import threading

import pytest
from atlas_toolkit.app import logs


@pytest.fixture(autouse=True)
def restore_logging():
    root = logging.getLogger()
    level = root.level
    yield
    for handler in root.handlers[:]:
        if getattr(handler, '_atlas_session', False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def test_session_files(tmp_path):
    for i in range(12):
        (tmp_path / f'atlastoolkit-20000101-0000{i:02}-1.log').touch()
    first = logs.start_session_log(False, directory=tmp_path)
    second = logs.start_session_log(False, directory=tmp_path)
    assert first != second
    assert re.fullmatch(r'atlastoolkit-\d{8}-\d{6}-\d+\.log', second.name)
    assert len(list(tmp_path.glob('*.log'))) == 10
    assert second.exists()


def test_cap(tmp_path):
    path = logs.start_session_log(False, directory=tmp_path, max_bytes=1000)
    for _ in range(20):
        logging.info('x' * 300)
    content = path.read_text()
    assert content.count('log size limit reached; further messages dropped') == 1
    logging.info('later record')
    assert path.read_text() == content


@pytest.mark.parametrize('debug', [False, True])
def test_level(tmp_path, debug):
    path = logs.start_session_log(debug, directory=tmp_path)
    logging.debug('debug marker')
    logging.info('info marker')
    text = path.read_text()
    assert ('debug marker' in text) is debug
    assert 'info marker' in text
    assert 'DEV_BUILD=' in text


def test_hooks(tmp_path, monkeypatch):
    path = logs.start_session_log(False, directory=tmp_path)
    seen = []
    monkeypatch.setattr(sys, 'excepthook', lambda *args: seen.append('sys'))
    monkeypatch.setattr(threading, 'excepthook', lambda args: seen.append('thread'))
    logs.install_excepthooks()
    try:
        raise ValueError('trace marker')
    except ValueError:
        args = sys.exc_info()
        sys.excepthook(*args)
        from types import SimpleNamespace
        threading.excepthook(SimpleNamespace(exc_type=args[0], exc_value=args[1], exc_traceback=args[2]))
    assert seen == ['sys', 'thread']
    text = path.read_text()
    assert 'Traceback (most recent call last)' in text
    assert 'ValueError: trace marker' in text


def test_log_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, 'get_config_dir', lambda: tmp_path)
    assert logs.log_dir() == tmp_path / 'logs'
    assert logs.log_dir().is_dir()
