# Web Parser

## Запуск

Команды PowerShell из каталога проекта:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install firefox
.\.venv\Scripts\python.exe main.py
```

Активация виртуального окружения не требуется. Установка PySide6 включает Qt:
[официальная инструкция](https://doc.qt.io/qtforpython-6.10/gettingstarted.html).
После установки можно запускать приложение двойным щелчком по `start.cmd`.

## Проверка интерфейса

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Прокси

Включите общий прокси в приложении и укажите адрес с протоколом, например `http://host:port` или `socks5://host:port`; логин и пароль задаются отдельно. Для SOCKS5 с авторизацией приложение поднимает временный локальный переходник на `127.0.0.1`, поскольку Playwright не передаёт SOCKS5-учётные данные браузеру. Переходник работает только во время сеанса браузера.
