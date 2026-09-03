"""Tests for the dataframe transforms used by the EDA notebook.

The notebook applies each transform twice — once when it loads the consolidated
CSV, again after reloading the intermediate CSV it wrote. Under pandas 2.x the
second application used to destroy the data: `to_numeric` on an
already-converted datetime column returns all-NaN, and the list parser returned
`[]` for values that were already lists. Both wiped whole columns silently.
"""

import pandas as pd

from gmail_bulk_export.data_transforms import (
    transform_date_columns,
    transform_int_columns,
    transform_list_columns,
    transform_text_columns,
)


def sample_df():
    return pd.DataFrame(
        {
            "id": ["a", "b"],
            "sizeEstimate": ["100", "200"],
            "historyId": ["1", "2"],
            "internalDate": ["1704193297000", "1704279697000"],
            "labelIds": ["['INBOX', 'UNREAD']", "['SENT']"],
            "date": ["2 Jan 2024 11:01:37 +0000", "3 Jan 2024 11:01:37 +0100"],
        }
    )


def test_int_then_date_conversion():
    df = transform_date_columns(transform_int_columns(sample_df(), ["internalDate"]))
    assert pd.api.types.is_datetime64_any_dtype(df["internalDate"])
    assert df["internalDate"].notna().all()


def test_int_conversion_is_idempotent_over_datetimes():
    """The regression: the second pass must not blank out internalDate."""
    df = transform_date_columns(transform_int_columns(sample_df(), ["internalDate"]))
    expected = df["internalDate"].tolist()

    df = transform_int_columns(df, ["sizeEstimate", "historyId", "internalDate"])
    df = transform_date_columns(df)

    assert df["internalDate"].notna().all()
    assert df["internalDate"].tolist() == expected


def test_list_conversion_is_idempotent():
    """The second pass must not turn parsed lists back into empty lists."""
    df = transform_list_columns(sample_df(), ["labelIds"])
    assert df["labelIds"].tolist() == [["INBOX", "UNREAD"], ["SENT"]]

    df = transform_list_columns(df, ["labelIds"])
    assert df["labelIds"].tolist() == [["INBOX", "UNREAD"], ["SENT"]]


def test_list_conversion_survives_malformed_values():
    df = pd.DataFrame({"labelIds": ["not a list", None, "['OK']"]})
    assert transform_list_columns(df, ["labelIds"])["labelIds"].tolist() == [
        [],
        [],
        ["OK"],
    ]


def test_mixed_timezone_dates_parse_to_naive_utc():
    """The raw Date header mixes offsets; pandas needs utc=True for those."""
    df = transform_date_columns(sample_df())
    assert pd.api.types.is_datetime64_any_dtype(df["date"])
    assert df["date"].notna().all()
    assert df["date"].dt.tz is None
    # 11:01:37 +0100 is 10:01:37 UTC.
    assert df["date"].iloc[1].hour == 10


def test_date_conversion_is_idempotent():
    df = transform_date_columns(sample_df())
    expected = df["date"].tolist()
    assert transform_date_columns(df)["date"].tolist() == expected


def test_internal_date_round_trips_through_csv(tmp_path):
    """After a CSV round-trip internalDate is a datetime string, not epoch ms."""
    df = transform_date_columns(transform_int_columns(sample_df(), ["internalDate"]))
    path = tmp_path / "clean.csv"
    df.to_csv(path, index=False)

    reloaded = pd.read_csv(path, dtype=str)
    reloaded = transform_int_columns(reloaded, ["sizeEstimate", "internalDate"])
    reloaded = transform_date_columns(reloaded)

    assert reloaded["internalDate"].notna().all()
    assert reloaded["internalDate"].tolist() == df["internalDate"].tolist()


def test_text_transform_tolerates_missing_columns():
    df = pd.DataFrame({"id": [1], "subject": ["hi"]})
    result = transform_text_columns(df)
    assert result["id"].tolist() == ["1"]


def test_int_transform_ignores_absent_columns():
    df = pd.DataFrame({"a": ["1"]})
    assert transform_int_columns(df, ["missing"])["a"].tolist() == ["1"]
