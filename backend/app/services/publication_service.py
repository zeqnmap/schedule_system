"""HTTP client for the isolated Telegram bot publication API."""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from fastapi import HTTPException

from ..contracts import PublicationFile, PublicationManifest, PublicationPeriod


def _bot_candidates() -> list[str]:
    bot_url = os.getenv("BOT_BROADCAST_URL", "").rstrip("/")
    if not bot_url:
        return []
    candidates = [bot_url]
    if urlsplit(bot_url).hostname == "mgkap_schedule_bot":
        candidates.append("http://127.0.0.1:8081")
    return list(dict.fromkeys(candidates))


def _credentials() -> tuple[list[str], str]:
    candidates = _bot_candidates()
    secret = os.getenv("BOT_SHARED_SECRET", "")
    if not candidates or not secret:
        raise HTTPException(status_code=503, detail="Telegram-бот ещё не настроен")
    return candidates, secret


def _post(path: str, body: bytes, *, query: dict[str, Any] | None = None,
          content_type: str, timeout: int, error_detail: str,
          expect_json: bool = True) -> dict | None:
    candidates, secret = _credentials()
    encoded_query = f"?{urlencode(query)}" if query else ""
    last_network_error: Exception | None = None
    for candidate in candidates:
        request = Request(
            f"{candidate}{path}{encoded_query}",
            data=body,
            headers={"Content-Type": content_type, "X-Bot-Secret": secret},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                if not expect_json:
                    return None
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise HTTPException(status_code=502, detail=error_detail) from exc
        except (URLError, TimeoutError) as exc:
            last_network_error = exc
    raise HTTPException(status_code=503, detail="Нет связи с Telegram-ботом") from last_network_error


def broadcast_pdf(pdf: io.BytesIO, audience: str, filename: str, caption: str) -> dict:
    return _post(
        "/internal/broadcast-pdf", pdf.getvalue(),
        query={"audience": audience, "filename": filename, "caption": caption},
        content_type="application/pdf", timeout=30, error_detail="Бот не принял рассылку",
    ) or {}


def publish_pdf(pdf: io.BytesIO, audience: str, filename: str, caption: str) -> dict:
    return _post(
        "/internal/publish-pdf", pdf.getvalue(),
        query={"audience": audience, "filename": filename, "caption": caption},
        content_type="application/pdf", timeout=30, error_detail="Бот не принял публикацию",
    ) or {}


def save_group_pdf(pdf: io.BytesIO, group_number: int, filename: str, caption: str) -> None:
    _post(
        "/internal/group-schedule", pdf.getvalue(),
        query={"group_number": group_number, "filename": filename, "caption": caption},
        content_type="application/pdf", timeout=30, error_detail="Бот не принял расписание группы",
        expect_json=False,
    )


def publish_student_package(
    pdf: io.BytesIO,
    group_pdfs: dict[int, io.BytesIO],
    filename: str,
    caption: str,
    *,
    academic_year_id: int | None = None,
    period_id: int | None = None,
    period: PublicationPeriod | None = None,
    pdf_version: str = "1",
) -> dict:
    """Build and send an immutable, self-describing student publication."""
    group_numbers = sorted(group_pdfs)
    files = [PublicationFile(filename=filename, sha256=hashlib.sha256(pdf.getvalue()).hexdigest())]
    files.extend(
        PublicationFile(
            filename=f"groups/{number}.pdf",
            sha256=hashlib.sha256(group_pdfs[number].getvalue()).hexdigest(),
            group_number=number,
        )
        for number in group_numbers
    )
    manifest = PublicationManifest(
        pdf_version=pdf_version,
        filename=filename,
        caption=caption,
        academic_year_id=academic_year_id,
        period_id=period_id,
        period=period or PublicationPeriod(),
        groups=group_numbers,
        files=files,
    )
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("manifest.json", manifest.model_dump_json())
        package.writestr("student.pdf", pdf.getvalue())
        for group_number in group_numbers:
            package.writestr(f"groups/{group_number}.pdf", group_pdfs[group_number].getvalue())
    return _post(
        "/internal/publish-student-package", payload.getvalue(),
        content_type="application/zip", timeout=120,
        error_detail="Бот не принял пакет публикации",
    ) or {}


def store_pdf(pdf: io.BytesIO, audience: str, filename: str, caption: str) -> None:
    _post(
        "/internal/store-pdf", pdf.getvalue(),
        query={"audience": audience, "filename": filename, "caption": caption},
        content_type="application/pdf", timeout=30, error_detail="Бот не сохранил PDF",
        expect_json=False,
    )


def broadcast_student_groups(publication_id: str | None = None) -> dict:
    return _post(
        "/internal/broadcast-student-groups", b"",
        query={"publication_id": publication_id} if publication_id else None,
        content_type="application/octet-stream", timeout=30,
        error_detail="Бот не поставил персональную рассылку в очередь",
    ) or {}
