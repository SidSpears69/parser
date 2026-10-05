# Web Parser

## Запуск

Команды PowerShell из каталога проекта:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

Активация виртуального окружения не требуется. Установка PySide6 включает Qt:
[официальная инструкция](https://doc.qt.io/qtforpython-6.10/gettingstarted.html).
После установки можно запускать приложение двойным щелчком по `start.cmd`.

## Проверка интерфейса

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
