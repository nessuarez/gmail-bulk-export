"""Data frame transformations.

Every function here is **idempotent**: the notebook applies the same transform
to the same dataframe more than once (once on load, again after reloading the
intermediate CSV), so re-applying one must be a no-op rather than destroying
the column.
"""

import ast

import pandas as pd


def transform_int_columns(df, int_columns):
    """Converts the given columns to numeric, leaving non-numeric ones intact."""
    for column in int_columns:
        if column not in df.columns:
            continue
        series = df[column]

        # Already a datetime: pandas 1.x returned epoch nanoseconds here, but
        # pandas 2.x coerces the whole column to NaN, which silently wipes
        # internalDate on the notebook's second pass.
        if pd.api.types.is_datetime64_any_dtype(series):
            continue

        converted = pd.to_numeric(series, errors="coerce")
        if converted.isna().all() and series.notna().any():
            # Nothing numeric in there (e.g. datetime strings). Leave it alone
            # instead of replacing real values with NaN.
            continue
        df[column] = converted
    return df


def transform_date_columns(df):
    """Transform date columns to datetime format."""
    if "internalDate" in df.columns:
        series = df["internalDate"]
        if not pd.api.types.is_datetime64_any_dtype(series):
            # internalDate is epoch milliseconds when it comes straight from the
            # API, but a datetime string once it has been through a CSV round-trip.
            numeric = pd.to_numeric(series, errors="coerce")
            if numeric.notna().any():
                df["internalDate"] = pd.to_datetime(numeric, unit="ms", errors="coerce")
            else:
                df["internalDate"] = pd.to_datetime(series, errors="coerce", format="mixed")

    if "date" in df.columns:
        series = df["date"]
        if not pd.api.types.is_datetime64_any_dtype(series):
            # The raw `Date` header carries mixed UTC offsets; pandas requires
            # utc=True to parse those together. Normalize to naive UTC so it
            # stays comparable with internalDate.
            parsed = pd.to_datetime(series, errors="coerce", utc=True, format="mixed")
            df["date"] = parsed.dt.tz_localize(None)
    return df


def drop_unnecessary_columns(df, unnecessary_columns):
    df.drop(columns=unnecessary_columns, inplace=True, errors="ignore")
    return df


def _coerce_list(value):
    """Parses a Python-list repr into a list, and passes real lists through."""
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value.strip())
        except (ValueError, SyntaxError):
            return []
        return list(parsed) if isinstance(parsed, (list, tuple)) else []
    return []


def transform_list_columns(df, list_columns):
    """Parses stringified list columns (labelIds is a Python repr, not JSON)."""
    for column in list_columns:
        if column not in df.columns:
            continue
        df[column] = df[column].apply(_coerce_list)
    return df


# Mantener como texto las columnas de tipo string
def transform_text_columns(df):
    text_columns = [
        "id",
        "threadId",
        "deliveredTo",
        "subject",
        "from",
        "to",
        "cc",
        "bcc",
        "contentType",
        "mailbox",
        "file_path",
    ]
    present = [column for column in text_columns if column in df.columns]
    df[present] = df[present].astype(str)
    return df
