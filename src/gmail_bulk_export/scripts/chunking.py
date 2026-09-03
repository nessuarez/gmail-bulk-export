"""Month-sized, resumable work units for bulk downloads.

Downloading ten years in a single Gmail query is not resumable: an interruption
three hours in throws away everything. Splitting the range into calendar months
gives each unit its own checkpoint, and keeps any single `messages.list`
pagination short enough to finish.

Two details matter for correctness:

* Gmail's `before:` operator is **exclusive** and its `after:`/`before:` dates
  are resolved in the mailbox's own timezone. Each chunk therefore pads its
  query by one day on both sides.
* The padding makes consecutive chunks overlap, which is deliberate: it
  guarantees there are no gaps at month boundaries. The duplicate rows are
  absorbed by the de-duplication in `core.save_to_csv.merge_csv_files`.
"""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator, List, Optional, Sequence

from gmail_bulk_export.core.checkpoints import make_key

PHASE_METADATA = "metadata"


@dataclass(frozen=True)
class MonthChunk:
    """A single calendar month of work for one mailbox."""

    year: int
    month: int

    @property
    def label(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        return date(self.year, self.month, monthrange(self.year, self.month)[1])

    @property
    def query_start(self) -> str:
        """Gmail `after:` bound, padded one day back."""
        return (self.first_day - timedelta(days=1)).strftime("%Y-%m-%d")

    @property
    def query_end(self) -> str:
        """Gmail `before:` bound, padded one day forward (the operator is exclusive)."""
        return (self.last_day + timedelta(days=1)).strftime("%Y-%m-%d")

    @property
    def date_range(self) -> tuple:
        return (self.query_start, self.query_end)

    def checkpoint_key(self, phase: str = PHASE_METADATA) -> str:
        """Stable key based on the calendar month, not on the padded query."""
        return make_key(
            self.first_day.strftime("%Y%m%d"),
            self.last_day.strftime("%Y%m%d"),
            phase=phase,
        )


def parse_month(value: str) -> tuple:
    """Parses 'YYYY-MM' (or 'YYYYMM') into (year, month)."""
    cleaned = value.strip().replace("/", "-")
    if "-" in cleaned:
        year_part, month_part = cleaned.split("-", 1)
    else:
        year_part, month_part = cleaned[:4], cleaned[4:]
    year, month = int(year_part), int(month_part)
    if not 1 <= month <= 12:
        raise ValueError(f"Mes fuera de rango en '{value}'")
    return year, month


def month_chunks(start: str, end: str) -> List[MonthChunk]:
    """Every calendar month from `start` to `end`, both inclusive.

    Args:
        start: first month, as 'YYYY-MM'.
        end: last month, as 'YYYY-MM'.
    """
    start_year, start_month = parse_month(start)
    end_year, end_month = parse_month(end)

    if (end_year, end_month) < (start_year, start_month):
        raise ValueError(f"El rango {start}..{end} está invertido")

    chunks = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        chunks.append(MonthChunk(year, month))
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return chunks


def clamp_to_today(end: str, today: Optional[date] = None) -> str:
    """Never schedule months in the future; there is nothing to download there."""
    today = today or date.today()
    end_year, end_month = parse_month(end)
    if (end_year, end_month) > (today.year, today.month):
        return f"{today.year:04d}-{today.month:02d}"
    return f"{end_year:04d}-{end_month:02d}"


def order_by_priority(
    chunks: Sequence[MonthChunk], priority_years: Sequence[int]
) -> List[MonthChunk]:
    """Puts the highest-value years first, then walks the rest newest to oldest.

    The point is that a multi-hour run should land the data the analysis
    actually cares about in its first hour, so the notebook can be exercised
    long before the whole backfill finishes.
    """
    priority = list(dict.fromkeys(int(year) for year in priority_years))
    rank = {year: index for index, year in enumerate(priority)}

    def sort_key(chunk: MonthChunk):
        if chunk.year in rank:
            return (0, rank[chunk.year], chunk.month)
        # Remaining years newest first, so recent history arrives before 2016.
        return (1, -chunk.year, chunk.month)

    return sorted(chunks, key=sort_key)


def iter_plan(mailboxes: Sequence[str], chunks: Sequence[MonthChunk]) -> Iterator[tuple]:
    """Yields (mailbox, chunk) grouped by year block, so a whole year of every
    mailbox completes before moving on to the next year."""
    seen_years = []
    for chunk in chunks:
        if chunk.year not in seen_years:
            seen_years.append(chunk.year)

    for year in seen_years:
        year_chunks = [chunk for chunk in chunks if chunk.year == year]
        for mailbox in mailboxes:
            for chunk in year_chunks:
                yield mailbox, chunk
