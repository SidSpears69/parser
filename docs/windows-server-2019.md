# Окружение парсера на Windows Server 2019

Приложение работает под Python 3.14 x64 в `.venv`. Обход страниц запускает установленный Яндекс.Браузер, Chrome или Edge через драйвер, входящий в пакет Playwright. Для Firefox используется отдельная сборка Playwright. Selenium и YandexDriver остались в проекте от раннего этапа, но текущая кнопка «Запустить» их не вызывает: вручную подбирать WebDriver для неё не нужно. [Поддерживаемые системы Playwright](https://playwright.dev/python/docs/intro), [браузеры Playwright](https://playwright.dev/python/docs/browsers).

## Установка

Запускайте команды из корня проекта под той же учётной записью Windows, которая затем будет запускать приложение. Браузер работает с видимым окном, поэтому нужна интерактивная пользовательская сессия. Firefox Playwright устанавливается в профиль текущего пользователя, обычно `%LOCALAPPDATA%\ms-playwright`.

1. Установите Python 3.14 x64. На Windows Server 2019 Python рекомендует [MSI вариант менеджера установки](https://docs.python.org/3.14/using/windows.html#advanced-installation); если Python уже установлен, достаточно знать полный путь к его `python.exe`. Команда `py` не обязательна.
2. При ошибке об отсутствующем `msvcp140_1.dll` установите [актуальный Microsoft Visual C++ Redistributable x64](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist/). Не копируйте отдельную DLL вручную.
3. Установите нужный браузер под этой учётной записью с [сайта Яндекса](https://browser.yandex.ru/c/browser-windows), [сайта Google](https://www.google.com/chrome/) или [сайта Microsoft](https://www.microsoft.com/edge). Для Яндекс.Браузера, Chrome и Edge приложение использует существующий `browser.exe`, `chrome.exe` или `msedge.exe`. Обычный Firefox не заменяет сборку Playwright.
4. Выполните подготовку и проверку (путь к Python нужен только при создании новой `.venv`):

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_windows_server.ps1 -PythonExe "C:\Python314\python.exe" -Browser "Яндекс Браузер" -TlsUrl "https://sail-master.ru/"
```

Если `.venv` уже создана с Python 3.14 x64, параметр `-PythonExe` можно опустить. Скрипт устанавливает версии из `requirements.txt`, устанавливает сборку Firefox Playwright и запускает проверку. Он не удаляет существующее окружение и не меняет системные параметры TLS. Завершение `READY` означает, что локальные проверки прошли.

## Повторная проверка без установки

```powershell
.\.venv\Scripts\python.exe -m scripts.check_windows_server --browser "Яндекс Браузер" --tls-url "https://sail-master.ru/"
```

Проверка показывает версию Python и разрядность, версии пакетов, доступность VC++ и Qt, найденные браузеры, запуск драйвера и выбранного браузера с временным профилем, а также HTTPS с проверкой сертификата. Она не читает конфигурацию и пароли. Для проверки другого источника передайте его HTTPS-адрес **без токена и параметров URL**. Браузерный SOCKS5-прокси и доступ к маркетплейсам проверяются отдельным прогоном в приложении.

## Если проверка не прошла

| Сообщение | Действие |
| --- | --- |
| `Python packages` | Повторно запустите установочный скрипт из актуальной версии проекта. |
| `VC++ runtime` | Установите Redistributable x64 по ссылке выше и повторите проверку. |
| `Playwright Firefox` | Запустите `.\.venv\Scripts\python.exe -m playwright install firefox` под учётной записью приложения. |
| `Browser launch` | Проверьте путь к выбранному браузеру, интерактивную сессию и полный текст ошибки запуска. Проверка использует временный профиль и не трогает профиль парсера. |
| `TLS` с ошибкой сертификата | Проверьте дату и время сервера, обновления Windows и доверенные корневые сертификаты. Не отключайте проверку сертификатов. |
| `TLS` с DNS или тайм-аутом | Проверьте сетевой маршрут, DNS и системный прокси для Python. Это отдельная проблема от сертификата. |

HTTP 401 или 403 в строке `OK: TLS` означает, что HTTPS-соединение и проверка сертификата прошли; доступ к конкретной странице проверяется отдельно. На Windows Server 2019 TLS 1.2 [включён по умолчанию](https://learn.microsoft.com/en-us/troubleshoot/azure/entra/entra-id/ad-dmn-services/enable-support-tls-environment); менять реестр заранее не требуется.
