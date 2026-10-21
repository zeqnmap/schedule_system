"""Shared PDF rendering primitives.

Route handlers should depend on this service for fonts and cell formatting;
the database-specific renderers can be moved here incrementally without
changing the public export endpoints.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import stringWidth


class PdfService:
    font_name = "ScheduleFont"
    bold_font_name = "ScheduleFont-Bold"

    @classmethod
    def register_schedule_fonts(cls) -> None:
        font_path = next(
            (
                path
                for path in (
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                    "/Library/Fonts/Arial Unicode.ttf",
                )
                if Path(path).exists()
            ),
            None,
        )
        if not font_path:
            raise HTTPException(status_code=500, detail="Не найден шрифт для PDF")
        pdfmetrics.registerFont(TTFont(cls.font_name, font_path))
        bold_path = next(
            (
                path
                for path in (
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                    "/Library/Fonts/Arial Bold.ttf",
                )
                if Path(path).exists()
            ),
            font_path,
        )
        pdfmetrics.registerFont(TTFont(cls.bold_font_name, bold_path))

    @staticmethod
    def fit_cell(value: object, width: float, font_name: str = "ScheduleFont", font_size: float = 8) -> str:
        text = str(value or "-")
        if stringWidth(text, font_name, font_size) <= width:
            return text
        suffix = "…"
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if stringWidth(text[:middle].rstrip() + suffix, font_name, font_size) <= width:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + suffix


pdf_service = PdfService()
