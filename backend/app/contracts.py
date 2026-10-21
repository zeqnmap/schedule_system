"""Wire contracts shared by the schedule site and the Telegram bot."""

from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class PublicationPeriod(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    week_number: int | None = Field(default=None, ge=1, le=53)
    day_of_week: int | None = Field(default=None, ge=1, le=6)


class PublicationFile(BaseModel):
    filename: str
    sha256: str
    group_number: int | None = Field(default=None, ge=1)


class PublicationManifest(BaseModel):
    """Versioned manifest embedded in student publication ZIP files.

    Defaults keep the contract readable by older bot deployments while all new
    packages carry complete metadata and hashes.
    """

    schema_version: Literal[1] = 1
    pdf_version: str = "1"
    publication_id: str | None = None
    filename: str = "Расписание.pdf"
    caption: str = ""
    published_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    academic_year_id: int | None = None
    period_id: int | None = None
    period: PublicationPeriod = Field(default_factory=PublicationPeriod)
    groups: list[int] = Field(default_factory=list)
    files: list[PublicationFile] = Field(default_factory=list)
