import re
import unicodedata
import numpy as np
import pandas as pd

TEAM_ALIASES = {
    "usa": "united states",
    "us": "united states",
    "u s a": "united states",
    "united states of america": "united states",
    "korea republic": "south korea",
    "republic of korea": "south korea",
    "ir iran": "iran",
    "iran islamic republic": "iran",
    "turkiye": "turkey",
    "côte d ivoire": "ivory coast",
    "cote d ivoire": "ivory coast",
    "cote divoire": "ivory coast",
    "cabo verde": "cape verde",
    "congo dr": "dr congo",
    "democratic republic of congo": "dr congo",
    "czech republic": "czechia",
    "bosnia herzegovina": "bosnia and herzegovina",
    "bosnia": "bosnia and herzegovina",
    "new zeland": "new zealand",
    "england national team": "england",
    "france national team": "france",
    "germany national team": "germany",
    "brazil national team": "brazil",
    "argentina national team": "argentina",
}

def clean_text(text):
    if pd.isna(text):
        return ""
    text = str(text).strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def team_key(name):
    c = clean_text(name)
    return TEAM_ALIASES.get(c, c)

def standardize_column_names(df):
    df = df.copy()
    df.columns = [clean_text(c).replace(" ", "_") for c in df.columns]
    return df

def pick_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None

def parse_date_series(series):
    opts = [
        pd.to_datetime(series, errors="coerce"),
        pd.to_datetime(series, errors="coerce", dayfirst=True),
        pd.to_datetime(series.astype(str), errors="coerce"),
        pd.to_datetime(series.astype(str), errors="coerce", dayfirst=True),
    ]
    counts = [o.notna().sum() for o in opts]
    return opts[int(np.argmax(counts))]

def to_numeric_clean(series):
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.extract(r"([-+]?\d*\.?\d+)")[0],
        errors="coerce"
    )

def minmax_score(series):
    s = pd.to_numeric(series, errors="coerce")
    mn, mx = s.min(), s.max()
    if pd.isna(mn) or pd.isna(mx) or mx == mn:
        return pd.Series(0.5, index=s.index)
    return ((s - mn) / (mx - mn)).fillna(0.5)

def group_letter(value):
    if pd.isna(value):
        return ""
    s = str(value).strip().upper()
    # handle "Group A"
    m = re.search(r"\b([A-L])\b", s)
    if m:
        return m.group(1)
    # handle plain A
    return s[0] if s and s[0] in list("ABCDEFGHIJKL") else s
