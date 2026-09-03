"""The search index: building, filters, and the CLI's date parsing."""

import csv
import json
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

from gmail_bulk_export.core.search_index import (
    Query,
    breakdown,
    build_index,
    count,
    format_timestamp,
    fts_expression,
    index_stats,
    open_index,
    parse_date_bound,
    search,
)
from gmail_bulk_export.scripts.search_emails import main as search_emails_main
from gmail_bulk_export.scripts.search_emails import parse_size

COLUMNS = [
    "id",
    "threadId",
    "labelIds",
    "sizeEstimate",
    "historyId",
    "internalDate",
    "deliveredTo",
    "subject",
    "from",
    "to",
    "cc",
    "bcc",
    "date",
    "contentType",
    "snippet",
    "mailbox",
    "file_path",
]


def millis(text):
    moment = datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def message(**overrides):
    row = {name: "" for name in COLUMNS}
    row.update(
        {
            "id": "m1",
            "threadId": "t1",
            "labelIds": "['INBOX', 'Label_7']",
            "sizeEstimate": "12000",
            "internalDate": str(millis("2023-05-17 09:30")),
            "deliveredTo": "client@example.com",
            "subject": "Flight booking to Múnich",
            "from": "Ana <ana@client.com>",
            "to": "desk@example.com",
            "date": "Wed, 17 May 2023 11:30:00 +0200",
            "contentType": "multipart/alternative; boundary=x",
            "snippet": "We need a petición for a flight on Monday",
            "mailbox": "desk@example.com",
        }
    )
    row.update(overrides)
    if "file_path" not in overrides:
        # The per-day CSV path: the folder is the day phase 2 derived from the
        # raw `Date` header, not the UTC date.
        day = datetime.fromtimestamp(int(row["internalDate"]) / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d"
        )
        row["file_path"] = f"output/{row['mailbox']}/{day}/{day}.csv"
    return row


def write_source(base_path, rows, labels=None):
    """Builds a fake `output/`: a consolidated export plus labels.csv per mailbox."""
    source = base_path / "emails_with_mailboxes.csv"
    with open(source, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    for mailbox, label_map in (labels or {}).items():
        directory = base_path / mailbox
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / "labels.csv", "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["id", "name", "type"])
            writer.writeheader()
            for label_id, name in label_map.items():
                writer.writerow({"id": label_id, "name": name, "type": "user"})
    return source


@pytest.fixture
def index(tmp_path):
    rows = [
        message(),
        message(
            id="m2",
            threadId="t1",
            subject="RE: Flight booking to Múnich",
            labelIds="['SENT']",
            **{"from": "Agency <desk@example.com>"},
            to="ana@client.com",
            internalDate=str(millis("2023-05-17 10:00")),
            contentType="multipart/mixed; boundary=y",
            sizeEstimate="2500000",
            snippet="Confirmed, the ticket is attached",
            file_path="output/legacy.csv",
        ),
        message(
            id="m3",
            threadId="t3",
            subject="January invoice",
            labelIds="['INBOX', 'Label_7']",
            internalDate=str(millis("2024-01-31 23:30")),
            deliveredTo="other@example.com",
            **{"from": "Luis <luis@othercustomer.com>"},
            mailbox="desk2@example.com",
            file_path="output/desk2@example.com/2024-02-01/2024-02-01.csv",
        ),
        # Same id as m1 but in another mailbox: two distinct rows, exactly as
        # `core.load_metadatas` treats them.
        message(id="m1", mailbox="desk2@example.com"),
        # An exact duplicate: month chunks overlap on purpose.
        message(),
    ]
    labels = {
        "desk@example.com": {"Label_7": "@Assigned/Ana Ruiz"},
        "desk2@example.com": {"Label_7": "Pending"},
    }
    source = write_source(tmp_path, rows, labels)
    db_path = tmp_path / "search_index.sqlite3"
    stats = build_index(source, db_path)
    return db_path, stats


@pytest.fixture
def connection(index):
    db_path, _ = index
    handle = open_index(db_path)
    yield handle
    handle.close()


def test_build_deduplicates_by_id_and_mailbox(index):
    _, stats = index
    assert stats["rows"] == 4
    assert stats["duplicates"] == 1
    assert stats["mailboxes"] == 2
    assert stats["threads"] == 2


def test_text_search_ignores_accents_and_case(connection):
    hits = search(connection, Query(text="peticion MUNICH"), fields=["id"])
    assert {row["id"] for row in hits} == {"m1"}


def test_subject_search_is_scoped_to_the_subject(connection):
    # "petición" is only in the snippet, not in the subject.
    assert search(connection, Query(subject="peticion"), fields=["id"]) == []
    assert len(search(connection, Query(subject="invoice"), fields=["id"])) == 1


def test_prefix_search(connection):
    hits = search(connection, Query(subject="invoic*"), fields=["id"])
    assert [row["id"] for row in hits] == ["m3"]


def test_contains_matches_a_literal_substring(connection):
    # "ooking" is not a word: FTS would not find it, `contains` does.
    assert search(connection, Query(text="ooking"), fields=["id"]) == []
    assert len(search(connection, Query(contains="ooking to"), fields=["id"])) == 3


def test_date_range_is_inclusive_on_both_ends(connection):
    query = Query(
        since=parse_date_bound("2024-01", utc=True),
        until=parse_date_bound("2024-01", end=True, utc=True),
    )
    # m3 is 31 January at 23:30, so it falls inside `--until 2024-01`.
    assert [row["id"] for row in search(connection, query, fields=["id"])] == ["m3"]


def test_label_filter_uses_the_name_of_that_mailbox(connection):
    # The same Label_7 is called something different in each mailbox.
    assigned = search(connection, Query(labels=["Assigned/Ana"]), fields=["id", "mailbox"])
    assert [(row["id"], row["mailbox"]) for row in assigned] == [("m1", "desk@example.com")]

    pending = search(connection, Query(labels=["Pending"]), fields=["id", "mailbox"])
    assert {row["mailbox"] for row in pending} == {"desk2@example.com"}


def test_derived_columns(connection):
    rows = search(
        connection,
        Query(ids=["m2"]),
        fields=["id", "fromDomain", "hasAttachment", "isSent", "labels"],
    )
    assert rows == [
        {
            "id": "m2",
            "fromDomain": "example.com",
            "hasAttachment": 1,
            "isSent": 1,
            "labels": "SENT",
        }
    ]


def test_repeated_filters_are_or_within_a_field(connection):
    assert count(connection, Query(domains=["client.com", "othercustomer.com"])) == 3


def test_wildcards_in_exact_filters(connection):
    assert count(connection, Query(mailboxes=["desk*"])) == 4
    assert count(connection, Query(mailboxes=["desk@example.com"])) == 2


def test_attachment_and_size_filters(connection):
    hits = search(connection, Query(with_attachment=True), fields=["id"])
    assert [row["id"] for row in hits] == ["m2"]
    assert count(connection, Query(min_size=1_000_000)) == 1


def test_direction_filter(connection):
    assert count(connection, Query(direction="sent")) == 1
    assert count(connection, Query(direction="received")) == 3


def test_thread_lookup_returns_the_whole_conversation(connection):
    hits = search(connection, Query(thread="t1"), fields=["id"], order="date-asc")
    assert [row["id"] for row in hits] == ["m1", "m1", "m2"]


def test_day_comes_from_the_metadata_folder_not_from_utc(connection):
    """About 1% of messages land in a day other than their UTC date."""
    rows = search(connection, Query(ids=["m3"]), fields=["date", "day"])
    assert format_timestamp(rows[0]["date"], utc=True, fmt="%Y-%m-%d") == "2024-01-31"
    assert rows[0]["day"] == "2024-02-01"


def test_day_falls_back_to_the_utc_date_without_a_day_csv(connection):
    rows = search(connection, Query(ids=["m2"]), fields=["day"])
    assert rows[0]["day"] == "2023-05-17"


def test_breakdown_by_year(connection):
    assert breakdown(connection, Query(), "year") == [("2023", 3), ("2024", 1)]


def test_breakdown_by_domain_is_ordered_by_count(connection):
    assert breakdown(connection, Query(), "domain") == [
        ("client.com", 2),
        ("example.com", 1),
        ("othercustomer.com", 1),
    ]


def test_index_is_opened_read_only(connection):
    with pytest.raises(sqlite3.OperationalError):
        connection.execute("DELETE FROM emails")


def test_search_rejects_unknown_fields(connection):
    with pytest.raises(ValueError):
        search(connection, Query(), fields=["asunto"])


def test_index_stats_reports_the_date_range(connection):
    stats = index_stats(connection)
    assert format_timestamp(stats["first"], utc=True, fmt="%Y-%m-%d") == "2023-05-17"
    assert format_timestamp(stats["last"], utc=True, fmt="%Y-%m-%d") == "2024-01-31"


def test_build_from_a_parquet_source_keeps_the_labels(tmp_path):
    """The typed Parquet carries `labelIds` as a real list, not as a repr."""
    pandas = pytest.importorskip("pandas")
    pytest.importorskip("pyarrow")
    from gmail_bulk_export.core.load_metadatas import to_parquet_typed

    source = write_source(tmp_path, [message()], {"desk@example.com": {"Label_7": "Groups"}})
    frame = pandas.read_csv(source, dtype=str)
    parquet = tmp_path / "emails_with_mailboxes.parquet"
    to_parquet_typed(frame, parquet)

    stats = build_index(parquet, tmp_path / "index.sqlite3")
    assert stats["rows"] == 1
    handle = open_index(tmp_path / "index.sqlite3")
    rows = search(handle, Query(labels=["Groups"]), fields=["id", "date", "labels"])
    handle.close()
    assert rows[0]["labels"] == "INBOX | Groups"
    # The Parquet datetime has to come back as the very same epoch-ms.
    assert rows[0]["date"] == millis("2023-05-17 09:30")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2023", "2023-01-01 00:00:00"),
        ("2023-05", "2023-05-01 00:00:00"),
        ("2023-05-17", "2023-05-17 00:00:00"),
        ("17/05/2023", "2023-05-17 00:00:00"),
        ("2023-05-17 08:45", "2023-05-17 08:45:00"),
    ],
)
def test_parse_date_bound_start(text, expected):
    bound = parse_date_bound(text, utc=True)
    assert format_timestamp(bound, utc=True, fmt="%Y-%m-%d %H:%M:%S") == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2023", "2023-12-31 23:59:59"),
        ("2023-02", "2023-02-28 23:59:59"),  # and the 29th on a leap year
        ("2024-02", "2024-02-29 23:59:59"),
        ("2023-05-17", "2023-05-17 23:59:59"),
    ],
)
def test_parse_date_bound_end_is_the_end_of_the_period(text, expected):
    bound = parse_date_bound(text, end=True, utc=True)
    assert format_timestamp(bound, utc=True, fmt="%Y-%m-%d %H:%M:%S") == expected


def test_parse_date_bound_rejects_nonsense():
    with pytest.raises(ValueError):
        parse_date_bound("last tuesday")


def test_fts_expression_quotes_user_text():
    # A stray apostrophe or hyphen is FTS5 syntax: unquoted, the query fails
    # outright rather than finding nothing.
    assert fts_expression(Query(text="l'hotel -madrid")) == '("l\'hotel" AND "-madrid")'
    assert fts_expression(Query(subject='"company dinner" invoic*')) == (
        '{subject} : ("company dinner" AND "invoic"*)'
    )
    assert fts_expression(Query()) is None


@pytest.mark.parametrize(
    "text, expected", [("1024", 1024), ("500K", 512000), ("2M", 2097152), ("1,5M", 1572864)]
)
def test_parse_size(text, expected):
    assert parse_size(text) == expected


# ---------------------------------------------------------------------------
# The CLI on top: that arguments reach the query and something comes out
# ---------------------------------------------------------------------------


def run_cli(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["search_emails", *args])
    code = search_emails_main()
    return code, capsys.readouterr()


def test_cli_csv_output(index, monkeypatch, capsys):
    db_path, _ = index
    code, output = run_cli(
        monkeypatch, capsys, "--db", str(db_path), "--subject", "invoice", "--format", "csv"
    )
    assert code == 0
    lines = output.out.strip().splitlines()
    assert lines[0].startswith("date,mailbox,id,threadId")
    assert len(lines) == 2
    assert "January invoice" in lines[1]


def test_cli_count_and_fields(index, monkeypatch, capsys):
    db_path, _ = index
    code, output = run_cli(monkeypatch, capsys, "--db", str(db_path), "--count")
    assert (code, output.out.strip()) == (0, "4")

    code, output = run_cli(
        monkeypatch, capsys, "--db", str(db_path), "--fields", "id,fromDomain", "--format", "jsonl"
    )
    assert code == 0
    assert json.loads(output.out.splitlines()[0]).keys() == {"id", "fromDomain"}


def test_cli_ids_format_honours_fields(index, monkeypatch, capsys):
    """`--fields threadId --format ids` yields thread ids, not message ids."""
    db_path, _ = index
    code, output = run_cli(
        monkeypatch,
        capsys,
        "--db",
        str(db_path),
        "--subject",
        "invoice",
        "--fields",
        "threadId",
        "--format",
        "ids",
    )
    assert (code, output.out.split()) == (0, ["t3"])


def test_cli_writes_a_file(index, monkeypatch, capsys, tmp_path):
    db_path, _ = index
    destination = tmp_path / "export" / "hits.csv"
    code, _ = run_cli(
        monkeypatch,
        capsys,
        "--db",
        str(db_path),
        "--domain",
        "othercustomer.com",
        "--format",
        "csv",
        "--out",
        str(destination),
    )
    assert code == 0
    with destination.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["id"] for row in rows] == ["m3"]


def test_cli_export_is_not_capped_at_the_console_default(index, monkeypatch, capsys, tmp_path):
    """With no `-n`, an `--out` takes every hit, not the console's 50."""
    db_path, _ = index
    destination = tmp_path / "all.csv"
    code, _ = run_cli(monkeypatch, capsys, "--db", str(db_path), "--out", str(destination))
    assert code == 0
    with destination.open(encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 4


def test_cli_infers_the_format_from_the_extension(index, monkeypatch, capsys, tmp_path):
    db_path, _ = index
    destination = tmp_path / "hits.jsonl"
    code, _ = run_cli(monkeypatch, capsys, "--db", str(db_path), "--out", str(destination))
    assert code == 0
    lines = destination.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert json.loads(lines[0])["mailbox"]


def test_cli_for_payloads_writes_what_the_downloader_reads(index, monkeypatch, capsys, tmp_path):
    """`--for-payloads` emits the columns read_email_ids_and_mailbox_from_csv wants."""
    from gmail_bulk_export.core.csv_handler import read_email_ids_and_mailbox_from_csv

    db_path, _ = index
    destination = tmp_path / "ids.csv"
    code, _ = run_cli(
        monkeypatch,
        capsys,
        "--db",
        str(db_path),
        "--id",
        "m3",
        "--for-payloads",
        "--out",
        str(destination),
    )
    assert code == 0
    assert destination.read_text(encoding="utf-8").splitlines()[0] == "id,mailbox,date"
    assert read_email_ids_and_mailbox_from_csv(destination) == [
        ("m3", "desk2@example.com", "2024-02-01")
    ]


def test_cli_without_an_index_explains_how_to_build_it(tmp_path, monkeypatch, capsys):
    code, output = run_cli(monkeypatch, capsys, "--db", str(tmp_path / "nope.sqlite3"))
    assert code == 1
    assert "build_search_index" in output.err
