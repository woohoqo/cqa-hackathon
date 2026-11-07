"""Organizer-specific adapter. No source credentials or transcripts in logging."""
import json
from urllib.parse import quote, unquote_plus, urlsplit, urlunsplit

import httpx
from pydantic import ValidationError

from .contracts import ConversationInput, ImportRequest, MessageInput
from .errors import AppError


def organizer_payload(payload: object, group_id: str) -> list[ConversationInput]:
    try:
        if not isinstance(payload, dict) or payload.get("isSuccess") is not True:
            raise ValueError()
        data = payload["data"]
        records = data["records"]
        if data["dataset_group_id"] != group_id or not isinstance(records, list):
            raise ValueError()
        if type(data["total_records"]) is not int or data["total_records"] != len(records):
            raise ValueError()
        if not 1 <= len(records) <= 1000:
            raise ValueError()
        items = []
        for row in records:
            if not isinstance(row["conversation_id"], str) or not isinstance(row["transcript"], list):
                raise ValueError()
            items.append(ConversationInput(
                external_id=row["conversation_id"], source=f"organizer:{group_id}",
                messages=[MessageInput(role=m["speaker"], text=m["text"], timestamp=m["timestamp"])
                          for m in row["transcript"]],
                metadata={"channel": row["channel"], "language": row["language"],
                          "audio_url": row.get("audio_url"), "dataset_group_id": group_id},
            ))
        if len({item.external_id for item in items}) != len(items):
            raise ValueError()
        return ImportRequest(conversations=items).conversations
    except (KeyError, TypeError, ValueError, ValidationError):
        raise AppError(422, "invalid_dev_payload", "Ответ API организаторов не соответствует формату Student Guide.") from None


def organizer_url(base_url: str, group_id: str) -> str:
    if not base_url:
        raise AppError(503, "dev_not_configured", "Заполните DEV_API_URL полной подписанной ссылкой из Student Guide в локальном .env.")
    parts = urlsplit(base_url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise AppError(503, "invalid_dev_configuration", "DEV_API_URL должен содержать корректную HTTPS-ссылку организаторов.")
    # Preserve the signed query bytes; rewrite only group_id.
    query = [piece for piece in parts.query.split("&") if unquote_plus(piece.split("=", 1)[0]) != "group_id" and piece]
    query.append("group_id=" + quote(group_id, safe=""))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(query), ""))


async def fetch_organizer(base_url: str, group_id: str) -> list[ConversationInput]:
    url = organizer_url(base_url, group_id)
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise AppError(502, "dev_http_error", "API организаторов отклонил запрос. Проверьте срок действия ссылки и group_id.", True)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 25 * 1024 * 1024:
                        raise AppError(502, "dev_response_too_large", "Ответ API превышает допустимый размер 25 МБ.")
        payload = json.loads(content)
    except httpx.HTTPError:
        raise AppError(502, "dev_unavailable", "API организаторов недоступен или не ответил вовремя.", True) from None
    except (ValueError, UnicodeDecodeError):
        raise AppError(502, "dev_invalid_json", "API организаторов вернул некорректный JSON.") from None
    return organizer_payload(payload, group_id)
