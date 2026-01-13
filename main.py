import re
import time
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Playwright, sync_playwright, TimeoutError as PlaywrightTimeoutError, expect


# -------------------- МОДЕЛИ --------------------

@dataclass(frozen=True)
class Vacancy:
    vacancy_id: str
    title: str
    watchers_text: str
    watchers_count: int | None
    description: Optional[str] = None  # Текст вакансии


def _parse_int(text: str) -> int | None:
    if not text:
        return None
    text = text.replace("\xa0", " ")
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else None


# -------------------- SERP: ПРОГРУЗКА --------------------

def scroll_until_all_loaded(page, pause_ms: int = 900, max_scrolls: int = 50, stable_rounds_needed: int = 3) -> None:
    cards = page.locator('[data-qa="vacancy-serp__vacancy"]')
    stable = 0
    prev = cards.count()

    print(f"Начинаю прогрузку скроллом. Сейчас карточек: {prev}")

    for i in range(1, max_scrolls + 1):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(pause_ms)
        page.wait_for_timeout(int(pause_ms * 0.6))

        cur = cards.count()
        if cur > prev:
            print(f"  Скролл {i}: +{cur - prev} (стало {cur})")
            prev = cur
            stable = 0
        else:
            stable += 1
            print(f"  Скролл {i}: новых нет (стало {cur}), стабильность {stable}/{stable_rounds_needed}")
            if stable >= stable_rounds_needed:
                break

    print(f"Прогрузка завершена. Итого карточек: {prev}")


# -------------------- SERP: ПАРСИНГ --------------------

def collect_vacancies_for_apply(page, limit: int = 10) -> list[Vacancy]:
    page.wait_for_selector('[data-qa="vacancy-serp__vacancy"]', timeout=30_000)
    cards = page.locator('[data-qa="vacancy-serp__vacancy"]')

    result: list[Vacancy] = []
    for i in range(cards.count()):
        card = cards.nth(i)

        # есть кнопка "Откликнуться" в карточке?
        resp = card.locator('[data-qa="vacancy-serp__vacancy_response"]').first
        if resp.count() == 0:
            continue

        title = card.locator('[data-qa="serp-item__title-text"]').first.inner_text().strip()
        href = card.locator('a[data-qa="serp-item__title"]').first.get_attribute("href") or ""
        m = re.search(r"/vacancy/(\d+)", href)
        if not m:
            continue
        vacancy_id = m.group(1)

        watchers_loc = card.locator('span:has-text("Сейчас смотрят")').first
        watchers_text = watchers_loc.inner_text().strip() if watchers_loc.count() else "Сейчас смотрят —"
        watchers_count = _parse_int(watchers_text)

        result.append(Vacancy(vacancy_id=vacancy_id, title=title, watchers_text=watchers_text, watchers_count=watchers_count))
        if len(result) >= limit:
            break

    return result


def find_card_by_vacancy_id(page, vacancy_id: str):
    return page.locator(
        '[data-qa="vacancy-serp__vacancy"]',
        has=page.locator(f'a[data-qa="serp-item__title"][href*="/vacancy/{vacancy_id}"]'),
    ).first


# -------------------- ТЕСТ/ВОПРОСЫ (РЕДИРЕКТ) --------------------

def is_test_page(page) -> bool:
    """
    Детект "вопросов работодателя":
      - data-qa="title-container"
      - data-qa="title-description" содержит "Для отклика необходимо ответить..."
    """
    container = page.locator('[data-qa="title-container"]').first
    if container.count() == 0:
        return False

    desc = page.locator('[data-qa="title-description"]:has-text("Для отклика необходимо ответить")').first
    return desc.count() > 0


def safe_go_back_to_serp(page, fallback_url: str) -> None:
    """
    ВАЖНО: networkidle на HH часто не наступает, поэтому ждём выдачу селектором.
    """
    try:
        page.go_back(wait_until="domcontentloaded")
    except Exception:
        page.goto(fallback_url, wait_until="domcontentloaded")

    # ждём возвращение выдачи
    page.wait_for_selector('[data-qa="vacancy-serp__vacancy"]', timeout=15_000)


# -------------------- ИЗВЛЕЧЕНИЕ ТЕКСТА ВАКАНСИИ --------------------

def extract_vacancy_text(page, vacancy_id: str) -> Optional[str]:
    """
    Открывает страницу вакансии и извлекает её описание.
    Возвращает текст вакансии или None при ошибке.
    """
    original_url = page.url
    
    try:
        vacancy_url = f"https://hh.ru/vacancy/{vacancy_id}"
        page.goto(vacancy_url, wait_until="domcontentloaded", timeout=15_000)
        
        # Ждём загрузки описания вакансии
        description_selector = '[data-qa="vacancy-description"]'
        page.wait_for_selector(description_selector, timeout=10_000)
        
        # Извлекаем текст описания
        description = page.locator(description_selector).first
        if description.count() > 0:
            text = description.inner_text().strip()
            return text
        
        return None
    except Exception as e:
        print(f"    ⚠️ Ошибка при извлечении текста вакансии: {e}")
        return None
    finally:
        # Возвращаемся обратно на страницу поиска
        try:
            page.goto(original_url, wait_until="domcontentloaded", timeout=10_000)
            page.wait_for_selector('[data-qa="vacancy-serp__vacancy"]', timeout=10_000)
        except Exception:
            pass


# -------------------- МОДАЛКА: ОБЯЗАТЕЛЬНОЕ СОПРОВОДИТЕЛЬНОЕ --------------------

def is_cover_letter_required_modal(page) -> bool:
    dlg = page.locator('[role="dialog"]').first
    if dlg.count() == 0:
        return False

    required_hint = dlg.locator('[data-qa="form-helper-description"]:has-text("Сопроводительное письмо обязательное")').first
    letter_input = dlg.locator('[data-qa="vacancy-response-popup-form-letter-input"]').first
    return required_hint.count() > 0 and letter_input.count() > 0


def fill_and_submit_cover_letter(page, cover_letter_text: str, timeout_ms: int = 10_000) -> bool:
    """
    Заполняет сопроводительное письмо в модалке и отправляет отклик.
    Возвращает True если отклик успешно отправлен.
    """
    try:
        # Ждём появления модалки
        dlg = page.locator('[role="dialog"]').first
        dlg.wait_for(state="visible", timeout=timeout_ms)
        
        # Находим поле для сопроводительного письма
        letter_input = dlg.locator('[data-qa="vacancy-response-popup-form-letter-input"]').first
        letter_input.wait_for(state="visible", timeout=timeout_ms)
        
        # Очищаем поле и заполняем
        letter_input.click()
        letter_input.fill(cover_letter_text)
        page.wait_for_timeout(500)  # Небольшая задержка для обновления UI
        
        # Ищем кнопку отправки
        submit_btn = dlg.locator('button[type="submit"]').first
        if submit_btn.count() == 0:
            # Альтернативный селектор
            submit_btn = dlg.locator('button:has-text("Откликнуться")').first
        
        if submit_btn.count() == 0:
            print("    ⚠️ Кнопка отправки не найдена")
            return False
        
        # Отправляем отклик
        submit_btn.click()
        
        # Ждём подтверждения отправки
        page.wait_for_timeout(2000)
        
        # Проверяем успешную отправку
        success_indicator = page.locator('#dialog-description:has-text("Отклик отправлен")').first
        if success_indicator.count() > 0:
            return True
        
        # Альтернативная проверка - модалка должна закрыться
        try:
            dlg.wait_for(state="hidden", timeout=3000)
            return True
        except Exception:
            pass
        
        return False
    except Exception as e:
        print(f"    ⚠️ Ошибка при заполнении сопроводительного письма: {e}")
        return False


def close_response_modal_if_open(page) -> None:
    close_btn = page.locator('[data-qa="response-popup-close"]').first
    if close_btn.count():
        close_btn.click()
        try:
            page.locator('[role="dialog"]').first.wait_for(state="hidden", timeout=5000)
        except Exception:
            pass


# -------------------- СКРЫТИЕ ВАКАНСИИ --------------------

def hide_vacancy_card(page, card, *, timeout_ms: int = 5000) -> bool:
    """
    1) В карточке: button[data-qa="vacancy__blacklist-show-add"]
    2) В меню:    button[data-qa="vacancy__blacklist-menu-add-vacancy"]
    """
    hide_icon = card.locator('button[data-qa="vacancy__blacklist-show-add"]').first
    if hide_icon.count() == 0:
        return False

    card.scroll_into_view_if_needed(timeout=timeout_ms)

    try:
        hide_icon.click(timeout=timeout_ms)
    except Exception:
        return False

    menu_item = page.locator('button[data-qa="vacancy__blacklist-menu-add-vacancy"]').first
    try:
        menu_item.wait_for(state="visible", timeout=timeout_ms)
        menu_item.click(timeout=timeout_ms)
    except Exception:
        return False

    # иногда карточка реально удаляется из DOM
    try:
        card.wait_for(state="detached", timeout=3000)
    except Exception:
        pass

    return True


# -------------------- ОТКЛИК "В ОДИН КЛИК" --------------------

def click_apply_on_card(page, card, cover_letter_text: Optional[str] = None, *, poll_timeout_sec: float = 6.0) -> str:
    """
    Отправляет отклик на вакансию. Если требуется сопроводительное письмо и оно предоставлено,
    заполняет и отправляет его.
    
    Возвращаем:
      - sent - отклик успешно отправлен
      - test_required - требуется тест/вопросы
      - cover_letter_required - требуется сопроводительное (но не было предоставлено)
      - cover_letter_filled - модалка открыта, письмо заполнено
      - extra_steps - нужны доп.шаги
      - unknown - неизвестный статус
    """
    original_url = page.url
    card.scroll_into_view_if_needed(timeout=10_000)

    apply_btn = card.locator('[data-qa="vacancy-serp__vacancy_response"]').first
    if apply_btn.count() == 0:
        return "no_apply_button"

    apply_btn.click()

    deadline = time.time() + poll_timeout_sec
    while time.time() < deadline:
        # 1) snackbar успеха
        if page.locator('#dialog-description:has-text("Отклик отправлен")').count():
            return "sent"

        # 2) модалка с обязательным сопроводительным
        if is_cover_letter_required_modal(page):
            if cover_letter_text:
                # Пытаемся заполнить и отправить
                if fill_and_submit_cover_letter(page, cover_letter_text):
                    # Проверяем успешную отправку
                    page.wait_for_timeout(1000)
                    if page.locator('#dialog-description:has-text("Отклик отправлен")').count():
                        return "sent"
                    return "cover_letter_filled"
                else:
                    close_response_modal_if_open(page)
                    return "cover_letter_required"
            else:
                close_response_modal_if_open(page)
                return "cover_letter_required"

        # 3) редирект на доп.страницу (вопросы/тест)
        if page.url != original_url:
            if is_test_page(page):
                safe_go_back_to_serp(page, fallback_url=original_url)
                return "test_required"

            safe_go_back_to_serp(page, fallback_url=original_url)
            return "extra_steps"

        page.wait_for_timeout(200)

    return "unknown"


# -------------------- ЛОГИН --------------------

def login_with_phone(page, phone_number: str, sms_code: Optional[str] = None) -> bool:
    """
    Выполняет вход на hh.ru через телефон и SMS.
    Если sms_code не предоставлен, ждёт ввода от пользователя.
    Возвращает True при успешном входе.
    """
    try:
        page.goto("https://hh.ru/", wait_until="domcontentloaded")
        
        # Кликаем "Войти"
        login_link = page.get_by_role("link", name="Войти").first
        if login_link.count() == 0:
            # Возможно, уже залогинены
            if page.locator('[data-qa="mainmenu_applicantProfile"]').count() > 0:
                print("✅ Уже выполнен вход")
                return True
            return False
        
        login_link.click()
        page.wait_for_timeout(1000)
        
        # Кликаем кнопку "Войти" в модалке
        login_btn = page.get_by_role("button", name="Войти").first
        if login_btn.count() > 0:
            login_btn.click()
            page.wait_for_timeout(1000)
        
        # Вводим номер телефона
        phone_input = page.locator('input[type="tel"]').first
        if phone_input.count() == 0:
            phone_input = page.get_by_role("textbox").nth(1)
        
        if phone_input.count() == 0:
            print("⚠️ Поле ввода телефона не найдено")
            return False
        
        phone_input.click()
        phone_input.fill(phone_number)
        page.wait_for_timeout(500)
        
        # Нажимаем "Дальше"
        next_btn = page.get_by_role("button", name="Дальше").first
        if next_btn.count() == 0:
            next_btn = page.locator('button:has-text("Дальше")').first
        
        if next_btn.count() == 0:
            print("⚠️ Кнопка 'Дальше' не найдена")
            return False
        
        next_btn.click()
        page.wait_for_timeout(2000)
        
        # Вводим код из SMS
        if not sms_code:
            sms_code = input("Введите код из SMS: ")
        
        code_input = page.get_by_role("textbox", name="Введите код").first
        if code_input.count() == 0:
            code_input = page.locator('input[type="text"]').first
        
        if code_input.count() == 0:
            print("⚠️ Поле ввода кода не найдено")
            return False
        
        code_input.click()
        code_input.fill(sms_code)
        page.wait_for_timeout(2000)
        
        # Проверяем успешный вход
        page.wait_for_timeout(3000)
        if page.locator('[data-qa="mainmenu_applicantProfile"]').count() > 0:
            print("✅ Вход выполнен успешно")
            return True
        
        return False
    except Exception as e:
        print(f"⚠️ Ошибка при входе: {e}")
        return False


# -------------------- ПОИСК --------------------

def search_vacancies(page, search_query: str) -> bool:
    """
    Выполняет поиск вакансий по запросу.
    """
    try:
        page.goto("https://hh.ru/", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        
        # Находим поле поиска
        search_input = page.get_by_role("textbox", name="Профессия, должность или компания").first
        if search_input.count() == 0:
            search_input = page.locator('input[data-qa="search-input"]').first
        
        if search_input.count() == 0:
            print("⚠️ Поле поиска не найдено")
            return False
        
        search_input.click()
        search_input.fill(search_query)
        page.wait_for_timeout(500)
        
        # Нажимаем кнопку поиска
        search_btn = page.get_by_role("button", name="Найти").first
        if search_btn.count() == 0:
            search_btn = page.locator('button[data-qa="search-button"]').first
        
        if search_btn.count() == 0:
            # Пробуем Enter
            search_input.press("Enter")
        else:
            search_btn.click()
        
        # Ждём загрузки результатов
        expect(page.locator('[data-qa="vacancy-serp__vacancy"]').first).to_be_visible(timeout=30_000)
        print(f"✅ Поиск выполнен: найдены вакансии")
        return True
    except Exception as e:
        print(f"⚠️ Ошибка при поиске: {e}")
        return False


# -------------------- ГЕНЕРАЦИЯ СОПРОВОДИТЕЛЬНОГО ПИСЬМА --------------------

def generate_cover_letter(vacancy_title: str, vacancy_description: Optional[str] = None, 
                         custom_template: Optional[str] = None) -> str:
    """
    Генерирует сопроводительное письмо на основе вакансии.
    """
    if custom_template:
        return custom_template
    
    # Базовый шаблон
    template = f"""Здравствуйте!

Меня заинтересовала вакансия "{vacancy_title}".

Готов обсудить детали и ответить на ваши вопросы.

С уважением"""
    
    return template


# -------------------- MAIN --------------------

def run(playwright: Playwright, 
        phone_number: Optional[str] = None,
        sms_code: Optional[str] = None,
        search_query: Optional[str] = None,
        cover_letter_template: Optional[str] = None,
        extract_vacancy_texts: bool = False,
        limit: int = 10) -> None:
    """
    Основная функция запуска скрипта.
    
    Args:
        phone_number: Номер телефона для входа (если None, будет запрошен)
        sms_code: Код из SMS (если None, будет запрошен)
        search_query: Поисковый запрос (если None, будет запрошен)
        cover_letter_template: Шаблон сопроводительного письма
        extract_vacancy_texts: Извлекать ли текст вакансий
        limit: Максимальное количество вакансий для отклика
    """
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    # Логин
    if not phone_number:
        phone_number = input("Введите номер телефона (например, +79991234567): ")
    
    if not login_with_phone(page, phone_number, sms_code):
        print("❌ Не удалось выполнить вход")
        context.close()
        browser.close()
        return

    # Поиск
    if not search_query:
        search_query = input("Введите поисковый запрос (например, React Next.js разработчик): ")
    
    if not search_vacancies(page, search_query):
        print("❌ Не удалось выполнить поиск")
        context.close()
        browser.close()
        return

    # Полная прогрузка
    scroll_until_all_loaded(page)

    # Сбор вакансий
    vacancies = collect_vacancies_for_apply(page, limit=limit)
    print(f"\n📋 Найдено вакансий для отклика: {len(vacancies)}")
    
    # Извлечение текстов вакансий (если нужно)
    if extract_vacancy_texts:
        print("\n📄 Извлекаю тексты вакансий...")
        updated_vacancies = []
        for v in vacancies:
            description = extract_vacancy_text(page, v.vacancy_id)
            updated_vac = Vacancy(
                vacancy_id=v.vacancy_id,
                title=v.title,
                watchers_text=v.watchers_text,
                watchers_count=v.watchers_count,
                description=description
            )
            updated_vacancies.append(updated_vac)
        vacancies = updated_vacancies

    # План откликов
    print("\n📝 План отклика (только вакансии с кнопкой «Откликнуться»):")
    for idx, v in enumerate(vacancies, start=1):
        w = v.watchers_count if v.watchers_count is not None else "—"
        print(f"{idx:02d}. {v.title} | сейчас смотрят: {w} | vacancy_id={v.vacancy_id}")

    # Отклики
    for idx, v in enumerate(vacancies, start=1):
        w = v.watchers_count if v.watchers_count is not None else "—"
        print(f"\n[{idx}/{len(vacancies)}] Отклик на вакансию: {v.title}")
        print(f"    Сейчас ее просматривает: {w}")

        card = find_card_by_vacancy_id(page, v.vacancy_id)
        if card.count() == 0:
            print("    ⚠️ Карточка не найдена (выдача могла обновиться). Пропускаю.")
            continue

        # Генерируем сопроводительное письмо
        cover_letter = generate_cover_letter(v.title, v.description, cover_letter_template)

        status = click_apply_on_card(page, card, cover_letter_text=cover_letter)

        if status == "sent":
            print("    ✅ Отклик отправлен.")
            continue
        elif status == "cover_letter_filled":
            print("    ✅ Отклик отправлен с сопроводительным письмом.")
            continue

        # Иначе — скрываем вакансию (чтобы не маячила)
        card_again = find_card_by_vacancy_id(page, v.vacancy_id)
        if card_again.count() > 0:
            hidden = hide_vacancy_card(page, card_again)
            print("    🫥 Вакансия скрыта." if hidden else "    ⚠️ Не удалось скрыть вакансию.")
        else:
            print("    ⚠️ Карточку для скрытия не нашёл.")

        if status == "test_required":
            print("    🧠 Требуется тест/вопросы работодателя — пропуск.")
        elif status == "cover_letter_required":
            print("    ✍️ Обязательное сопроводительное — пропуск (письмо не было заполнено автоматически).")
        elif status == "extra_steps":
            print("    ℹ️ Нужны доп.шаги — пропуск.")
        else:
            print(f"    ❓ Статус: {status} — пропуск.")

    print("\n✅ Работа завершена!")
    context.close()
    browser.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Автоматизация откликов на hh.ru")
    parser.add_argument("--phone", type=str, help="Номер телефона для входа (например, +79991234567)")
    parser.add_argument("--sms-code", type=str, help="Код из SMS (если не указан, будет запрошен)")
    parser.add_argument("--search", type=str, help="Поисковый запрос (например, 'React Next.js разработчик')")
    parser.add_argument("--search-role", type=str, choices=["react_nextjs", "qa_lead", "backend"], 
                       help="Использовать готовый запрос для роли (react_nextjs, qa_lead, backend)")
    parser.add_argument("--cover-letter", type=str, help="Шаблон сопроводительного письма (файл или текст)")
    parser.add_argument("--extract-texts", action="store_true", help="Извлекать тексты вакансий")
    parser.add_argument("--limit", type=int, default=10, help="Максимальное количество вакансий для отклика (по умолчанию: 10)")
    
    args = parser.parse_args()
    
    # Использование готового запроса из search_queries.py
    search_query = args.search
    if args.search_role and not search_query:
        try:
            from search_queries import get_default_query
            search_query = get_default_query(args.search_role)
            print(f"📋 Используется запрос для роли '{args.search_role}': {search_query}")
        except ImportError:
            print("⚠️ Модуль search_queries не найден, используйте --search")
    
    # Загрузка шаблона сопроводительного письма из файла
    cover_letter_template = None
    if args.cover_letter:
        try:
            with open(args.cover_letter, 'r', encoding='utf-8') as f:
                cover_letter_template = f.read()
        except FileNotFoundError:
            # Если файл не найден, используем как текст
            cover_letter_template = args.cover_letter
    
    with sync_playwright() as p:
        run(p, 
            phone_number=args.phone,
            sms_code=args.sms_code,
            search_query=search_query,
            cover_letter_template=cover_letter_template,
            extract_vacancy_texts=args.extract_texts,
            limit=args.limit)