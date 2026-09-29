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
QComboBox QAbstractItemView { background: white; selection-background-color: #e8efff; selection-color: #243246; }
QPushButton {
    background: white; border: 1px solid #cdd7e4; border-radius: 5px;
    padding: 7px 13px; min-height: 18px; font-weight: 600;
}
QPushButton:hover { background: #f0f4fb; border-color: #9bb3d7; }
QPushButton:pressed { background: #e2eafa; }
QPushButton#primary { background: #245fd2; border-color: #245fd2; color: white; }
QPushButton#primary:hover { background: #1b50b6; }
QPushButton:disabled { background: #f2f4f7; color: #98a3b2; border-color: #e0e5ec; }
QPushButton#primary:disabled { background: #e9eef8; color: #8094b9; border-color: #e0e5ec; }
QPlainTextEdit {
    background: #f8fafc; border: 1px solid #e0e6ef; border-radius: 6px;
    padding: 10px; font-family: "Consolas"; font-size: 12px;
    selection-background-color: #245fd2;
}
QCheckBox { spacing: 8px; }
QStatusBar { color: #637389; background: #f3f5f9; }
QStatusBar::item { border: none; }
QToolTip { background: #243246; color: white; border: none; padding: 6px; }
"""
