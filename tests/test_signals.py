"""CrawlerSignals 信号系统测试 — 覆盖真实 Signal + StubSignal 双模式。

在 PySide6 可用环境中验证 emit→connect→handler 完整链路。
"""

from __future__ import annotations

import pytest

from astrocrawl.crawler.signals import create_worker_signals


def test_signals_emit_connect_no_error():
    """所有 CrawlerSignals 协议信号可 emit/connect 不抛异常。"""
    signals = create_worker_signals()
    assert signals is not None

    signals.layer_progress.emit(0, 1, 2)
    signals.stats_update.emit(1, 2, 3)
    signals.outcome_update.emit({"ok": 1})
    signals.finished.emit("/tmp/out.jsonl", {"status": "ok"})
    signals.error.emit("test error")
    signals.pause_state.emit(True)
    signals.worker_state.emit(3, "active")

    # connect 返回 Connection 对象
    conn = signals.error.connect(lambda msg: None)
    assert conn is not None
    # StubSignal 无 disconnect, 仅在真实 Signal 上测试
    if hasattr(signals.error, "disconnect"):
        signals.error.disconnect(conn)


def test_signals_unknown_attribute_raises():
    """未知信号名（拼写错误）应抛出 AttributeError，对标精确 Null Object。"""
    signals = create_worker_signals()

    with pytest.raises(AttributeError):
        _ = signals.finshed  # typo for 'finished'

    with pytest.raises(AttributeError):
        _ = signals.layr_progress  # typo for 'layer_progress'


# ═══════════════════════════════════════════════════════════════════════
# StubSignals 完整性
# ═══════════════════════════════════════════════════════════════════════


class TestStubSignals:
    """测试 StubSignals / StubSignal 的 Null Object 行为。"""

    def test_all_signal_attrs_exist(self):
        from astrocrawl.crawler.signals import SIGNAL_NAMES

        signals = create_worker_signals()
        for attr in SIGNAL_NAMES:
            assert hasattr(signals, attr), f"missing signal: {attr}"

    def test_signal_emit_with_valid_args(self):
        """所有信号 emit 接受正确类型参数, connect 后 handler 被调用。"""
        signals = create_worker_signals()
        received = []

        def _capture(*args):
            received.append(args)

        signals.stats_update.connect(_capture)
        signals.finished.connect(_capture)
        signals.worker_state.connect(_capture)

        signals.stats_update.emit(1, 2, 3)
        signals.finished.emit("/tmp/out.jsonl", {"status": "ok"})
        signals.worker_state.emit(0, "working")

        assert len(received) == 3
        assert received[0] == (1, 2, 3)
        assert received[1][0] == "/tmp/out.jsonl"
        assert received[1][1] == {"status": "ok"}
        assert received[2] == (0, "working")

    def test_signal_connect_accepts_callable(self):
        """connect 接受 callable, emit 后 handler 被调用。"""
        signals = create_worker_signals()
        called = []

        def handler(*args):
            called.append(args)

        signals.error.connect(handler)
        signals.error.emit("test")
        assert len(called) == 1
        assert called[0][0] == "test"

    def test_worker_state_signal_exists(self):
        """验证 Bug #disconnect_signals 已修复: worker_state 信号存在并可 emit。"""
        signals = create_worker_signals()
        assert hasattr(signals, "worker_state")
        received = []
        signals.worker_state.connect(lambda *a: received.append(a))
        signals.worker_state.emit(1, "idle")
        assert len(received) == 1


class TestSignalClass:
    """测试信号类的创建和缓存。"""

    def test_singleton_caching(self):
        from astrocrawl.crawler.signals import _get_worker_signals_class

        cls1 = _get_worker_signals_class()
        cls2 = _get_worker_signals_class()
        assert cls1 is cls2
        cls3 = create_worker_signals().__class__
        assert cls1 is cls3

    def test_create_returns_valid_object(self):
        from astrocrawl.crawler.signals import SIGNAL_NAMES

        signals = create_worker_signals()
        assert signals is not None
        for attr in SIGNAL_NAMES:
            assert hasattr(signals, attr)


# ═══════════════════════════════════════════════════════════════════════
# Signal 载荷精确验证
# ═══════════════════════════════════════════════════════════════════════


class TestSignalPayloads:
    """验证每种 signal 的 payload 类型和值正确传递。"""

    def test_stats_update_payload(self):
        """Signal(int,int,int) — completed, total, active 三参数。"""
        signals = create_worker_signals()
        received = []
        signals.stats_update.connect(lambda *args: received.append(args))
        signals.stats_update.emit(10, 100, 5)
        assert received == [(10, 100, 5)]

    def test_finished_payload(self):
        """Signal(str,dict) — output_path + summary dict。"""
        signals = create_worker_signals()
        received = []
        signals.finished.connect(lambda *args: received.append(args))
        signals.finished.emit("/tmp/out.jsonl", {"status": "ok", "urls": 42})
        assert received[0][0] == "/tmp/out.jsonl"
        assert received[0][1] == {"status": "ok", "urls": 42}

    def test_worker_state_payload(self):
        """Signal(int,str) — #disconnect_signals bug 防回归。"""
        signals = create_worker_signals()
        received = []
        signals.worker_state.connect(lambda *args: received.append(args))
        signals.worker_state.emit(3, "active")
        assert received == [(3, "active")]

    def test_pause_state_payload(self):
        signals = create_worker_signals()
        received = []
        signals.pause_state.connect(lambda v: received.append(v))
        signals.pause_state.emit(True)
        assert received == [True]
        signals.pause_state.emit(False)
        assert received == [True, False]

    def test_error_payload(self):
        signals = create_worker_signals()
        received = []
        signals.error.connect(lambda msg: received.append(msg))
        signals.error.emit("connection refused")
        assert received == ["connection refused"]

    def test_outcome_update_payload(self):
        signals = create_worker_signals()
        received = []
        signals.outcome_update.connect(lambda d: received.append(d))
        signals.outcome_update.emit({"ok": 5, "error": 1})
        assert received == [{"ok": 5, "error": 1}]

    def test_layer_progress_payload(self):
        """Signal(int,int,int) — 深度层进度。"""
        signals = create_worker_signals()
        received = []
        signals.layer_progress.connect(lambda *args: received.append(args))
        signals.layer_progress.emit(0, 1, 2)
        assert received == [(0, 1, 2)]

    def test_rule_matched_payload(self):
        """Signal(str, object) — rule_name, trace_info。"""
        signals = create_worker_signals()
        received = []
        signals.rule_matched.connect(lambda *args: received.append(args))
        signals.rule_matched.emit("test_rule", {"info": 1})
        assert received == [("test_rule", {"info": 1})]

    def test_rule_stats_updated_payload(self):
        """Signal(object) — rule stats snapshot。"""
        signals = create_worker_signals()
        received = []
        signals.rule_stats_updated.connect(lambda d: received.append(d))
        signals.rule_stats_updated.emit({"count": 5})
        assert received == [{"count": 5}]


# ═══════════════════════════════════════════════════════════════════════
# 跨模块一致性：信号 SSOT 对所有下游的约束
# ═══════════════════════════════════════════════════════════════════════


def test_forwarded_signals_match_ssot():
    """_FORWARDED must equal SIGNAL_NAMES (as set comparison)."""
    from astrocrawl.crawler.signals import SIGNAL_NAMES
    from astrocrawl.gui.crawl_session import _FORWARDED

    assert set(_FORWARDED) == SIGNAL_NAMES, f"Mismatch: {set(_FORWARDED) ^ SIGNAL_NAMES}"


def test_worker_to_session_keys_match_ssot():
    """_WORKER_TO_SESSION keys must equal SIGNAL_NAMES."""
    from astrocrawl.crawler.signals import SIGNAL_NAMES
    from astrocrawl.gui.crawl_session import _WORKER_TO_SESSION

    assert set(_WORKER_TO_SESSION) == SIGNAL_NAMES, f"Mismatch: {set(_WORKER_TO_SESSION) ^ SIGNAL_NAMES}"
