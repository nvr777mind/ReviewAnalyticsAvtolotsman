# reviews_viewer.py
import sys
import csv
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Any, List, Tuple

import pandas as pd
from PyQt6.QtCore import (
    QAbstractTableModel, Qt, QModelIndex, QVariant, QSortFilterProxyModel,
    QSize, QDate, QEvent, QProcess
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTableView,
    QPushButton, QComboBox, QSpinBox, QGroupBox, QFormLayout, QDateEdit,
    QMessageBox, QStyledItemDelegate, QHeaderView, QLabel, QPlainTextEdit,
    QSizePolicy, QStyleOptionButton, QStyle
)
from PyQt6.QtGui import QTextOption, QTextDocument, QPainter

# matplotlib (QtAgg для PyQt6)
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas


# ---------- Модель DataFrame → Qt ----------
class DataFrameModel(QAbstractTableModel):
    def __init__(self, df: pd.DataFrame):
        super().__init__()
        self._df = df.reset_index(drop=True)
        self._headers = list(map(str, self._df.columns))
        self._need_answer_idx: Optional[int] = self.column_name_to_index("need_answer")

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._df)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._headers)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return QVariant()

        r, c = index.row(), index.column()

        # Чекбокс для need_answer
        if self._need_answer_idx is not None and c == self._need_answer_idx:
            if role == Qt.ItemDataRole.CheckStateRole:
                val = self._df.iat[r, c]
                checked = str(val).strip().lower() in {"1", "true", "yes", "y", "да"}
                return Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
                return ""  # текст в ячейке не нужен, только чекбокс

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            val = self._df.iat[r, c]
            if pd.isna(val):
                return ""
            return str(val)

        return QVariant()

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid():
            return False

        r, c = index.row(), index.column()

        # Обработка клика по чекбоксу need_answer
        if self._need_answer_idx is not None and c == self._need_answer_idx and role == Qt.ItemDataRole.CheckStateRole:
            self._df.iat[r, c] = 1 if value == Qt.CheckState.Checked else 0
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole, Qt.ItemDataRole.DisplayRole])
            return True

        return False

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if self._need_answer_idx is not None and index.column() == self._need_answer_idx:
            return base | Qt.ItemFlag.ItemIsUserCheckable
        return base

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role != Qt.ItemDataRole.DisplayRole:
            return QVariant()
        return self._headers[section] if orientation == Qt.Orientation.Horizontal else section + 1

    def get_dataframe(self) -> pd.DataFrame:
        return self._df

    def column_name_to_index(self, name: str) -> Optional[int]:
        try:
            return self._headers.index(name)
        except ValueError:
            return None

    def refresh_need_answer_index(self):
        self._need_answer_idx = self.column_name_to_index("need_answer")


# ---------- Делегат для переносов и авто-высоты текста ----------
class TextWrapDelegate(QStyledItemDelegate):
    def __init__(self, table: QTableView,
                 wrap_mode=QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere):
        super().__init__(table)
        self.table = table
        self.wrap_mode = wrap_mode

    def paint(self, painter: QPainter, option, index):
        option.textElideMode = Qt.TextElideMode.ElideNone
        super().paint(painter, option, index)

    def sizeHint(self, option, index) -> QSize:
        text = str(index.data() or "")
        if not text:
            return super().sizeHint(option, index)
        col = index.column()
        width = max(50, self.table.columnWidth(col) - 12)

        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        topt = QTextOption()
        topt.setWrapMode(self.wrap_mode)
        doc.setDefaultTextOption(topt)
        doc.setTextWidth(width)
        doc.setPlainText(text)

        h = int(doc.size().height()) + 8
        return QSize(width + 12, h)


# ---------- Делегат чекбокса для need_answer ----------
class CheckBoxDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opt = QStyleOptionButton()
        opt.state |= QStyle.StateFlag.State_Enabled
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        opt.state |= (QStyle.StateFlag.State_On if checked else QStyle.StateFlag.State_Off)
        style = option.widget.style() if option.widget else QApplication.style()
        rect = style.subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, opt, None)
        opt.rect = rect
        opt.rect.moveCenter(option.rect.center())
        style.drawControl(QStyle.ControlElement.CE_CheckBox, opt, painter)

    def editorEvent(self, event, model, option, index):
        et = event.type()
        if et == QEvent.Type.MouseButtonRelease or et == QEvent.Type.MouseButtonDblClick:
            if getattr(event, "button", lambda: None)() != Qt.MouseButton.LeftButton:
                return False
        elif et == QEvent.Type.KeyPress:
            if getattr(event, "key", lambda: None)() not in (Qt.Key.Key_Space, Qt.Key.Key_Select):
                return False
        else:
            return False
        cur = index.data(Qt.ItemDataRole.CheckStateRole)
        new_state = Qt.CheckState.Unchecked if cur == Qt.CheckState.Checked else Qt.CheckState.Checked
        return model.setData(index, new_state, Qt.ItemDataRole.CheckStateRole)


# ---------- Прокси с фильтрами ----------
class ReviewFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, src: DataFrameModel):
        super().__init__()
        self.setSourceModel(src)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        self._platform_exact: Optional[str] = None
        self._org_exact: Optional[str] = None
        self._rating_min: Optional[float] = None
        self._rating_max: Optional[float] = None
        self._date_from: Optional[datetime] = None
        self._date_to: Optional[datetime] = None
        self._sentiment_exact: Optional[str] = None
        self._need_answer: Optional[bool] = None

        self.col_platform = src.column_name_to_index("platform")
        self.col_org = src.column_name_to_index("organization")
        self.col_rating = src.column_name_to_index("rating")
        self.col_date = src.column_name_to_index("date_iso")
        self.col_sentiment = (
            src.column_name_to_index("sentiment")
            or src.column_name_to_index("sentiment_label")
            or src.column_name_to_index("tone")
            or src.column_name_to_index("Тональность")
        )
        self.col_need_answer = src.column_name_to_index("need_answer")

    def set_platform_filter(self, val: Optional[str]):
        self._platform_exact = val if val and val != "— Все —" else None
        self.invalidateFilter()

    def set_org_filter(self, val: Optional[str]):
        self._org_exact = val if val and val != "— Все —" else None
        self.invalidateFilter()

    def set_sentiment_filter(self, val: Optional[str]):
        self._sentiment_exact = val if val and val != "— Все —" else None
        self.invalidateFilter()

    def set_need_answer_filter(self, text: str):
        if not text or text == "— Все —" or self.col_need_answer is None:
            self._need_answer = None
        elif text.lower().startswith("требует"):
            self._need_answer = True
        else:
            self._need_answer = False
        self.invalidateFilter()

    def set_rating_range(self, rmin: Optional[float], rmax: Optional[float]):
        self._rating_min = rmin
        self._rating_max = rmax
        self.invalidateFilter()

    def set_date_range(self, dfrom: Optional[datetime], dto: Optional[datetime]):
        self._date_from = dfrom
        self._date_to = dto
        self.invalidateFilter()

    def _value(self, src_row: int, col: Optional[int]) -> Optional[str]:
        if col is None:
            return None
        idx = self.sourceModel().index(src_row, col)
        val = self.sourceModel().data(idx, Qt.ItemDataRole.DisplayRole)
        return None if val is None or val == "" else str(val)

    @staticmethod
    def _parse_float(s: Optional[str]) -> Optional[float]:
        if not s:
            return None
        try:
            return float(s.replace(",", "."))
        except Exception:
            return None

    @staticmethod
    def _parse_date_iso(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        s = s.strip()
        for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%d.%m.%Y"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None

    @staticmethod
    def _truthy_need_answer(raw: Optional[str]) -> bool:
        if raw is None:
            return False
        s = str(raw).strip().lower()
        return s in {"1", "true", "yes", "y", "да"}

    def filterAcceptsRow(self, src_row: int, src_parent: QModelIndex) -> bool:
        if self._platform_exact is not None:
            p = self._value(src_row, self.col_platform)
            if (p or "") != self._platform_exact:
                return False

        if self._org_exact is not None:
            o = self._value(src_row, self.col_org)
            if (o or "") != self._org_exact:
                return False

        if self._sentiment_exact is not None:
            s = self._value(src_row, self.col_sentiment)
            if (s or "") != self._sentiment_exact:
                return False

        if self._rating_min is not None or self._rating_max is not None:
            r = self._parse_float(self._value(src_row, self.col_rating))
            if r is None:
                return False
            if self._rating_min is not None and r < self._rating_min:
                return False
            if self._rating_max is not None and r > self._rating_max:
                return False

        if self._date_from is not None or self._date_to is not None:
            d = self._parse_date_iso(self._value(src_row, self.col_date))
            if d is None:
                return False
            if self._date_from is not None and d < self._date_from:
                return False
            if self._date_to is not None and d > self._date_to:
                return False

        # --- need_answer filter (читаем сырое значение из DataFrame, а не DisplayRole) ---
        if self._need_answer is not None and self.col_need_answer is not None:
            src_model: DataFrameModel = self.sourceModel()  # type: ignore
            raw = src_model.get_dataframe().iat[src_row, self.col_need_answer]
            is_true = self._truthy_need_answer(raw)
            if is_true != self._need_answer:
                return False

        return True

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        src_idx = self.mapToSource(index)
        return self.sourceModel().flags(src_idx)

    def setData(self, index: QModelIndex, value, role=Qt.ItemDataRole.EditRole):
        if not index.isValid():
            return False
        src_idx = self.mapToSource(index)
        ok = self.sourceModel().setData(src_idx, value, role)
        if ok:
            self.dataChanged.emit(index, index, [role])
        return ok


class DateEditWithDash(QDateEdit):
    def __init__(self, start_year=2022, start_month=1, parent=None):
        super().__init__(parent)
        self._start_year = start_year
        self._start_month = start_month

        self.setCalendarPopup(True)
        self.setDisplayFormat("yyyy-MM-dd")
        self.setSpecialValueText("—")
        self.setDateRange(QDate(1900, 1, 1), QDate(2100, 12, 31))
        self.setDate(self.minimumDate())
        self.calendarWidget().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self.calendarWidget() and event.type() == QEvent.Type.Show:
            self.calendarWidget().setCurrentPage(self._start_year, self._start_month)
        return super().eventFilter(obj, event)

    def set_start_page(self, year: int, month: int = 1):
        self._start_year, self._start_month = year, month


# ---------- Главное окно ----------
class MainWindow(QMainWindow):
    def __init__(self, df: Optional[pd.DataFrame] = None):
        super().__init__()
        self.setWindowTitle("Reviews Viewer")
        self.resize(1400, 820)

        self.FILTERS_WIDTH_RATIO = 0.5
        self.FILTERS_MIN_W = 480

        self.table = QTableView()
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        thead = self.table.horizontalHeader()
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        thead.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        thead.setStretchLastSection(False)
        self.table.setEditTriggers(QTableView.EditTrigger.AllEditTriggers)

        self._model: Optional[DataFrameModel] = None
        self._proxy: Optional[ReviewFilterProxyModel] = None

        # индексы для раскладки
        self._text_col: Optional[int] = None
        self._col_rating: Optional[int] = None
        self._col_platform: Optional[int] = None
        self._col_org: Optional[int] = None
        self._col_need_answer: Optional[int] = None

        # доли/ширины
        self._text_col_ratio = 0.50
        self._org_col_ratio = 0.17
        self._RATING_W = 10
        self._PLATFORM_W = 90
        self._ORG_MIN = 180
        self._ORG_MAX = 460
        self._NEED_ANSWER_W = 130

        # --- фильтры ---
        self._platform_combo = QComboBox()
        self._org_combo = QComboBox()
        self._sentiment_combo = QComboBox()
        self._rmin = QSpinBox(); self._rmax = QSpinBox()
        self._date_from = DateEditWithDash(start_year=2022, start_month=1)
        self._date_to   = DateEditWithDash(start_year=2022, start_month=1)
        self._need_answer_combo = QComboBox()
        self._need_answer_combo.addItems(["— Все —", "Требует", "Не требует"])
        self._need_answer_combo.setToolTip("Фильтр доступен только для датасета «Отзывы». В «Сводке» будет проигнорирован.")

        self._rmin.setRange(0, 1000); self._rmax.setRange(0, 1000)
        self._rmin.setSpecialValueText("—"); self._rmax.setSpecialValueText("—")
        self._rmin.setValue(0); self._rmax.setValue(0)

        # --- даты ---
        self._date_from.setDisplayFormat("yyyy-MM-dd")
        self._date_to.setDisplayFormat("yyyy-MM-dd")
        self._date_from.setSpecialValueText("—")
        self._date_to.setSpecialValueText("—")
        self._date_from.setDateRange(QDate(1900, 1, 1), QDate(2100, 12, 31))
        self._date_to.setDateRange(QDate(1900, 1, 1), QDate(2100, 12, 31))
        self._date_from.setDate(self._date_from.minimumDate())
        self._date_to.setDate(self._date_to.minimumDate())

        # -------- Контейнер фильтров --------
        self._filters_group = QGroupBox("Фильтры")
        self._filters_group.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        # --- кнопки действий ---
        apply_btn = QPushButton("Применить фильтры")
        clear_btn = QPushButton("Сбросить фильтры")

        # Две кнопки экспорта
        export_csv_btn = QPushButton("Экспорт отфильтрованного в CSV")
        export_json_btn = QPushButton("Экспорт отфильтрованного в JSON")

        run_all_btn = QPushButton("Собрать данные заново")
        run_incr_btn = QPushButton("Собрать только новое")

        # стили кнопок
        apply_btn.setStyleSheet("""
            QPushButton {
                background-color: #d4edda; border: 1px solid #c3e6cb;
                padding: 6px 12px; border-radius: 6px; color: #1b1e21;
            }
            QPushButton:hover { background-color: #cfe9d6; }
            QPushButton:pressed { background-color: #c3e6cb; }
        """)
        clear_btn.setStyleSheet("""
            QPushButton {
                background-color: #f8d7da; border: 1px solid #f5c6cb;
                padding: 6px 12px; border-radius: 6px; color: #1b1e21;
            }
            QPushButton:hover { background-color: #f6cfd3; }
            QPushButton:pressed { background-color: #f5c6cb; }
        """)
        common_run_style = """
            QPushButton {
                background-color: #d1ecf1; border: 1px solid #bee5eb;
                padding: 6px 12px; border-radius: 6px; color: #0c5460;
            }
            QPushButton:hover { background-color: #cbe7ed; }
            QPushButton:pressed { background-color: #bee5eb; }
        """
        run_all_btn.setStyleSheet(common_run_style)
        run_incr_btn.setStyleSheet(common_run_style)

        # сводка (белым)
        self._stats_label = QLabel("Отфильтровано: — | Средний рейтинг: —")
        self._stats_label.setStyleSheet("color: #ffffff; padding: 2px 0;")

        # ==== CSV-переключатель ====
        self._csv_mode = "reviews"  # "reviews" | "summary"
        self._csv_paths = {
            "reviews": Path("Csv/Reviews/all_reviews.csv"),
            "summary": Path("Csv/Summary/all_summary.csv"),
        }
        self._current_csv_path: Optional[Path] = None

        # Кнопка "Переключить" + короткая метка "Отзывы/Сводка"
        self._csv_toggle_btn = QPushButton("Переключить")
        self._csv_toggle_btn.setToolTip("Переключить между набором Отзывы и Сводка")
        self._csv_toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: #e2e3e5; border: 1px solid #d6d8db;
                padding: 6px 12px; border-radius: 6px; color: #1b1e21;
            }
            QPushButton:hover { background-color: #d8d9db; }
            QPushButton:pressed { background-color: #d6d8db; }
        """)
        self._csv_current_label = QLabel("")
        self._csv_current_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        # --- Унификация высоты контролов ---
        common_h = 28
        for w in [self._csv_toggle_btn,
                  self._platform_combo, self._org_combo, self._sentiment_combo,
                  self._rmin, self._rmax, self._date_from, self._date_to,
                  self._need_answer_combo,
                  apply_btn, clear_btn, export_csv_btn, export_json_btn,
                  run_all_btn, run_incr_btn]:
            w.setFixedHeight(common_h)
            w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        # --- Верхняя строка контейнера фильтров ---
        header_row = QVBoxLayout()
        top_buttons = QHBoxLayout()
        top_buttons.addStretch(1)
        top_buttons.addWidget(run_all_btn)
        header_row.addLayout(top_buttons)
        # новая кнопка — непосредственно под предыдущей
        second_row = QHBoxLayout()
        second_row.addStretch(1)
        second_row.addWidget(run_incr_btn)
        header_row.addLayout(second_row)

        # --- Форма фильтров ---
        fl = QFormLayout()
        fl.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        fl.setFormAlignment(Qt.AlignmentFlag.AlignLeft)
        fl.setVerticalSpacing(10)

        # Строка с датасетом
        csv_row = QHBoxLayout()
        csv_row.setContentsMargins(0, 0, 0, 0)
        csv_row.setSpacing(8)
        csv_row.addWidget(self._csv_current_label, 1)
        csv_row.addWidget(self._csv_toggle_btn)
        csv_w = QWidget(); csv_w.setLayout(csv_row)
        fl.addRow(QLabel("Датасет:"), csv_w)

        # Ниже обычные фильтры
        fl.addRow(QLabel("Платформа:"), self._platform_combo)
        fl.addRow(QLabel("Организация:"), self._org_combo)
        fl.addRow(QLabel("Тональность:"), self._sentiment_combo)

        # ----- Рейтинг -----
        rating_row = QHBoxLayout()
        rating_row.setContentsMargins(0, 0, 0, 0)
        rating_row.setSpacing(8)
        rating_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        rating_row.addWidget(QLabel("Мин:"))
        rating_row.addWidget(self._rmin)
        rating_row.addWidget(QLabel("Макс:"))
        rating_row.addWidget(self._rmax)
        rating_w = QWidget(); rating_w.setLayout(rating_row)
        rating_w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        fl.addRow(QLabel("Рейтинг:"), rating_w)

        # ----- Дата -----
        date_row = QHBoxLayout()
        date_row.setContentsMargins(0, 0, 0, 0)
        date_row.setSpacing(8)
        date_row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        date_row.addWidget(QLabel("От:"))
        date_row.addWidget(self._date_from)
        date_row.addWidget(QLabel("До:"))
        date_row.addWidget(self._date_to)
        date_w = QWidget(); date_w.setLayout(date_row)
        date_w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        fl.addRow(QLabel("Дата (date_iso):"), date_w)

        # ----- Требует ответа -----
        self._need_answer_label = QLabel("Требует ответа:")
        fl.addRow(self._need_answer_label, self._need_answer_combo)

        # Кнопки применить/сброс
        btns_top = QHBoxLayout()
        btns_top.setContentsMargins(0, 0, 0, 0)
        btns_top.addWidget(apply_btn)
        btns_top.addWidget(clear_btn)
        btns_top.addSpacing(12)
        btns_top.addStretch(1)

        # Кнопки экспорта (ниже сводки)
        btns_export = QHBoxLayout()
        btns_export.setContentsMargins(0, 0, 0, 0)
        btns_export.setSpacing(8)
        btns_export.addWidget(export_csv_btn)
        btns_export.addWidget(export_json_btn)
        btns_export.addStretch(1)

        # ЛОГ
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Лог выполнения парсеров и объединения…")
        self._log.setMinimumHeight(150)
        self._log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Внутренний лэйаут группы фильтров
        filters_vbox = QVBoxLayout()
        filters_vbox.setContentsMargins(9, 12, 9, 9)
        filters_vbox.setSpacing(8)
        filters_vbox.addLayout(header_row)
        filters_vbox.addLayout(fl)
        filters_vbox.addLayout(btns_top)
        filters_vbox.addWidget(self._stats_label)
        filters_vbox.addLayout(btns_export)
        filters_vbox.addWidget(self._log, 1)
        self._filters_group.setLayout(filters_vbox)

        # -------- Группа диаграмм (справа) --------
        self._charts_group = QGroupBox("Диаграммы")
        self._charts_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        charts_vbox = QVBoxLayout()
        charts_vbox.setContentsMargins(9, 12, 9, 9)
        charts_vbox.setSpacing(8)

        # Канвас 1: количество по рейтингу
        self._fig_rating = Figure(figsize=(4, 2.8), tight_layout=True)
        self._ax_rating = self._fig_rating.add_subplot(111)
        self._canvas_rating = FigureCanvas(self._fig_rating)
        self._canvas_rating.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        charts_vbox.addWidget(QLabel("Количество комментариев по рейтингу"))
        charts_vbox.addWidget(self._canvas_rating, 1)

        # Канвас 2: доли по тональности
        self._fig_sent = Figure(figsize=(4, 2.8), tight_layout=True)
        self._ax_sent = self._fig_sent.add_subplot(111)
        self._canvas_sent = FigureCanvas(self._fig_sent)
        self._canvas_sent.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        charts_vbox.addWidget(QLabel("Соотношение отзывов по тональности"))
        charts_vbox.addWidget(self._canvas_sent, 1)

        self._charts_group.setLayout(charts_vbox)

        # ---- Верхняя строка окна ----
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.addWidget(self._filters_group)
        top_row.addWidget(self._charts_group, 1)

        # --- Кнопка "Развернуть" над отзывами ---
        self._expand_btn = QPushButton("Развернуть список отзывов")
        self._expand_btn.setFixedHeight(28)
        self._expand_btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._expand_style_collapsed = """
            QPushButton { background-color: #d4edda; border: 1px solid #c3e6cb;
                          padding: 6px 12px; border-radius: 6px; color: #1b1e21; }
            QPushButton:hover { background-color: #cfe9d6; }
            QPushButton:pressed { background-color: #c3e6cb; }
        """
        self._expand_style_expanded = """
            QPushButton { background-color: #f8d7da; border: 1px solid #f5c6cb;
                          padding: 6px 12px; border-radius: 6px; color: #1b1e21; }
            QPushButton:hover { background-color: #f6cfd3; }
            QPushButton:pressed { background-color: #f5c6cb; }
        """
        self._expand_btn.setStyleSheet(self._expand_style_collapsed)
        self._expanded = False

        # ---- Основной вертикальный лэйаут ----
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(8)
        cl.addLayout(top_row)
        cl.addWidget(self._expand_btn)
        cl.addWidget(self.table, 1)
        self.setCentralWidget(central)

        # Сигналы
        apply_btn.clicked.connect(self.apply_filters)
        clear_btn.clicked.connect(self.clear_filters)
        export_csv_btn.clicked.connect(self.export_filtered_csv)
        export_json_btn.clicked.connect(self.export_filtered_json)
        run_all_btn.clicked.connect(self.run_full_pipeline)
        run_incr_btn.clicked.connect(self.run_incremental_pipeline)
        self._expand_btn.clicked.connect(self._toggle_expand_reviews)
        self._csv_toggle_btn.clicked.connect(self._on_toggle_csv)

        # Поля состояния пайплайна
        self._running = False
        self._scraper_procs: List[Tuple[str, QProcess]] = []
        self._scrapers_done = 0

        # Пути к скриптам (полный сбор)
        self.SCRAPER_SCRIPTS: List[Tuple[str, Path]] = [
            ("Google Maps", Path("Parsers/gmaps_reviews.py")),
            ("Yandex Maps", Path("Parsers/yamaps_reviews.py")),
            ("2GIS", Path("Parsers/2gis_reviews.py")),
        ]
        self.MERGE_SCRIPTS: List[Tuple[str, Path]] = [
            ("Merge Reviews", Path("Csv/Reviews/merged_reviews.py")),
            ("Add Sentiment", Path("DataAnalytics/add_sentiment.py")),
            ("Merge Summary", Path("Csv/Summary/merged_summary.py")),
        ]

        # Инкрементальный сбор — скрипты обнаруживаются динамически
        self.INCR_MERGE_SCRIPTS: List[Tuple[str, Path]] = [
            ("Merge NEW Reviews", Path("Csv/Reviews/NewReviews/merged_new_reviews.py")),
            ("Merge NEW Summary", Path("Csv/Summary/NewSummary/merged_new_summary.py")),
            ("Add Sentiment", Path("DataAnalytics/add_sentiment.py")),
        ]

        # Данные
        if df is not None:
            self.set_dataframe(df)
        else:
            self.autoload_csv()

        # начальная подгонка ширины фильтров
        self._adjust_filters_width()

    # ---------- Сервис: ширина группы фильтров ----------
    def _adjust_filters_width(self):
        cw = self.centralWidget().width() if self.centralWidget() else self.width()
        target = max(self.FILTERS_MIN_W, int(cw * self.FILTERS_WIDTH_RATIO))
        target = min(target, max(self.FILTERS_MIN_W, cw - 40))
        self._filters_group.setFixedWidth(target)

    # ---- Автозагрузка текущего CSV по режиму ----
    def autoload_csv(self):
        target = self._csv_paths.get(self._csv_mode, Path("Csv/Reviews/all_reviews.csv"))
        self.load_csv(target)

    # ---- Универсальная загрузка CSV ----
    def load_csv(self, csv_path: Path):
        self._current_csv_path = csv_path
        if not csv_path.exists():
            QMessageBox.critical(self, "Файл не найден",
                                 f"Не удалось найти файл:\n{csv_path}")
            self._update_csv_label()
            return
        try:
            df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)

            if "rating" in df.columns:
                df["rating"] = pd.to_numeric(
                    df["rating"].astype(str).str.replace(",", ".", regex=False), errors="coerce"
                )

            if self._csv_mode == "reviews" and "need_answer" not in df.columns:
                df["need_answer"] = 0

            self.set_dataframe(df)
            self.statusBar().showMessage(
                f"Загружено: {csv_path} | строк: {len(df)} | столбцов: {len(df.columns)}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка чтения CSV", str(e))
        finally:
            self._update_csv_label()

    # ---- Обработчик "Переключить" ----
    def _on_toggle_csv(self):
        self._csv_mode = "summary" if self._csv_mode == "reviews" else "reviews"
        self.autoload_csv()

    # ---- Обновление метки текущего набора (Отзывы/Сводка) ----
    def _update_csv_label(self):
        self._csv_current_label.setText("Отзывы" if self._csv_mode == "reviews" else "Сводка")
        path = self._csv_paths.get(self._csv_mode)
        if path is not None:
            self._csv_current_label.setToolTip(str(path))
            self._csv_toggle_btn.setToolTip(f"Переключить. Текущий файл: {path}")

    # ---- Работа с данными ----
    def set_dataframe(self, df: pd.DataFrame):
        self._model = DataFrameModel(df)
        self._proxy = ReviewFilterProxyModel(self._model)
        self.table.setModel(self._proxy)

        self._text_col = self._model.column_name_to_index("text") or self._model.column_name_to_index("Текст")
        self._col_rating = self._model.column_name_to_index("rating") or self._model.column_name_to_index("Рейтинг")
        self._col_platform = self._model.column_name_to_index("platform") or self._model.column_name_to_index("Платформа")
        self._col_org = self._model.column_name_to_index("organization") or self._model.column_name_to_index("Организация")
        self._col_need_answer = self._model.column_name_to_index("need_answer")

        if self._text_col is not None:
            self.table.setItemDelegateForColumn(self._text_col, TextWrapDelegate(self.table))
            self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        if self._col_need_answer is not None:
            self.table.setItemDelegateForColumn(self._col_need_answer, CheckBoxDelegate(self.table))

        self._date_from.setDate(self._date_from.minimumDate())
        self._date_to.setDate(self._date_to.minimumDate())

        self._proxy.modelReset.connect(self._on_view_changed)
        self._proxy.layoutChanged.connect(self._on_view_changed)
        self._proxy.rowsInserted.connect(lambda *_: self._on_view_changed())
        self._proxy.rowsRemoved.connect(lambda *_: self._on_view_changed())
        self._proxy.dataChanged.connect(lambda *_: self._on_view_changed())

        self._model.dataChanged.connect(self._on_source_data_changed)

        # Заполнение значений фильтров (включая need_answer: «Все» только если колонки нет)
        self._populate_filter_values(df)

        self._apply_column_layout()
        self._on_view_changed()

    def _populate_filter_values(self, df: pd.DataFrame):
        def fill_combo(combo: QComboBox, col_names):
            combo.clear(); combo.addItem("— Все —")
            col = next((c for c in ([col_names] if isinstance(col_names, str) else col_names)
                        if c in df.columns), None)
            if col:
                vals = sorted(set(map(str, df[col].dropna().astype(str))))
                for v in vals:
                    combo.addItem(v)

        fill_combo(self._platform_combo, "platform")
        fill_combo(self._org_combo, "organization")
        fill_combo(self._sentiment_combo, ["sentiment", "sentiment_label", "tone", "Тональность"])

        # need_answer — особая логика: если колонки нет, оставляем только «— Все —»
        self._need_answer_combo.blockSignals(True)
        self._need_answer_combo.clear()
        self._need_answer_combo.addItem("— Все —")
        if "need_answer" in df.columns:
            self._need_answer_combo.addItems(["Требует", "Не требует"])
        self._need_answer_combo.setCurrentIndex(0)
        self._need_answer_combo.blockSignals(False)

    # ---- Фильтры ----
    def _spin_value_or_none(self, sp: QSpinBox) -> Optional[float]:
        v = sp.value()
        return None if v == 0 and sp.specialValueText() == "—" else float(v)

    def _dateedit_to_dt(self, de: QDateEdit) -> Optional[datetime]:
        if de.specialValueText() and de.date() == self._date_from.minimumDate():
            return None
        txt = de.text().strip()
        if not txt:
            return None
        try:
            return datetime.strptime(txt, "%Y-%m-%d")
        except Exception:
            return None

    def apply_filters(self):
        if not self._proxy:
            return
        self._proxy.set_platform_filter(self._platform_combo.currentText())
        self._proxy.set_org_filter(self._org_combo.currentText())
        self._proxy.set_sentiment_filter(self._sentiment_combo.currentText())
        self._proxy.set_rating_range(self._spin_value_or_none(self._rmin),
                                     self._spin_value_or_none(self._rmax))
        self._proxy.set_date_range(self._dateedit_to_dt(self._date_from),
                                   self._dateedit_to_dt(self._date_to))
        self._proxy.set_need_answer_filter(self._need_answer_combo.currentText())
        self._on_view_changed()

    def clear_filters(self):
        if not self._proxy:
            return
        self._platform_combo.setCurrentIndex(0)
        self._org_combo.setCurrentIndex(0)
        self._sentiment_combo.setCurrentIndex(0)
        self._need_answer_combo.setCurrentIndex(0)
        self._rmin.setValue(0); self._rmax.setValue(0)
        self._date_from.setDate(self._date_from.minimumDate())
        self._date_to.setDate(self._date_to.minimumDate())
        self.apply_filters()

    # ---- Текущая выборка и сводка/диаграммы ----
    def _current_filtered_dataframe(self) -> Optional[pd.DataFrame]:
        if not self._proxy:
            return None
        src_model: DataFrameModel = self._proxy.sourceModel()
        df = src_model.get_dataframe()
        rows = []
        for r in range(self._proxy.rowCount()):
            src_row = self._proxy.mapToSource(self._proxy.index(r, 0)).row()
            rows.append(df.iloc[src_row])
        if not rows:
            return pd.DataFrame(columns=df.columns)
        return pd.DataFrame(rows, columns=df.columns)

    def _on_view_changed(self):
        self._update_stats_label()
        self._update_charts()

    def _update_stats_label(self):
        try:
            out_df = self._current_filtered_dataframe()
            if out_df is None or out_df.empty:
                self._stats_label.setText("Отфильтровано: 0 | Средний рейтинг: —")
                return

            if "rating" in out_df.columns:
                ratings = pd.to_numeric(
                    out_df["rating"].astype(str).str.replace(",", ".", regex=False), errors="coerce"
                )
            elif "Рейтинг" in out_df.columns:
                ratings = pd.to_numeric(
                    out_df["Рейтинг"].astype(str).str.replace(",", ".", regex=False), errors="coerce"
                )
            else:
                ratings = pd.Series(dtype=float)

            cnt = len(out_df)
            mean_txt = f"{ratings.mean():.2f}" if ratings.notna().any() else "—"
            self._stats_label.setText(f"Отфильтровано: {cnt} | Средний рейтинг: {mean_txt}")
        except Exception:
            self._stats_label.setText("Отфильтровано: — | Средний рейтинг: —")

    def _update_charts(self):
        df = self._current_filtered_dataframe()
        self._ax_rating.clear()
        if df is not None and not df.empty:
            if "rating" in df.columns:
                r = pd.to_numeric(df["rating"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
            elif "Рейтинг" in df.columns:
                r = pd.to_numeric(df["Рейтинг"].astype(str).str.replace(",", ".", regex=False), errors="coerce")
            else:
                r = pd.Series(dtype=float)
            r = r.dropna()
            if not r.empty:
                r_int = r.round().astype(int)
                counts = r_int.value_counts().sort_index()
                index = pd.Index([1, 2, 3, 4, 5], dtype=int)
                counts = counts.reindex(index, fill_value=0)
                self._ax_rating.bar(counts.index.astype(str), counts.values)
                self._ax_rating.set_xlabel("Рейтинг")
                self._ax_rating.set_ylabel("Кол-во отзывов")
            else:
                self._ax_rating.text(0.5, 0.5, "Нет данных по рейтингу",
                                     ha="center", va="center", transform=self._ax_rating.transAxes)
        else:
            self._ax_rating.text(0.5, 0.5, "Нет данных",
                                 ha="center", va="center", transform=self._ax_rating.transAxes)
        self._canvas_rating.draw()

        self._ax_sent.clear()
        if df is not None and not df.empty:
            sent_col = next((c for c in ["sentiment", "sentiment_label", "tone", "Тональность"] if c in df.columns), None)
            if sent_col:
                counts = df[sent_col].fillna("unknown").astype(str).str.lower().replace({
                    "pos": "positive", "neg": "negative", "neu": "neutral",
                    "положительная": "positive", "отрицательная": "negative", "нейтральная": "neutral"
                }).value_counts()
                if not counts.empty:
                    self._ax_sent.pie(counts.values, labels=counts.index, autopct="%1.0f%%", startangle=90)
                    self._ax_sent.axis("equal")
                else:
                    self._ax_sent.text(0.5, 0.5, "Нет данных по тональности",
                                       ha="center", va="center", transform=self._ax_sent.transAxes)
            else:
                self._ax_sent.text(0.5, 0.5, "Колонка тональности не найдена",
                                   ha="center", va="center", transform=self._ax_sent.transAxes)
        else:
            self._ax_sent.text(0.5, 0.5, "Нет данных",
                               ha="center", va="center", transform=self._ax_sent.transAxes)
        self._canvas_sent.draw()

    # ---- Имя базового файла экспорта ----
    def _export_basename(self) -> str:
        return "filtered_reviews" if self._csv_mode == "reviews" else "filtered_summary"

    # ---- Экспорт в CSV ----
    def export_filtered_csv(self):
        if not self._proxy:
            return
        out_df = self._current_filtered_dataframe()
        if out_df is None:
            return

        out_path = Path(f"{self._export_basename()}.csv")
        try:
            out_df.to_csv(out_path, index=False, quoting=csv.QUOTE_MINIMAL)
            self.statusBar().showMessage(f"Экспортировано в CSV: {out_path} | строк: {len(out_df)}")
            QMessageBox.information(self, "Экспорт завершён", f"CSV файл сохранён:\n{out_path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта CSV", str(e))

    # ---- Экспорт в JSON ----
    def export_filtered_json(self):
        if not self._proxy:
            return
        out_df = self._current_filtered_dataframe()
        if out_df is None:
            return

        # приводим NaN к None, чтобы корректно сериализовалось в JSON
        df_clean = out_df.where(pd.notna(out_df), None)

        out_path = Path(f"{self._export_basename()}.json")
        try:
            records = df_clean.to_dict(orient="records")
            out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            self.statusBar().showMessage(f"Экспортировано в JSON: {out_path} | строк: {len(df_clean)}")
            QMessageBox.information(self, "Экспорт завершён", f"JSON файл сохранён:\n{out_path}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка экспорта JSON", str(e))

    # ==================== ПАЙПЛАЙН СБОРА ДАННЫХ ====================
    def _append_log(self, text: str):
        self._log.appendPlainText(text.rstrip())

    # ---------- Полный сбор ----------
    def run_full_pipeline(self):
        if self._running:
            QMessageBox.information(self, "Уже выполняется", "Пайплайн уже запущен.")
            return

        reply = QMessageBox.question(
            self,
            "Подтверждение",
            "Собрать данные заново?\nБудут запущены парсеры и перезаписаны промежуточные файлы.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        missing = [str(p) for _, p in (self.SCRAPER_SCRIPTS + self.MERGE_SCRIPTS) if not p.exists()]
        if missing:
            QMessageBox.critical(self, "Скрипты не найдены",
                                 "Отсутствуют файлы:\n" + "\n".join(missing))
            return

        self._running = True
        self._scrapers_done = 0
        self._scraper_procs.clear()
        self._append_log("=== Старт парсеров (параллельно) ===")

        for name, path in self.SCRAPER_SCRIPTS:
            proc = QProcess(self)
            proc.setProgram(sys.executable)
            proc.setArguments([str(path)])
            proc.setWorkingDirectory(str(Path(".").resolve()))
            proc.readyReadStandardOutput.connect(
                lambda p=proc, n=name: self._append_log(f"[{n}] {bytes(p.readAllStandardOutput()).decode('utf-8', errors='replace')}")
            )
            proc.readyReadStandardError.connect(
                lambda p=proc, n=name: self._append_log(f"[{n} ERR] {bytes(p.readAllStandardError()).decode('utf-8', errors='replace')}")
            )
            proc.finished.connect(lambda code, status, n=name, p=proc: self._on_scraper_finished(n, code, status))
            self._scraper_procs.append((name, proc))
            proc.start()

    def _on_scraper_finished(self, name: str, code: int, status):
        self._append_log(f"[{name}] Завершён с кодом {code}.")
        self._scrapers_done += 1
        if self._scrapers_done == len(self.SCRAPER_SCRIPTS):
            self._append_log("=== Все парсеры завершены. Запуск слияния… ===")
            self._run_merges_sequentially(0)

    def _run_merges_sequentially(self, idx: int):
        if idx >= len(self.MERGE_SCRIPTS):
            self._append_log("=== Слияние завершено. Перезагрузка таблицы… ===")
            try:
                self.autoload_csv()
                QMessageBox.information(self, "Готово", "Данные обновлены и объединены.")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка обновления", str(e))
            self._running = False
            return

        name, path = self.MERGE_SCRIPTS[idx]
        self._append_log(f"[{name}] Запуск {path}…")
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments([str(path)])
        proc.setWorkingDirectory(str(Path(".").resolve()))
        proc.readyReadStandardOutput.connect(
            lambda p=proc, n=name: self._append_log(f"[{n}] {bytes(p.readAllStandardOutput()).decode('utf-8', errors='replace')}")
        )
        proc.readyReadStandardError.connect(
            lambda p=proc, n=name: self._append_log(f"[{n} ERR] {bytes(p.readAllStandardError()).decode('utf-8', errors='replace')}")
        )
        proc.finished.connect(lambda code, status, i=idx: self._on_merge_finished(code, status, i))
        proc.start()

    def _on_merge_finished(self, code: int, status, idx: int):
        name, _ = self.MERGE_SCRIPTS[idx]
        self._append_log(f"[{name}] Завершён с кодом {code}.")
        self._run_merges_sequentially(idx + 1)

    # ---------- Инкрементальный сбор ----------
    def _discover_incremental_scripts(self) -> List[Tuple[str, Path]]:
        """Находит все *.py в Parsers/Incremental и возвращает список (name, path)."""
        inc_dir = Path("Parsers/Incremental")
        scripts = []
        if inc_dir.exists():
            for p in sorted(inc_dir.glob("*.py")):
                scripts.append((p.stem, p))
        return scripts

    def run_incremental_pipeline(self):
        if self._running:
            QMessageBox.information(self, "Уже выполняется", "Пайплайн уже запущен.")
            return

        reply = QMessageBox.question(
            self,
            "Подтверждение",
            "Собрать только новые данные?\nБудут запущены инкрементальные парсеры, затем новые данные будут добавлены к остальным.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        incr_scrapers = self._discover_incremental_scripts()
        if not incr_scrapers:
            QMessageBox.critical(self, "Скрипты не найдены",
                                 "Не найдено ни одного инкрементального скрипта в Parsers/Incremental/*.py")
            return

        missing = [str(p) for _, p in (incr_scrapers + self.INCR_MERGE_SCRIPTS) if not p.exists()]
        if missing:
            QMessageBox.critical(self, "Скрипты не найдены",
                                 "Отсутствуют файлы:\n" + "\n".join(missing))
            return

        # Стартуем инкрементальные парсеры параллельно
        self._running = True
        self._scrapers_done = 0
        self._scraper_procs.clear()
        self._append_log("=== Инкрементальные парсеры (параллельно) ===")

        # сохраним список текущих инкрементальных для колбэков
        self._incr_scrapers_cache = incr_scrapers  # type: ignore[attr-defined]

        for name, path in incr_scrapers:
            proc = QProcess(self)
            proc.setProgram(sys.executable)
            proc.setArguments([str(path)])
            proc.setWorkingDirectory(str(Path(".").resolve()))
            proc.readyReadStandardOutput.connect(
                lambda p=proc, n=name: self._append_log(f"[INCR {n}] {bytes(p.readAllStandardOutput()).decode('utf-8', errors='replace')}")
            )
            proc.readyReadStandardError.connect(
                lambda p=proc, n=name: self._append_log(f"[INCR {n} ERR] {bytes(p.readAllStandardError()).decode('utf-8', errors='replace')}")
            )
            proc.finished.connect(lambda code, status, n=name: self._on_incr_scraper_finished(n, code, status))
            self._scraper_procs.append((name, proc))
            proc.start()

    def _on_incr_scraper_finished(self, name: str, code: int, status):
        self._append_log(f"[INCR {name}] Завершён с кодом {code}.")
        self._scrapers_done += 1
        # Когда закончатся ВСЕ из Parsers/Incremental — запускаем объединение новых данных
        if self._scrapers_done == len(self._scraper_procs):
            self._append_log("=== Инкрементальные парсеры завершены. Запуск объединения новых данных… ===")
            self._run_incr_merges_sequentially(0)

    def _run_incr_merges_sequentially(self, idx: int):
        if idx >= len(self.INCR_MERGE_SCRIPTS):
            self._append_log("=== Инкрементальное слияние завершено. Перезагрузка таблицы… ===")
            try:
                self.autoload_csv()
                QMessageBox.information(self, "Готово", "Новые данные собраны и объединены.")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка обновления", str(e))
            self._running = False
            return

        name, path = self.INCR_MERGE_SCRIPTS[idx]
        self._append_log(f"[{name}] Запуск {path}…")
        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments([str(path)])
        proc.setWorkingDirectory(str(Path(".").resolve()))
        proc.readyReadStandardOutput.connect(
            lambda p=proc, n=name: self._append_log(f"[{n}] {bytes(p.readAllStandardOutput()).decode('utf-8', errors='replace')}")
        )
        proc.readyReadStandardError.connect(
            lambda p=proc, n=name: self._append_log(f"[{n} ERR] {bytes(p.readAllStandardError()).decode('utf-8', errors='replace')}")
        )
        proc.finished.connect(lambda code, status, i=idx: self._on_incr_merge_finished(code, status, i))
        proc.start()

    def _on_incr_merge_finished(self, code: int, status, idx: int):
        name, _ = self.INCR_MERGE_SCRIPTS[idx]
        self._append_log(f"[{name}] Завершён с кодом {code}.")
        self._run_incr_merges_sequentially(idx + 1)

    # ---- Макет колонок ----
    def _apply_column_layout(self):
        if self._model is None:
            return
        viewport_w = max(360, self.table.viewport().width())

        fixed_sum = 0
        if self._col_rating is not None:
            self.table.setColumnWidth(self._col_rating, self._RATING_W); fixed_sum += self._RATING_W
        if self._col_platform is not None:
            self.table.setColumnWidth(self._col_platform, self._PLATFORM_W); fixed_sum += self._PLATFORM_W
        if self._col_need_answer is not None:
            self.table.setColumnWidth(self._col_need_answer, self._NEED_ANSWER_W); fixed_sum += self._NEED_ANSWER_W

        cols = self._model.columnCount()
        remaining = max(200, viewport_w - fixed_sum)

        if self._col_org is not None:
            org_w = int(remaining * self._org_col_ratio)
            org_w = max(self._ORG_MIN, min(self._ORG_MAX, org_w))
            self.table.setColumnWidth(self._col_org, org_w)
        else:
            org_w = 0

        if self._text_col is not None:
            text_w = int(remaining * self._text_col_ratio)
            self.table.setColumnWidth(self._text_col, text_w)
        else:
            text_w = 0

        other_cols = [c for c in range(cols) if c not in {self._text_col, self._col_rating, self._col_platform, self._col_org, self._col_need_answer}]
        remaining2 = max(80, remaining - org_w - text_w)
        if other_cols:
            per = max(90, remaining2 // len(other_cols))
            for c in other_cols:
                self.table.setColumnWidth(c, per)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_filters_width()
        self._apply_column_layout()

    def _toggle_expand_reviews(self):
        self._expanded = not self._expanded
        if self._expanded:
            self._filters_group.hide()
            self._charts_group.hide()
            self._expand_btn.setText("Свернуть список отзывов")
            self._expand_btn.setStyleSheet(self._expand_style_expanded)
        else:
            self._filters_group.show()
            self._charts_group.show()
            self._expand_btn.setText("Развернуть список отзывов")
            self._expand_btn.setStyleSheet(self._expand_style_collapsed)
            self._adjust_filters_width()
        self._apply_column_layout()

    # ---- Автосейв при изменении need_answer ----
    def _on_source_data_changed(self, topLeft: QModelIndex, bottomRight: QModelIndex, roles: List[int] = []):
        if self._csv_mode != "reviews":
            return
        if self._model is None or self._current_csv_path is None:
            return
        if self._col_need_answer is None:
            return
        if topLeft.column() <= self._col_need_answer <= bottomRight.column():
            try:
                df_to_save = self._model.get_dataframe().copy()
                df_to_save.to_csv(self._current_csv_path, index=False, quoting=csv.QUOTE_MINIMAL)
                self.statusBar().showMessage(f"Изменения сохранены: {self._current_csv_path}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка сохранения", str(e))


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
