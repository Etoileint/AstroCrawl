"""代理 Profile 管理页 — _ProxyProfilePage + ProxyProfileListModel + ProxyProfileEditDialog（ADR-0010 Phase 3.5a）。

Profile 编辑对话框含端点子表（5 列）+ bypass 域名表（1 列），对标 _AIProfilePage 架构。
"""

from __future__ import annotations

import asyncio
from typing import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from astrocrawl._constants import QLINEEDIT_MAX
from astrocrawl.gui._animated_bar import _ProgressStatusBar
from astrocrawl.gui._delegates import StatusColorDelegate
from astrocrawl.gui._proxy_endpoint_dialog import ProxyEndpointEditDialog
from astrocrawl.gui._style import ColumnDef
from astrocrawl.gui._table_page import _TableManagementPage
from astrocrawl.gui._tokens import FONT_MD, SPACE_MD, SPACE_SM
from astrocrawl.gui.theme import get_theme_manager
from astrocrawl.proxy._config import (
    ParsedProxy,
    ProxyAuth,
    ProxyEndpointSpec,
    ProxyProfile,
    ProxyType,
    endpoint_display,
    endpoint_key,
    find_duplicate_endpoint,
)
from astrocrawl.proxy._consumers import PROXY_CONSUMERS
from astrocrawl.proxy._probe import probe_one
from astrocrawl.utils.preferences import Preferences

# ═══════════════════════════════════════════════════════════════════════════
# ProxyProfileListModel
# ═══════════════════════════════════════════════════════════════════════════


class ProxyProfileListModel(QAbstractTableModel):
    """代理 Profile 列表 Model — 4 列：名称 / 端点 / 测试状态 / 使用中。

    UserRole 在 Status 列存储状态 key（all_reachable/partial/untested）。
    """

    _COLUMNS: list[ColumnDef] = [
        ColumnDef(key="name", label="Name"),
        ColumnDef(key="endpoints", label="Endpoints"),
        ColumnDef(key="status", label="Status", resize="fixed", width=100),
        ColumnDef(key="consumers", label="In Use"),
    ]

    def __init__(self, prefs: Preferences, parent=None):
        super().__init__(parent)
        self._prefs = prefs
        self._profiles: list[ProxyProfile] = []
        self._test_status: dict[str, str] = {}  # profile_name -> status key
        self._unreachable_counts: dict[str, int] = {}  # profile_name -> count
        self._probe_accum: dict[str, tuple[int, int]] = {}  # profile -> (reachable, unreachable)
        self.load()

    def load(self) -> None:
        self.beginResetModel()
        self._profiles = self._prefs.get_proxy_profiles()
        # 清理已删除 profile 的测试状态
        existing = {p.name for p in self._profiles}
        self._test_status = {k: v for k, v in self._test_status.items() if k in existing}
        self._unreachable_counts = {k: v for k, v in self._unreachable_counts.items() if k in existing}
        self.endResetModel()

    def set_test_result(self, profile_name: str, reachable: int, unreachable: int) -> None:
        """更新单个 profile 的测试结果。"""
        if unreachable == 0:
            self._test_status[profile_name] = "all_reachable"
        else:
            self._test_status[profile_name] = "partial"
        self._unreachable_counts[profile_name] = unreachable
        # emit dataChanged for the row
        for row, p in enumerate(self._profiles):
            if p.name == profile_name:
                idx = self.index(row, 2)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole])
                break

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._profiles)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._COLUMNS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        profile = self._profiles[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return profile.name
            elif col == 1:
                count = len(profile.proxies)
                return self.tr("{0} endpoints").format(count)
            elif col == 2:
                return self._status_display(profile.name)
            elif col == 3:
                return self._consumers_display(profile.uuid)
            return None

        if role == Qt.ItemDataRole.UserRole and col == 2:
            return self._test_status.get(profile.name, "untested")

        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return [self.tr("Name"), self.tr("Endpoints"), self.tr("Status"), self.tr("In Use")][section]
        return None

    def _status_display(self, name: str) -> str:
        status = self._test_status.get(name, "untested")
        if status == "all_reachable":
            return str(self.tr("All Reachable"))
        if status == "partial":
            count = self._unreachable_counts.get(name, 0)
            return str(self.tr("{0} Unreachable").format(count))
        return str(self.tr("Untested"))

    def _consumers_display(self, profile_uuid: str) -> str:
        consumers: list[str] = []
        for key, display in PROXY_CONSUMERS.items():
            entry = self._prefs.get_proxy_last_used(key)
            if entry and entry.get("profile") == profile_uuid:
                consumers.append(self.tr(display))
        return ", ".join(consumers) if consumers else self.tr("Unused")

    def get_profile(self, row: int) -> ProxyProfile | None:
        if 0 <= row < len(self._profiles):
            return self._profiles[row]
        return None

    @property
    def profiles(self) -> list[ProxyProfile]:
        return list(self._profiles)


# ═══════════════════════════════════════════════════════════════════════════
# _BypassDomainDialog
# ═══════════════════════════════════════════════════════════════════════════


class _BypassDomainDialog(QDialog):
    """单字段域名输入对话框 — 替代 QInputDialog.getText()。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModal)
        self.setWindowTitle(self.tr("Add Bypass Domain"))

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_MD)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        form.setSpacing(SPACE_SM)
        self._domain_edit = QLineEdit()
        self._domain_edit.setMaxLength(QLINEEDIT_MAX)
        self._domain_edit.setPlaceholderText(self.tr("e.g. example.com"))
        form.addRow(self.tr("Domain:"), self._domain_edit)
        layout.addLayout(form)

        self._error_label = QLabel("")
        self._error_label.setStyleSheet(f"color: {get_theme_manager().get('danger')}; font-size: {FONT_MD}px;")
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(SPACE_SM)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        self._ok_btn = QPushButton(self.tr("OK"))
        self._ok_btn.clicked.connect(self._validate_and_accept)
        self._ok_btn.setDefault(True)
        btn_row.addWidget(cancel_btn, 1)
        btn_row.addWidget(self._ok_btn, 1)
        layout.addLayout(btn_row)

        self.adjustSize()
        ideal_h = self.height()
        self.setMaximumWidth(self.width())
        screen = self.screen()
        if screen:
            max_h = int(screen.availableGeometry().height() * 0.85)
            self.setMaximumHeight(min(ideal_h, max_h))
            self.setMinimumHeight(min(ideal_h, max_h))
        else:
            self.setMaximumHeight(ideal_h)
            self.setMinimumHeight(ideal_h)
        self.setMinimumWidth(self.width())

    def get_domain(self) -> str:
        return self._domain_edit.text().strip()  # type: ignore[no-any-return]  # PySide6 stubs return Any

    def _validate(self) -> bool:
        domain = self._domain_edit.text().strip()
        if not domain:
            self._error_label.setText(self.tr("Domain cannot be empty"))
            return False
        self._error_label.setText("")
        return True

    def _validate_and_accept(self) -> None:
        if self._validate():
            self.accept()


# ═══════════════════════════════════════════════════════════════════════════
# ProxyProfileEditDialog
# ═══════════════════════════════════════════════════════════════════════════


class ProxyProfileEditDialog(QDialog):
    """代理 Profile 编辑对话框 — 端点子表 + bypass 域名表。

    端点子表 5 列：标签 / 类型 / 主机 / 端口 / 权重
    Bypass 域名表 1 列：域名
    """

    def __init__(
        self,
        parent: QWidget | None,
        existing_names: list[str],
        profile: ProxyProfile | None = None,
    ):
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModal)
        self._existing_names = [n for n in existing_names if n != (profile.name if profile else None)]
        self._profile = profile
        self._is_new = profile is None
        self._dirty = False
        self._probe_worker: _ProbeWorker | None = None

        # 内存中的端点列表（ProxyEndpointSpec）
        if profile is not None:
            self._endpoints: list[ProxyEndpointSpec] = list(profile.proxies)
            self._bypass_domains: list[str] = list(profile.bypass_domains)
        else:
            self._endpoints = []
            self._bypass_domains = []

        title = self.tr("Add Proxy Profile") if self._is_new else self.tr("Edit Proxy Profile")
        self.setWindowTitle(title)

        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_MD)

        # ── 名称 ──
        name_layout = QHBoxLayout()
        name_layout.setSpacing(SPACE_SM)
        name_label = QLabel(self.tr("Name"))
        self._name_edit = QLineEdit()
        self._name_edit.setMaxLength(QLINEEDIT_MAX)
        self._name_edit.setPlaceholderText(self.tr("Enter Profile name"))
        if not self._is_new and profile is not None:
            self._name_edit.setText(profile.name)
        self._name_edit.textChanged.connect(self._mark_dirty)
        name_layout.addWidget(name_label, 1)
        name_layout.addWidget(self._name_edit, 3)
        layout.addLayout(name_layout)

        self._name_error = QLabel("")
        self._name_error.setStyleSheet(f"color: {get_theme_manager().get('danger')}; font-size: {FONT_MD}px;")
        self._name_error.hide()
        layout.addWidget(self._name_error)

        # ── 端点子表 ──
        endpoints_group = QGroupBox(self.tr("Endpoints"))
        endpoints_layout = QVBoxLayout(endpoints_group)
        endpoints_layout.setSpacing(SPACE_SM)

        self._endpoint_table = QTableWidget(0, 5)
        self._endpoint_table.setHorizontalHeaderLabels(
            [self.tr("Label"), self.tr("Type"), self.tr("Host"), self.tr("Port"), self.tr("Weight")]
        )
        self._endpoint_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._endpoint_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._endpoint_table.setAlternatingRowColors(True)
        self._endpoint_table.verticalHeader().setVisible(False)
        hdr = self._endpoint_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(1, 70)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(3, 60)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr.resizeSection(4, 60)
        self._endpoint_table.cellDoubleClicked.connect(lambda row, _col: self._edit_endpoint_at(row))
        endpoints_layout.addWidget(self._endpoint_table)

        ep_btn_layout = QHBoxLayout()
        ep_btn_layout.setSpacing(SPACE_SM)

        add_ep_btn = QPushButton(self.tr("Add"))
        add_ep_btn.clicked.connect(self._add_endpoint)
        ep_btn_layout.addWidget(add_ep_btn, 1)

        edit_ep_btn = QPushButton(self.tr("Edit"))
        edit_ep_btn.clicked.connect(self._edit_endpoint)
        ep_btn_layout.addWidget(edit_ep_btn, 1)

        del_ep_btn = QPushButton(self.tr("Delete"))
        del_ep_btn.clicked.connect(self._remove_endpoint)
        ep_btn_layout.addWidget(del_ep_btn, 1)

        test_ep_btn = QPushButton(self.tr("Test Selected"))
        test_ep_btn.clicked.connect(self._test_selected_endpoints)
        ep_btn_layout.addWidget(test_ep_btn, 1)

        endpoints_layout.addLayout(ep_btn_layout)

        layout.addWidget(endpoints_group)

        # ── Bypass 域名表 ──
        bypass_group = QGroupBox(self.tr("Bypass Domains"))
        bypass_layout = QVBoxLayout(bypass_group)
        bypass_layout.setSpacing(SPACE_SM)

        self._bypass_table = QTableWidget(0, 1)
        self._bypass_table.setHorizontalHeaderLabels([self.tr("Domain")])
        self._bypass_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._bypass_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._bypass_table.setAlternatingRowColors(True)
        self._bypass_table.verticalHeader().setVisible(False)
        bypass_hdr = self._bypass_table.horizontalHeader()
        bypass_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        bypass_layout.addWidget(self._bypass_table)

        bp_btn_layout = QHBoxLayout()
        bp_btn_layout.setSpacing(SPACE_SM)

        add_bp_btn = QPushButton(self.tr("Add"))
        add_bp_btn.clicked.connect(self._add_bypass)
        bp_btn_layout.addWidget(add_bp_btn, 1)

        del_bp_btn = QPushButton(self.tr("Delete"))
        del_bp_btn.clicked.connect(self._remove_bypass)
        bp_btn_layout.addWidget(del_bp_btn, 1)

        bypass_layout.addLayout(bp_btn_layout)

        layout.addWidget(bypass_group)

        # ── 脉动条 + 状态栏（_ProgressStatusBar 复合组件） ──
        self._psb = _ProgressStatusBar()
        layout.addWidget(self._psb)

        # ── 按钮行 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(SPACE_SM)

        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(self._on_cancel)
        commit_btn = QPushButton(self.tr("OK"))
        commit_btn.clicked.connect(self._validate_and_accept)
        commit_btn.setDefault(True)

        btn_row.addWidget(cancel_btn, 1)
        btn_row.addWidget(commit_btn, 1)
        layout.addLayout(btn_row)

        # ── 加载数据 ──
        self._refresh_endpoint_table()
        self._refresh_bypass_table()

        # ── 尺寸锁定 ──
        self.adjustSize()
        ideal_h = self.height()
        self.setMaximumWidth(self.width())
        screen = self.screen()
        if screen:
            max_h = int(screen.availableGeometry().height() * 0.85)
            self.setMaximumHeight(min(ideal_h, max_h))
            self.setMinimumHeight(min(ideal_h, max_h))
        else:
            self.setMaximumHeight(ideal_h)
            self.setMinimumHeight(ideal_h)
        self.setMinimumWidth(self.width())

    # ── 公共 API ──

    def get_profile(self) -> ProxyProfile:
        return ProxyProfile(
            name=self._name_edit.text().strip(),
            proxies=tuple(self._endpoints),
            bypass_domains=tuple(self._bypass_domains),
        )

    # ── 端点操作 ──

    _TYPE_NAMES: dict[ProxyType, str] = {ProxyType.HTTP: "HTTP", ProxyType.HTTPS: "HTTPS", ProxyType.SOCKS5: "SOCKS5"}

    def _refresh_endpoint_table(self) -> None:
        self._endpoint_table.setRowCount(len(self._endpoints))
        for row, ep in enumerate(self._endpoints):
            self._endpoint_table.setItem(row, 0, QTableWidgetItem(ep.label))
            self._endpoint_table.setItem(row, 1, QTableWidgetItem(self._TYPE_NAMES.get(ep.type, "HTTP")))
            self._endpoint_table.setItem(row, 2, QTableWidgetItem(ep.host))
            self._endpoint_table.setItem(row, 3, QTableWidgetItem(str(ep.port)))
            self._endpoint_table.setItem(row, 4, QTableWidgetItem(str(ep.weight)))

    def _check_endpoint_duplicate(self, new_ep: ProxyEndpointSpec, exclude_index: int | None = None) -> bool:
        """检查端点是否重复，有重复则弹框返回 False。"""
        conflict_idx = find_duplicate_endpoint(self._endpoints, new_ep, exclude_index)
        if conflict_idx is not None:
            existing = self._endpoints[conflict_idx]
            QMessageBox.warning(
                self,
                self.tr("Duplicate Endpoint"),
                self.tr(
                    'The endpoint "{0}" conflicts with existing "{1}". Endpoints must have unique type, host, and port.'
                ).format(
                    endpoint_display(new_ep),
                    endpoint_display(existing),
                ),
            )
            return False
        return True

    def _add_endpoint(self) -> None:
        dlg = ProxyEndpointEditDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_ep = dlg.get_endpoint()
            if not self._check_endpoint_duplicate(new_ep):
                return
            self._endpoints.append(new_ep)
            self._refresh_endpoint_table()
            self._mark_dirty()

    def _edit_endpoint(self) -> None:
        row = self._selected_endpoint_row()
        if row < 0:
            return
        self._edit_endpoint_at(row)

    def _edit_endpoint_at(self, row: int) -> None:
        dlg = ProxyEndpointEditDialog(self, self._endpoints[row])
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_ep = dlg.get_endpoint()
            if not self._check_endpoint_duplicate(new_ep, exclude_index=row):
                return
            self._endpoints[row] = new_ep
            self._refresh_endpoint_table()
            self._mark_dirty()

    def _remove_endpoint(self) -> None:
        row = self._selected_endpoint_row()
        if row < 0:
            return
        msg = QMessageBox(
            QMessageBox.Warning,
            self.tr("Confirm Delete"),
            self.tr('Delete endpoint "{0}"?').format(self._endpoints[row].label),
            parent=self,
        )
        del_btn = msg.addButton(self.tr("Delete"), QMessageBox.YesRole)
        msg.addButton(self.tr("Cancel"), QMessageBox.NoRole)
        msg.exec()
        if msg.clickedButton() == del_btn:
            del self._endpoints[row]
            self._refresh_endpoint_table()
            self._mark_dirty()

    def _test_selected_endpoints(self) -> None:
        row = self._selected_endpoint_row()
        if row < 0:
            return
        if self._probe_worker is not None and self._probe_worker.isRunning():
            return
        ep = self._endpoints[row]
        parsed = ParsedProxy(
            type=ep.type,
            host=ep.host,
            port=ep.port,
            auth=ProxyAuth(username=ep.username, password=ep.password),
            weight=ep.weight,
        )
        self._probe_worker = _ProbeWorker([parsed], self)
        self._probe_worker.single_result.connect(self._on_single_test_result)
        self._probe_worker.finished.connect(lambda: setattr(self, "_probe_worker", None))
        self._probe_worker.finished.connect(lambda: self._psb.stop_pulse())
        self._probe_worker.probe_error.connect(self._on_probe_error)
        self._psb.start_pulse()
        self._psb.show_status(self.tr("Testing {0}...").format(ep.label), "info")
        self._probe_worker.start()

    def _on_probe_error(self, msg: str) -> None:
        self._psb.show_status(self.tr("Test failed: {0}").format(msg), "error")

    def _on_single_test_result(self, label: str, reachable: bool) -> None:
        if reachable:
            self._psb.show_status(self.tr("{0} reachable").format(label), "success")
        else:
            self._psb.show_status(self.tr("{0} unreachable").format(label), "error")

    def _selected_endpoint_row(self) -> int:
        sel = self._endpoint_table.selectionModel()
        if sel is None or not sel.hasSelection():
            return -1
        return int(sel.selectedRows()[0].row())

    # ── Bypass 操作 ──

    def _refresh_bypass_table(self) -> None:
        self._bypass_table.setRowCount(len(self._bypass_domains))
        for row, domain in enumerate(self._bypass_domains):
            self._bypass_table.setItem(row, 0, QTableWidgetItem(domain))

    def _add_bypass(self) -> None:
        dlg = _BypassDomainDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        self._bypass_domains.append(dlg.get_domain())
        self._refresh_bypass_table()
        self._mark_dirty()

    def _remove_bypass(self) -> None:
        sel = self._bypass_table.selectionModel()
        if sel is None or not sel.hasSelection():
            return
        row = int(sel.selectedRows()[0].row())
        del self._bypass_domains[row]
        self._refresh_bypass_table()
        self._mark_dirty()

    # ── 验证与脏检查 ──

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _validate(self) -> bool:
        name = self._name_edit.text().strip()
        if not name:
            self._name_error.setText(self.tr("Name cannot be empty"))
            self._name_error.show()
            return False
        if name in self._existing_names:
            self._name_error.setText(self.tr("Name already exists"))
            self._name_error.show()
            return False
        self._name_error.hide()
        # 端点去重（兜底——正常路径已被 _add_endpoint / _edit_endpoint_at 拦截）
        seen: dict[str, int] = {}
        for i, ep in enumerate(self._endpoints):
            key = endpoint_key(ep)
            if key in seen:
                first = self._endpoints[seen[key]]
                QMessageBox.warning(
                    self,
                    self.tr("Duplicate Endpoint"),
                    self.tr(
                        'The endpoint "{0}" conflicts with existing "{1}". '
                        "Each endpoint must have a unique type, host, and port "
                        "combination."
                    ).format(
                        endpoint_display(ep),
                        endpoint_display(first),
                    ),
                )
                return False
            seen[key] = i
        return True

    def _validate_and_accept(self) -> None:
        if self._validate():
            self.accept()

    def _cleanup_worker(self) -> None:
        w = self._probe_worker
        if w is None:
            return
        if not w.isRunning():
            self._probe_worker = None
            self._psb.stop_pulse()
            return
        try:
            w.single_result.disconnect()
        except RuntimeError:
            pass
        try:
            w.finished.disconnect()
        except RuntimeError:
            pass
        try:
            w.probe_error.disconnect()
        except RuntimeError:
            pass
        w.cancel()
        if not w.wait(10000):
            w.terminate()
            w.wait(2000)
        self._probe_worker = None
        self._psb.stop_pulse()

    def reject(self) -> None:
        self._cleanup_worker()
        self._psb.dispose()
        super().reject()

    def accept(self) -> None:
        self._cleanup_worker()
        super().accept()

    def _on_cancel(self) -> None:
        if self._dirty and not self._is_new:
            answer = QMessageBox.question(
                self,
                self.tr("Discard Changes?"),
                self.tr("Unsaved changes will be lost. Discard?"),
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
        self.reject()


# ═══════════════════════════════════════════════════════════════════════════
# _ProbeWorker
# ═══════════════════════════════════════════════════════════════════════════


class _ProbeWorker(QThread):
    """后台探针线程 — 每端点 10 次探测，跨端点并行、单端点串行。"""

    single_result = Signal(str, bool)  # label, reachable
    all_results = Signal(dict)  # label -> (reachable_count, total_count)
    probe_error = Signal(str)  # error message

    def __init__(self, endpoints: list[ParsedProxy], parent=None):
        super().__init__(parent)
        self._endpoints = endpoints
        self._main_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            if self.isInterruptionRequested():
                return
            main_task = asyncio.ensure_future(self._probe_all(), loop=loop)
            self._main_task = main_task
            results = loop.run_until_complete(main_task)
            for label, (reachable_count, total) in results.items():
                self.single_result.emit(label, reachable_count == total)
            self.all_results.emit(results)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.probe_error.emit(str(exc)[:200])
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            finally:
                loop.close()
                asyncio.set_event_loop(None)
                self._loop = None
                self._main_task = None

    def cancel(self) -> None:
        self.requestInterruption()
        if self._main_task is not None and self._loop is not None and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._main_task.cancel)
            except RuntimeError:
                pass

    async def _probe_all(self) -> dict[str, tuple[int, int]]:
        tasks = [self._probe_one_n_times(ep) for ep in self._endpoints]
        ep_results = await asyncio.gather(*tasks)
        result: dict[str, tuple[int, int]] = {}
        for ep, (reachable, total) in zip(self._endpoints, ep_results):
            result[endpoint_key(ep)] = (reachable, total)
        return result

    async def _probe_one_n_times(self, ep: ParsedProxy, n: int = 10, interval: float = 0.5) -> tuple[int, int]:
        reachable = 0
        for _ in range(n):
            try:
                r = await probe_one(ep)
                if r.reachable:
                    reachable += 1
            except Exception:
                pass
            await asyncio.sleep(interval)
        return reachable, n


# ═══════════════════════════════════════════════════════════════════════════
# _ProxyProfilePage
# ═══════════════════════════════════════════════════════════════════════════


class _ProxyProfilePage(_TableManagementPage):
    """代理 Profile 列表管理页 — View + CRUD + ☆ 设为默认 + 测试选中。"""

    profile_changed = Signal()
    busy_changed = Signal(bool)
    status_message = Signal(str, str)  # msg, level

    def __init__(self, prefs: Preferences, parent=None):
        self._prefs = prefs
        self._probe_worker: _ProbeWorker | None = None
        super().__init__(parent)

    # ── _TableManagementPage 抽象方法 ──

    def _define_columns(self) -> list[ColumnDef]:
        return [
            ColumnDef(key="name", label=self.tr("Name")),
            ColumnDef(key="endpoints", label=self.tr("Endpoints")),
            ColumnDef(key="status", label=self.tr("Status"), resize="fixed", width=100),
            ColumnDef(key="consumers", label=self.tr("In Use")),
        ]

    def _create_model(self) -> ProxyProfileListModel:
        return ProxyProfileListModel(self._prefs, self)

    def _on_add(self) -> None:
        model: ProxyProfileListModel = self._model  # type: ignore[assignment]
        names = [p.name for p in model.profiles]
        dlg = ProxyProfileEditDialog(self, names)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._prefs.save_proxy_profile(dlg.get_profile())
            self.refresh()
            self.profile_changed.emit()

    def _on_edit(self, row: int) -> None:
        model: ProxyProfileListModel = self._model  # type: ignore[assignment]
        profile = model.get_profile(row)
        if profile is None:
            return
        old_name = profile.name
        names = [p.name for p in model.profiles]
        dlg = ProxyProfileEditDialog(self, names, profile)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_profile = dlg.get_profile()
            self._prefs.save_proxy_profile(new_profile)
            if new_profile.name != old_name:
                self._prefs.remove_proxy_profile(old_name, force=True)
            self.refresh()
            self.profile_changed.emit()

    def _on_remove(self, row: int) -> None:
        model: ProxyProfileListModel = self._model  # type: ignore[assignment]
        profile = model.get_profile(row)
        if profile is None:
            return
        # 检查是否有 consumer 在使用
        using_consumers = [
            self.tr(PROXY_CONSUMERS[k])
            for k in PROXY_CONSUMERS
            if (e := self._prefs.get_proxy_last_used(k)) and e.get("profile") == profile.uuid
        ]
        if using_consumers:
            QMessageBox.warning(
                self,
                self.tr("Cannot Delete"),
                self.tr("The following consumers are using this Profile. Switch them first:\n{0}").format(
                    ", ".join(using_consumers)
                ),
            )
            return
        msg = QMessageBox(
            QMessageBox.Warning,
            self.tr("Confirm Delete"),
            self.tr('Delete Profile "{0}"?').format(profile.name),
            parent=self,
        )
        del_btn = msg.addButton(self.tr("Delete"), QMessageBox.YesRole)
        msg.addButton(self.tr("Cancel"), QMessageBox.NoRole)
        msg.exec()
        if msg.clickedButton() == del_btn:
            self._prefs.remove_proxy_profile(profile.name)
            self.refresh()
            self.profile_changed.emit()

    def _apply_toggle(self, name: str, enabled: bool) -> None:
        pass  # Proxy profile 不使用 enable/disable toggle

    def _search_columns(self) -> tuple[int, ...]:
        return (0,)

    def _empty_text(self) -> str:
        return str(self.tr('No Proxy Profiles — Click "Add" to create one'))

    def _extra_buttons(self) -> list[tuple[str, Callable[[], None]]]:
        return [
            (self.tr("☆ Set as Default"), self._on_set_default),
            (self.tr("Test Selected"), self._on_test_selected),
        ]

    # ── UI setup overrides ──

    def _setup_ui(self) -> None:
        super()._setup_ui()

        # StatusColorDelegate on status column

        delegate = StatusColorDelegate(
            {
                "all_reachable": "success",
                "partial": "warning",
                "untested": "disabled",
            },
        )
        if self._table is not None:
            self._table.setItemDelegateForColumn(2, delegate)

    # ── actions ──

    def _on_set_default(self) -> None:
        row = self._selected_source_row()
        if row < 0:
            return
        model: ProxyProfileListModel = self._model  # type: ignore[assignment]
        profile = model.get_profile(row)
        if profile is None:
            return
        for consumer_key in PROXY_CONSUMERS:
            self._prefs.set_proxy_last_used(consumer_key, profile.uuid, "")
        self.refresh()
        self.profile_changed.emit()

    def _on_test_selected(self) -> None:
        if self._probe_worker is not None and self._probe_worker.isRunning():
            self.status_message.emit(self.tr("Test in progress, please try again later"), "warning")
            return

        row = self._selected_source_row()
        if row < 0:
            return
        model: ProxyProfileListModel = self._model  # type: ignore[assignment]
        profile = model.get_profile(row)
        if profile is None:
            return

        all_endpoints: list[ParsedProxy] = []
        for spec in profile.proxies:
            parsed = ParsedProxy(
                type=spec.type,
                host=spec.host,
                port=spec.port,
                auth=ProxyAuth(username=spec.username, password=spec.password),
                weight=spec.weight,
            )
            all_endpoints.append(parsed)

        if not all_endpoints:
            self.status_message.emit(self.tr("No endpoints to test"), "warning")
            return

        profile_name = profile.name
        model._probe_accum.clear()
        self._probe_worker = _ProbeWorker(all_endpoints, self)
        self._probe_worker.single_result.connect(
            lambda label, reachable, nm=profile_name, m=model: self._on_probe_single(label, reachable, nm, m)
        )
        self._probe_worker.all_results.connect(lambda _: self.refresh())
        self._probe_worker.finished.connect(lambda: setattr(self, "_probe_worker", None))
        self._probe_worker.finished.connect(lambda: self.busy_changed.emit(False))
        self._probe_worker.probe_error.connect(
            lambda msg: self.status_message.emit(self.tr("Test failed: {0}").format(msg), "error")
        )
        self.busy_changed.emit(True)
        self.status_message.emit(self.tr("Testing {0}...").format(profile.name), "info")
        self._probe_worker.start()

    def _cleanup_worker(self) -> None:
        w = self._probe_worker
        if w is None:
            return
        if not w.isRunning():
            self._probe_worker = None
            self.busy_changed.emit(False)
            return
        for sig_name in ("single_result", "all_results", "probe_error", "finished"):
            try:
                getattr(w, sig_name).disconnect()
            except (RuntimeError, AttributeError):
                pass
        w.cancel()
        if not w.wait(15000):
            w.terminate()
            w.wait(2000)
        self._probe_worker = None
        self.busy_changed.emit(False)

    @staticmethod
    def _on_probe_single(label: str, reachable: bool, profile_name: str, model: ProxyProfileListModel) -> None:
        r, u = model._probe_accum.get(profile_name, (0, 0))
        if reachable:
            r += 1
        else:
            u += 1
        model._probe_accum[profile_name] = (r, u)
        model.set_test_result(profile_name, r, u)
