"""Shared light theme for the desktop widgets."""

STYLESHEET = """
QWidget {
    color: #243246;
    font-family: "Segoe UI";
    font-size: 13px;
}
QMainWindow, QWidget#root { background: #f3f5f9; }
QLabel { background: transparent; }
QLabel#appTitle { font-size: 26px; font-weight: 700; color: #14263d; }
QLabel#subtitle, QLabel#hint { color: #637389; }
QLabel#sectionTitle { font-size: 14px; font-weight: 600; }
QLabel#badge {
    color: #405b86; background: #e7edf8;
    border-radius: 12px; padding: 5px 12px;
}
QLabel#state { color: #52657c; background: #edf1f6; border-radius: 5px; padding: 5px 10px; }
QTabWidget::pane { border: 1px solid #dce3ed; background: white; border-radius: 8px; }
QTabBar::tab {
    background: transparent; color: #607087; padding: 13px 22px;
    border-bottom: 3px solid transparent; font-weight: 600;
}
QTabBar::tab:selected { color: #245fd2; border-bottom: 3px solid #245fd2; }
QTabBar::tab:hover:!selected { background: #eaf0f8; }
QWidget#sitePage, QScrollArea, QScrollArea > QWidget > QWidget { background: white; }
QScrollArea { border: none; }
QFrame#section { background: white; border: 1px solid #e0e6ef; border-radius: 8px; }
QLineEdit, QSpinBox, QTimeEdit, QComboBox {
    background: #fbfcfe; border: 1px solid #cdd7e4; border-radius: 5px;
    min-height: 22px; padding: 5px 8px; selection-background-color: #245fd2;
}
QLineEdit:focus, QSpinBox:focus, QTimeEdit:focus, QComboBox:focus { border: 1px solid #245fd2; }
QComboBox { padding-right: 20px; }
QComboBox::drop-down {
    subcontrol-origin: padding; subcontrol-position: top right;
    width: 27px; background: #e8eff9; border-left: 1px solid #b9c9dd;
    border-top-right-radius: 5px; border-bottom-right-radius: 5px;
}
QComboBox::drop-down:hover { background: #d3e1f4; }
QComboBox QAbstractItemView { background: white; selection-background-color: #e8efff; selection-color: #243246; }
QSpinBox::up-button, QSpinBox::down-button,
QTimeEdit::up-button, QTimeEdit::down-button {
    subcontrol-origin: border; width: 24px; background: #e8eff9;
    border-left: 1px solid #b9c9dd;
}
QSpinBox::up-button, QTimeEdit::up-button {
    subcontrol-position: top right; border-top-right-radius: 4px;
    border-bottom: 1px solid #c6d3e4;
}
QSpinBox::down-button, QTimeEdit::down-button {
    subcontrol-position: bottom right; border-bottom-right-radius: 4px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QTimeEdit::up-button:hover, QTimeEdit::down-button:hover { background: #d3e1f4; }
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed,
QTimeEdit::up-button:pressed, QTimeEdit::down-button:pressed { background: #bcd2ef; }
QPushButton {
    background: #ffffff; border: 1px solid #aebed4; border-bottom: 2px solid #8fa6c4;
    border-radius: 6px; padding: 7px 13px; min-height: 20px; font-weight: 600;
}
QPushButton:hover { background: #eef4ff; border-color: #5d82ba; }
QPushButton:pressed { background: #dbe8ff; border-color: #245fd2; }
QPushButton:focus { border: 2px solid #245fd2; padding: 6px 12px; }
QPushButton#primary { background: #245fd2; border-color: #245fd2; color: white; }
QPushButton#primary:hover { background: #1b50b6; }
QPushButton#primary:pressed { background: #143f96; }
QPushButton#loadLinks, QPushButton#launchBrowser,
QPushButton#saveConfig { background: #245fd2; border-color: #245fd2; color: white; }
QPushButton#loadLinks:hover, QPushButton#launchBrowser:hover,
QPushButton#saveConfig:hover { background: #1b50b6; border-color: #1b50b6; }
QPushButton#loadLinks:pressed, QPushButton#launchBrowser:pressed,
QPushButton#saveConfig:pressed { background: #143f96; border-color: #143f96; }
QPushButton:disabled { background: #f2f4f7; color: #98a3b2; border-color: #e0e5ec; }
QPushButton#primary:disabled { background: #e9eef8; color: #8094b9; border-color: #e0e5ec; }
QPushButton#loadLinks:disabled, QPushButton#launchBrowser:disabled,
QPushButton#saveConfig:disabled { background: #e8eef9; color: #8297bd; border-color: #d5dfef; }
QPlainTextEdit {
    background: #f8fafc; border: 1px solid #e0e6ef; border-radius: 6px;
    padding: 10px; font-family: "Consolas"; font-size: 12px;
    selection-background-color: #245fd2;
}
QTableWidget {
    background: #fbfcfe; alternate-background-color: #f3f7fd;
    border: 1px solid #cdd7e4; border-radius: 6px;
    gridline-color: #e0e6ef; selection-background-color: #dce9ff;
    selection-color: #243246;
}
QHeaderView::section {
    background: #e8eff9; color: #334a68; border: none;
    border-bottom: 1px solid #c4d2e4; padding: 7px 8px; font-weight: 600;
}
QScrollBar:vertical {
    background: #edf1f6; width: 14px; margin: 1px;
    border: 1px solid #d4dce8; border-radius: 7px;
}
QScrollBar::handle:vertical {
    background: #7891b3; min-height: 44px; margin: 2px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover { background: #52729d; }
QScrollBar::handle:vertical:pressed { background: #245fd2; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    background: #dfe7f2; height: 15px; border: none;
}
QScrollBar::add-line:vertical:hover, QScrollBar::sub-line:vertical:hover { background: #c8d7eb; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: #f6f8fb; }
QScrollBar:horizontal {
    background: #edf1f6; height: 14px; margin: 1px;
    border: 1px solid #d4dce8; border-radius: 7px;
}
QScrollBar::handle:horizontal {
    background: #7891b3; min-width: 44px; margin: 2px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal:hover { background: #52729d; }
QScrollBar::handle:horizontal:pressed { background: #245fd2; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    background: #dfe7f2; width: 15px; border: none;
}
QScrollBar::add-line:horizontal:hover, QScrollBar::sub-line:horizontal:hover { background: #c8d7eb; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: #f6f8fb; }
QCheckBox { spacing: 8px; }
QStatusBar { color: #637389; background: #f3f5f9; }
QStatusBar::item { border: none; }
QToolTip { background: #243246; color: white; border: none; padding: 6px; }
"""
