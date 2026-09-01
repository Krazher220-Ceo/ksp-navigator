"""
web/errors.py — единый формат ошибки версионированного API (блок Ф2).

Зачем модуль: внешнему фронтенду мало кода состояния — ему нужен готовый
русский текст, который можно показать человеку не переписывая. Поэтому
всё под /api/v1/* отвечает одинаково:

    {"error": {"code": "NOT_FOUND", "message": "Не нашёл — ..."}}

Тексты берутся из bot/texts.py и здесь не сочиняются: на одну ситуацию в
продукте — одна формулировка, у бота и у кабинета общая.

Что осознанно не делает: не трогает старые /api/* — на них работает
Mini App в проде, и формат их ответов этот блок менять не должен.
Обработчики ниже смотрят на путь запроса и для всего, что вне /api/v1,
отдают управление обработчику FastAPI по умолчанию.

Чего в кодах нет: 403. Чужой ресурс отдаёт 404 — мы не подтверждаем даже
факт существования чужой записи (решение блока Б9.2).
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from bot import texts

V1_PREFIX = "/api/v1"

# Коды ошибок. Кодов ровно столько, сколько различимых ситуаций: клиенту
# нужно уметь развести «войдите заново» и «повторите через минуту», всё
# остальное он показывает текстом.
CODE_NOT_AUTHORIZED = "NOT_AUTHORIZED"
CODE_NOT_FOUND = "NOT_FOUND"
CODE_BAD_REQUEST = "BAD_REQUEST"
CODE_SERVER_UNAVAILABLE = "SERVER_UNAVAILABLE"
CODE_INTERNAL = "INTERNAL"

_BY_STATUS: dict[int, tuple[str, str]] = {
    401: (CODE_NOT_AUTHORIZED, texts.API_NOT_AUTHORIZED),
    403: (CODE_NOT_AUTHORIZED, texts.API_NOT_AUTHORIZED),
    404: (CODE_NOT_FOUND, texts.API_NOT_FOUND),
    503: (CODE_SERVER_UNAVAILABLE, texts.API_SERVER_UNAVAILABLE),
}


class ApiError(HTTPException):
    """Ошибка с готовым русским текстом для показа человеку.

    Наследуется от HTTPException, чтобы её одинаково понимали и FastAPI,
    и обработчики ниже: эндпоинту достаточно поднять ApiError, ничего не
    зная про формат ответа.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message


def error_body(code: str, message: str) -> dict:
    """Тело ответа единого формата. Отдельная функция затем, что этим же
    конвертом пользуется /api/v1/health, а он не исключение и не ошибка."""
    return {"error": {"code": code, "message": message}}


def _is_v1(request: Request) -> bool:
    return request.url.path.startswith(V1_PREFIX)


async def _handle_http_exception(request: Request, exc: HTTPException) -> Response:
    if not _is_v1(request):
        # Старые /api/* и статика Mini App отвечают ровно как раньше.
        return await http_exception_handler(request, exc)

    if isinstance(exc, ApiError):
        code, message = exc.code, exc.message
    else:
        # У обычного HTTPException в detail лежит служебная строка вроде
        # «не найдено» — показывать её человеку нельзя, берём текст по
        # коду состояния.
        code, message = _BY_STATUS.get(exc.status_code, (CODE_INTERNAL, texts.ERROR_UNEXPECTED))

    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(code, message),
        headers=getattr(exc, "headers", None),
    )


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> Response:
    if not _is_v1(request):
        return await request_validation_exception_handler(request, exc)

    # Первая непройденная проверка — та, о которой человеку и надо
    # сказать. Перечислять все двадцать полей смысла нет.
    первая = exc.errors()[0] if exc.errors() else {}
    поле = ".".join(str(part) for part in первая.get("loc", ()) if part != "body") or "поле"
    return JSONResponse(
        status_code=422,
        content=error_body(CODE_BAD_REQUEST, texts.API_BAD_REQUEST.format(reason=f"не заполнено «{поле}»")),
    )


def install_error_handlers(app: FastAPI) -> None:
    """Ставит обработчики на приложение. Вызывается один раз, в web/api.py.

    Обработчик вешается на HTTPException Starlette, а не FastAPI: 404 на
    несуществующий путь поднимает именно базовый класс, и подписка на
    наследника его не поймала бы. Проверено на /api/v1/несуществующее —
    без этого он отвечал старым {"detail": "Not Found"}.
    """
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
