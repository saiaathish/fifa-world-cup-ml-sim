#!/usr/bin/env python3
"""Build the World Cup 2026 player database.

The pipeline parses FIFA's confirmed squad-list PDF, then optionally enriches
the squad rows with EAFC26 player ratings. Ratings are never invented: if a
player or country is missing from the EAFC26 file, the player fields stay blank
and country-level coverage flags show how reliable the player features are.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import string
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import openpyxl  # noqa: F401 - required by pandas for .xlsx input support.
import pandas as pd
import pdfplumber
from pypdf import PdfReader
from rapidfuzz import fuzz


logging.getLogger("pdfminer").setLevel(logging.ERROR)

PYTHON_EXECUTABLE = (
    "/home/saiaathish/.cache/codex-runtimes/"
    "codex-primary-runtime/dependencies/python/bin/python3"
)
DEFAULT_OUTPUT_DIR = Path("/home/saiaathish/Downloads/world_cup_2026_player_database")

FIFA_CONFIRMATION_ARTICLE_URL = (
    "https://www.fifa.com/en/articles/fifa-world-cup-2026-squads-confirmed"
)

# This PDF mirrors FIFA's official 48-page SquadLists-English document.
# Pass --pdf-url or --pdf-path if FIFA exposes a newer direct asset URL.
DEFAULT_PDF_URL = (
    "https://deportegraficomaryland.com/wp-content/uploads/2026/06/"
    "SquadLists-English.pdf"
)

SOURCE_VERSION_DATE = "2026-06-02"
EXPECTED_COUNTRIES = 48
EXPECTED_PLAYERS = 1248
EXPECTED_PLAYERS_PER_COUNTRY = 26
VALID_POSITIONS = {"GK", "DF", "MF", "FW"}
RELIABILITY_THRESHOLD = 0.60
HIGH_CONFIDENCE_SCORE = 90.0
MEDIUM_CONFIDENCE_SCORE = 82.0

SQUAD_COLUMNS = [
    "country",
    "fifa_code",
    "squad_no",
    "position",
    "player_name",
    "first_names",
    "last_names",
    "name_on_shirt",
    "dob",
    "club",
    "height_cm",
    "source_url",
    "source_version_date",
]

FC26_COLUMNS = [
    "fc26_player_id",
    "first_name",
    "last_name",
    "common_name",
    "full_name",
    "display_name",
    "short_name",
    "long_name",
    "nationality",
    "overall",
    "potential",
    "age",
    "fc26_dob",
    "fc26_height_cm",
    "weight",
    "pace",
    "shooting",
    "passing",
    "dribbling",
    "defending",
    "physical",
    "physic",
    "club_name",
    "league_name",
    "fc26_position",
    "player_positions",
    "position_type",
    "match_method",
    "match_score",
    "match_confidence",
]

MATCHED_COLUMNS = SQUAD_COLUMNS + FC26_COLUMNS

TEAM_FEATURE_COLUMNS = [
    "country",
    "fifa_code",
    "squad_players",
    "fc26_players_matched",
    "player_data_coverage",
    "player_features_available",
    "top_11_avg_overall",
    "top_23_avg_overall",
    "squad_avg_overall",
    "squad_avg_age",
    "avg_pace",
    "avg_shooting",
    "avg_passing",
    "avg_dribbling",
    "avg_defending",
    "avg_physical",
]

UNMATCHED_COLUMNS = SQUAD_COLUMNS + [
    "reason",
    "best_candidate_name",
    "best_candidate_country",
    "best_candidate_club",
    "best_candidate_score",
]

LOW_CONFIDENCE_COLUMNS = MATCHED_COLUMNS + [
    "best_candidate_name",
    "best_candidate_country",
    "best_candidate_club",
]

COUNTRY_ALIASES = {
    "usa": "United States",
    "united states": "United States",
    "united states of america": "United States",
    "ir iran": "Iran",
    "iran": "Iran",
    "korea republic": "South Korea",
    "south korea": "South Korea",
    "republic of korea": "South Korea",
    "turkiye": "Turkey",
    "turkey": "Turkey",
    "cote divoire": "Ivory Coast",
    "cote d ivoire": "Ivory Coast",
    "cote d'ivoire": "Ivory Coast",
    "côte divoire": "Ivory Coast",
    "côte d'ivoire": "Ivory Coast",
    "ivory coast": "Ivory Coast",
    "curacao": "Curacao",
    "curaçao": "Curacao",
    "cabo verde": "Cape Verde",
    "cape verde": "Cape Verde",
    "cape verde islands": "Cape Verde",
    "congo dr": "DR Congo",
    "dr congo": "DR Congo",
    "democratic republic of congo": "DR Congo",
    "holland": "Netherlands",
    "netherlands": "Netherlands",
    "czech republic": "Czechia",
    "czechia": "Czechia",
    "bosnia and herzegovina": "Bosnia And Herzegovina",
    "england": "England",
    "scotland": "Scotland",
    "wales": "Wales",
}

POSITION_GROUPS = {
    "GK": {"GK"},
    "DF": {"CB", "LB", "RB", "LWB", "RWB", "DF"},
    "MF": {"CM", "CDM", "CAM", "LM", "RM", "MF"},
    "FW": {"ST", "CF", "LW", "RW", "LF", "RF", "FW"},
}

COMMON_NAME_TOKENS = {
    "da",
    "de",
    "del",
    "di",
    "do",
    "dos",
    "das",
    "du",
    "la",
    "le",
    "van",
    "von",
    "der",
    "den",
    "jr",
    "junior",
}

FC26_COLUMN_ALIASES = {
    "fc26_player_id": [
        "fc26_player_id",
        "player_id",
        "sofifa_id",
        "id",
        "uid",
        "playerid",
    ],
    "first_name": ["firstName", "first_name", "first name"],
    "last_name": ["lastName", "last_name", "last name"],
    "common_name": ["commonName", "common_name", "common name", "known_as"],
    "full_name": ["full_name", "full name"],
    "display_name": ["display_name", "display name"],
    "short_name": ["short_name", "short name", "name", "known_as", "commonName"],
    "long_name": ["long_name", "long name", "full_name", "full name", "player_name"],
    "nationality": [
        "nationality",
        "nationality_name",
        "nationality name",
        "nation",
        "country",
    ],
    "overall": ["overallRating", "overall", "ova", "rating"],
    "potential": ["potential", "pot"],
    "age": ["age"],
    "fc26_dob": ["birthdate", "dob", "date_of_birth", "date of birth"],
    "fc26_height_cm": ["height", "height_cm", "height cm"],
    "weight": ["weight"],
    "pace": ["pace", "pac"],
    "shooting": ["shooting", "sho"],
    "passing": ["passing", "pas"],
    "dribbling": ["dribbling", "dri"],
    "defending": ["defending", "def"],
    "physical": ["phy", "physical", "physic"],
    "physic": ["phy", "physic", "physical"],
    "club_name": ["club_name", "club name", "club", "team", "club_team"],
    "league_name": ["leagueName", "league_name", "league name", "league"],
    "fc26_position": ["position", "best_position"],
    "player_positions": [
        "player_positions",
        "player positions",
        "positions",
        "position",
        "best_position",
    ],
    "position_type": ["positionType", "position_type", "position type"],
}


@dataclass
class CandidateMatch:
    index: int | None
    method: str
    score: float
    confidence: str
    best_candidate_name: str = ""
    best_candidate_country: str = ""
    best_candidate_club: str = ""


def clean_pdf_text(value: Any) -> str:
    """Clean PDF-extracted text while preserving accents for display fields."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\x00", "")).strip()


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def normalize_name(value: Any) -> str:
    """Normalize names for exact and fuzzy matching."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = strip_accents(str(value).lower())
    text = text.replace("ß", "ss").replace("ø", "o").replace("đ", "d")
    text = re.sub(r"[’'`]", " ", text)
    text = text.translate(str.maketrans("", "", string.punctuation))
    text = re.sub(r"\b(jr|junior|sr|senior|ii|iii|iv|v)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_club(value: Any) -> str:
    text = normalize_name(value)
    # Remove generic club words that can differ across datasets.
    text = re.sub(r"\b(fc|cf|sc|afc|c f|club|football|de|the)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_country(value: Any) -> str:
    cleaned = normalize_name(value)
    return COUNTRY_ALIASES.get(cleaned, strip_accents(str(value)).strip() if value else "")


def country_sort_key(country: str) -> str:
    return normalize_name(country)


def make_name_variants(*values: Any) -> list[str]:
    variants: list[str] = []
    for value in values:
        normalized = normalize_name(value)
        if normalized and normalized not in variants:
            variants.append(normalized)
    return variants


def squad_name_variants(row: pd.Series) -> list[str]:
    variants = make_name_variants(
        row.get("player_name"),
        f"{row.get('first_names', '')} {row.get('last_names', '')}",
        f"{row.get('last_names', '')} {row.get('first_names', '')}",
        row.get("name_on_shirt"),
    )

    # Handle common short-name patterns such as "L. Messi" vs "Lionel Messi".
    first_names = normalize_name(row.get("first_names"))
    last_names = normalize_name(row.get("last_names"))
    if first_names and last_names:
        first_token = first_names.split()[0]
        last_token = last_names.split()[-1]
        initials = " ".join(f"{token[0]}" for token in first_names.split() if token)
        variants.extend(
            v
            for v in [
                f"{first_token} {last_token}",
                f"{first_token[0]} {last_token}",
                f"{initials} {last_token}".strip(),
            ]
            if v and v not in variants
        )
    return variants


def squad_fuzzy_name_variants(row: pd.Series) -> list[str]:
    variants = make_name_variants(
        row.get("player_name"),
        row.get("name_on_shirt"),
        f"{row.get('first_names', '')} {row.get('last_names', '')}",
    )
    first_names = normalize_name(row.get("first_names"))
    last_names = normalize_name(row.get("last_names"))
    if first_names and last_names:
        first_token = first_names.split()[0]
        last_token = last_names.split()[-1]
        for variant in [f"{first_token} {last_token}", f"{first_token[0]} {last_token}"]:
            if variant and variant not in variants:
                variants.append(variant)
    return variants


def download_pdf(pdf_url: str, output_dir: Path) -> Path:
    cache_dir = output_dir / "_source"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "SquadLists-English.pdf"
    request = Request(pdf_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=60) as response:
            target.write_bytes(response.read())
    except URLError as exc:
        raise RuntimeError(f"Could not download squad PDF from {pdf_url}: {exc}") from exc
    return target


def find_country_header(text: str) -> tuple[str, str]:
    for line in text.splitlines():
        line = clean_pdf_text(line)
        match = re.match(r"^(.+?)\s+\(([A-Z]{3})\)$", line)
        if match and not line.startswith("FIFA World Cup"):
            return match.group(1).strip(), match.group(2).strip()
    raise ValueError("Could not find country header on PDF page.")


def parse_table_row(
    cells: list[Any],
    country: str,
    fifa_code: str,
    source_url: str,
) -> dict[str, Any] | None:
    values = [clean_pdf_text(cell) for cell in cells if clean_pdf_text(cell)]
    if len(values) < 8 or values[0] == "#" or values[1] not in VALID_POSITIONS:
        return None

    date_index = next(
        (idx for idx, cell in enumerate(values) if re.fullmatch(r"\d{2}/\d{2}/\d{4}", cell)),
        None,
    )
    if date_index is None or date_index < 5:
        return None

    squad_no = values[0]
    position = values[1]
    player_name = " ".join(values[2 : date_index - 3]).strip()
    first_names = values[date_index - 3]
    last_names = values[date_index - 2]
    name_on_shirt = values[date_index - 1]
    dob = values[date_index]
    club = " ".join(values[date_index + 1 : -1]).strip()
    height_cm = values[-1]

    if not player_name:
        player_name = f"{last_names} {first_names}".strip()

    return {
        "country": country,
        "fifa_code": fifa_code,
        "squad_no": int(squad_no),
        "position": position,
        "player_name": clean_pdf_text(player_name),
        "first_names": clean_pdf_text(first_names),
        "last_names": clean_pdf_text(last_names),
        "name_on_shirt": clean_pdf_text(name_on_shirt),
        "dob": dob,
        "club": clean_pdf_text(club),
        "height_cm": int(height_cm) if str(height_cm).isdigit() else pd.NA,
        "source_url": source_url,
        "source_version_date": SOURCE_VERSION_DATE,
    }


def parse_pdf_with_pdfplumber(pdf_path: Path, source_url: str) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
            country, fifa_code = find_country_header(text)
            tables = page.extract_tables()
            if not tables:
                raise ValueError(f"No table found for {country} ({fifa_code}).")

            page_records: list[dict[str, Any]] = []
            for table in tables:
                for cells in table:
                    parsed = parse_table_row(cells, country, fifa_code, source_url)
                    if parsed:
                        page_records.append(parsed)

            if len(page_records) != EXPECTED_PLAYERS_PER_COUNTRY:
                raise ValueError(
                    f"Expected {EXPECTED_PLAYERS_PER_COUNTRY} players for "
                    f"{country}, got {len(page_records)}."
                )
            records.extend(page_records)
    return pd.DataFrame(records, columns=SQUAD_COLUMNS)


def parse_text_line(
    line: str,
    country: str,
    fifa_code: str,
    source_url: str,
) -> dict[str, Any] | None:
    """Loose pypdf fallback parser.

    pypdf text does not preserve table columns reliably, so this is deliberately
    only a fallback if pdfplumber table extraction is unavailable.
    """
    match = re.match(
        r"^(\d{1,2})\s+(GK|DF|MF|FW)\s+(.+?)\s+(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(\d{3})$",
        clean_pdf_text(line),
    )
    if not match:
        return None
    squad_no, position, name_blob, dob, club, height_cm = match.groups()
    return {
        "country": country,
        "fifa_code": fifa_code,
        "squad_no": int(squad_no),
        "position": position,
        "player_name": name_blob,
        "first_names": "",
        "last_names": "",
        "name_on_shirt": "",
        "dob": dob,
        "club": club,
        "height_cm": int(height_cm),
        "source_url": source_url,
        "source_version_date": SOURCE_VERSION_DATE,
    }


def parse_pdf_with_pypdf(pdf_path: Path, source_url: str) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    reader = PdfReader(str(pdf_path))
    for page in reader.pages:
        text = page.extract_text() or ""
        country, fifa_code = find_country_header(text)
        page_records = [
            parsed
            for line in text.splitlines()
            if (parsed := parse_text_line(line, country, fifa_code, source_url))
        ]
        if len(page_records) != EXPECTED_PLAYERS_PER_COUNTRY:
            raise ValueError(
                f"pypdf fallback expected {EXPECTED_PLAYERS_PER_COUNTRY} players "
                f"for {country}, got {len(page_records)}."
            )
        records.extend(page_records)
    return pd.DataFrame(records, columns=SQUAD_COLUMNS)


def parse_squad_pdf(pdf_path: Path, source_url: str) -> tuple[pd.DataFrame, str]:
    try:
        return parse_pdf_with_pdfplumber(pdf_path, source_url), "pdfplumber"
    except Exception as pdfplumber_error:
        print(
            f"pdfplumber parse failed, trying pypdf fallback: {pdfplumber_error}",
            file=sys.stderr,
        )
        try:
            return parse_pdf_with_pypdf(pdf_path, source_url), "pypdf"
        except Exception as pypdf_error:
            raise RuntimeError(
                "Both PDF parsers failed. "
                f"pdfplumber={pdfplumber_error}; pypdf={pypdf_error}"
            ) from pypdf_error


def validate_squads(squads: pd.DataFrame) -> dict[str, Any]:
    errors: list[str] = []
    country_count = squads["country"].nunique()
    row_count = len(squads)
    per_country = squads.groupby("country").size()
    invalid_positions = sorted(set(squads["position"]) - VALID_POSITIONS)
    duplicate_no = squads.duplicated(["country", "squad_no"]).sum()
    duplicate_player = squads.duplicated(["country", "player_name", "dob"]).sum()
    spot_check_countries = ["Brazil", "USA", "Spain", "Croatia", "Cabo Verde"]
    missing_spot_checks = [
        country for country in spot_check_countries if country not in set(squads["country"])
    ]

    if country_count != EXPECTED_COUNTRIES:
        errors.append(f"Expected {EXPECTED_COUNTRIES} countries, got {country_count}.")
    if row_count != EXPECTED_PLAYERS:
        errors.append(f"Expected {EXPECTED_PLAYERS} squad rows, got {row_count}.")
    bad_country_counts = per_country[per_country != EXPECTED_PLAYERS_PER_COUNTRY]
    if not bad_country_counts.empty:
        errors.append(
            "Countries without 26 players: "
            + ", ".join(f"{k}={v}" for k, v in bad_country_counts.items())
        )
    if invalid_positions:
        errors.append(f"Invalid positions found: {invalid_positions}.")
    if duplicate_no:
        errors.append(f"Duplicate country+squad_no rows: {duplicate_no}.")
    if duplicate_player:
        errors.append(f"Duplicate country+player_name+dob rows: {duplicate_player}.")
    if missing_spot_checks:
        errors.append(f"Spot-check countries missing: {missing_spot_checks}.")

    validation = {
        "country_count": int(country_count),
        "row_count": int(row_count),
        "players_per_country_ok": bool(bad_country_counts.empty),
        "invalid_positions": invalid_positions,
        "duplicate_country_squad_no_rows": int(duplicate_no),
        "duplicate_country_player_dob_rows": int(duplicate_player),
        "spot_checks": {
            country: int((squads["country"] == country).sum())
            for country in spot_check_countries
        },
        "errors": errors,
    }
    if errors:
        raise ValueError("Squad validation failed: " + " | ".join(errors))
    return validation


def normalize_columns(columns: list[str]) -> dict[str, str]:
    return {normalize_name(column).replace(" ", "_"): column for column in columns}


def first_existing_column(source_columns: dict[str, str], aliases: list[str]) -> str | None:
    for alias in aliases:
        key = normalize_name(alias).replace(" ", "_")
        if key in source_columns:
            return source_columns[key]
    return None


def has_text(value: Any) -> bool:
    return pd.notna(value) and str(value).strip() != ""


def clean_cell(value: Any) -> str:
    return str(value).strip() if has_text(value) else ""


def combine_full_name(row: pd.Series) -> str:
    return re.sub(
        r"\s+",
        " ",
        f"{clean_cell(row.get('first_name'))} {clean_cell(row.get('last_name'))}",
    ).strip()


def choose_display_name(row: pd.Series) -> str:
    for field in ["common_name", "full_name", "long_name", "short_name"]:
        value = clean_cell(row.get(field))
        if value:
            return value
    return ""


def calculate_age_from_dob(value: Any, as_of: str = "2026-06-11") -> int | pd.NA:
    if not has_text(value):
        return pd.NA
    dob = pd.to_datetime(value, errors="coerce")
    if pd.isna(dob):
        return pd.NA
    reference = pd.Timestamp(as_of)
    age = reference.year - dob.year - ((reference.month, reference.day) < (dob.month, dob.day))
    return int(age)


def load_fc26_file(fc26_path: Path) -> pd.DataFrame:
    if not fc26_path.exists():
        raise FileNotFoundError(f"EAFC26 file does not exist: {fc26_path}")

    suffix = fc26_path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(fc26_path) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not csv_names:
                raise ValueError(f"No CSV files found inside EAFC26 zip: {fc26_path}")
            preferred = [
                name
                for name in csv_names
                if Path(name).name.lower() == "ea_fc26_players.csv"
            ]
            selected_name = preferred[0] if preferred else max(
                csv_names, key=lambda name: archive.getinfo(name).file_size
            )
            with archive.open(selected_name) as handle:
                raw = pd.read_csv(handle)
    elif suffix in {".xlsx", ".xlsm", ".xls"}:
        raw = pd.read_excel(fc26_path)
    else:
        for encoding in ["utf-8-sig", "utf-8", "latin-1"]:
            try:
                raw = pd.read_csv(fc26_path, encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raw = pd.read_csv(fc26_path)

    source_columns = normalize_columns(list(raw.columns))
    normalized = pd.DataFrame(index=raw.index)
    for target, aliases in FC26_COLUMN_ALIASES.items():
        source_column = first_existing_column(source_columns, aliases)
        normalized[target] = raw[source_column] if source_column else pd.NA

    normalized["full_name"] = normalized.apply(combine_full_name, axis=1)
    normalized["display_name"] = normalized.apply(choose_display_name, axis=1)

    normalized["long_name"] = normalized["long_name"].where(
        normalized["long_name"].map(has_text), normalized["full_name"]
    )
    normalized["short_name"] = normalized["short_name"].where(
        normalized["short_name"].map(has_text), normalized["display_name"]
    )
    normalized["common_name"] = normalized["common_name"].where(
        normalized["common_name"].map(has_text), pd.NA
    )

    alternate_column = first_existing_column(source_columns, ["alternatePositions", "alternate_positions"])
    if alternate_column:
        normalized["player_positions"] = normalized.apply(
            lambda row: ",".join(
                part
                for part in [
                    clean_cell(row.get("fc26_position")),
                    clean_cell(raw.loc[row.name, alternate_column]),
                ]
                if part
            ),
            axis=1,
        )

    for numeric_col in [
        "overall",
        "potential",
        "age",
        "fc26_height_cm",
        "weight",
        "pace",
        "shooting",
        "passing",
        "dribbling",
        "defending",
        "physical",
        "physic",
    ]:
        normalized[numeric_col] = pd.to_numeric(normalized[numeric_col], errors="coerce")

    if normalized["age"].isna().all():
        normalized["age"] = normalized["fc26_dob"].map(calculate_age_from_dob)

    normalized["physic"] = normalized["physic"].where(
        normalized["physic"].notna(), normalized["physical"]
    )
    normalized["physical"] = normalized["physical"].where(
        normalized["physical"].notna(), normalized["physic"]
    )

    normalized["country_clean"] = normalized["nationality"].map(normalize_country)
    normalized["short_name_clean"] = normalized["short_name"].map(normalize_name)
    normalized["long_name_clean"] = normalized["long_name"].map(normalize_name)
    normalized["common_name_clean"] = normalized["common_name"].map(normalize_name)
    normalized["full_name_clean"] = normalized["full_name"].map(normalize_name)
    normalized["display_name_clean"] = normalized["display_name"].map(normalize_name)
    normalized["last_name_clean"] = normalized["last_name"].map(normalize_name)
    normalized["club_clean"] = normalized["club_name"].map(normalize_club)
    normalized["candidate_name"] = normalized.apply(best_display_name, axis=1)
    normalized["_fc26_dob_date"] = pd.to_datetime(
        normalized["fc26_dob"], format="%m/%d/%Y %I:%M:%S %p", errors="coerce"
    ).dt.date
    normalized["_fc26_height_numeric"] = pd.to_numeric(
        normalized["fc26_height_cm"], errors="coerce"
    )
    normalized["_candidate_name_tokens"] = normalized.apply(
        lambda row: set().union(
            *[
                significant_tokens(row.get(field))
                for field in [
                    "common_name",
                    "full_name",
                    "display_name",
                    "short_name",
                    "long_name",
                ]
            ]
        ),
        axis=1,
    )
    return normalized


def best_display_name(row: pd.Series) -> str:
    for field in ["display_name", "common_name", "full_name", "long_name", "short_name"]:
        value = row.get(field)
        if has_text(value):
            return str(value).strip()
    return ""


def fc26_name_variants(row: pd.Series) -> list[str]:
    return make_name_variants(
        row.get("common_name"),
        row.get("full_name"),
        f"{row.get('first_name', '')} {row.get('last_name', '')}",
        row.get("last_name"),
        row.get("display_name"),
        row.get("short_name"),
        row.get("long_name"),
    )


def fc26_fuzzy_name_variants(row: pd.Series) -> list[str]:
    return make_name_variants(
        row.get("common_name"),
        row.get("full_name"),
        f"{row.get('first_name', '')} {row.get('last_name', '')}",
        row.get("display_name"),
        row.get("short_name"),
        row.get("long_name"),
    )


def fuzzy_name_score(squad_value: str, candidate_value: str) -> float:
    return max(
        fuzz.token_sort_ratio(squad_value, candidate_value),
        fuzz.WRatio(squad_value, candidate_value),
    )


def significant_tokens(value: Any) -> set[str]:
    return {
        token
        for token in normalize_name(value).split()
        if len(token) >= 3 and token not in COMMON_NAME_TOKENS
    }


def primary_token_overlap_ok(squad_row: pd.Series, candidate: pd.Series) -> bool:
    primary_tokens = squad_row.get("_primary_name_tokens")
    if not isinstance(primary_tokens, set):
        primary_tokens = significant_tokens(squad_row.get("player_name")) | significant_tokens(
            squad_row.get("name_on_shirt")
        )

    candidate_tokens = candidate.get("_candidate_name_tokens")
    if not isinstance(candidate_tokens, set):
        candidate_tokens = set()
        for field in ["common_name", "full_name", "display_name", "short_name", "long_name"]:
            candidate_tokens |= significant_tokens(candidate.get(field))

    if not primary_tokens or not candidate_tokens:
        return True

    overlap = primary_tokens & candidate_tokens
    required_overlap = 1 if len(primary_tokens) == 1 else 2
    return len(overlap) >= required_overlap


def parse_squad_date(value: Any) -> pd.Timestamp | pd.NaT:
    return pd.to_datetime(value, format="%d/%m/%Y", errors="coerce")


def parse_fc26_date(value: Any) -> pd.Timestamp | pd.NaT:
    return pd.to_datetime(value, errors="coerce")


def dob_match_score(squad_dob: Any, fc26_dob: Any) -> float:
    squad_date = parse_squad_date(squad_dob)
    fc26_date = parse_fc26_date(fc26_dob)
    if pd.isna(squad_date) or pd.isna(fc26_date):
        return 50.0
    return 100.0 if squad_date.date() == fc26_date.date() else 0.0


def height_match_score(squad_height: Any, fc26_height: Any) -> float:
    squad_numeric = pd.to_numeric(pd.Series([squad_height]), errors="coerce").iloc[0]
    fc26_numeric = pd.to_numeric(pd.Series([fc26_height]), errors="coerce").iloc[0]
    if pd.isna(squad_numeric) or pd.isna(fc26_numeric):
        return 50.0
    diff = abs(float(squad_numeric) - float(fc26_numeric))
    if diff <= 2:
        return 100.0
    if diff <= 5:
        return 70.0
    return 0.0


def precomputed_dob_match_score(squad_row: pd.Series, candidate: pd.Series) -> float:
    squad_date = squad_row.get("_squad_dob_date")
    fc26_date = candidate.get("_fc26_dob_date")
    if pd.isna(squad_date) or pd.isna(fc26_date):
        return 50.0
    return 100.0 if squad_date == fc26_date else 0.0


def precomputed_height_match_score(squad_row: pd.Series, candidate: pd.Series) -> float:
    squad_numeric = squad_row.get("_squad_height_numeric")
    fc26_numeric = candidate.get("_fc26_height_numeric")
    if pd.isna(squad_numeric) or pd.isna(fc26_numeric):
        return 50.0
    diff = abs(float(squad_numeric) - float(fc26_numeric))
    if diff <= 2:
        return 100.0
    if diff <= 5:
        return 70.0
    return 0.0


def position_compatible(squad_position: str, fc26_positions: Any) -> float:
    if pd.isna(fc26_positions):
        return 50.0
    positions = {
        normalize_name(position).upper()
        for position in re.split(r"[,/;| ]+", str(fc26_positions))
        if position.strip()
    }
    if not positions:
        return 50.0
    compatible = POSITION_GROUPS.get(str(squad_position), set())
    return 100.0 if positions & compatible else 0.0


def score_candidate(squad_row: pd.Series, candidate: pd.Series) -> tuple[float, str]:
    squad_player_name = normalize_name(squad_row.get("player_name"))
    squad_shirt_name = normalize_name(squad_row.get("name_on_shirt"))
    candidate_common_name = normalize_name(candidate.get("common_name"))
    candidate_full_name = normalize_name(candidate.get("full_name"))
    candidate_last_name = normalize_name(candidate.get("last_name"))
    candidate_display_name = normalize_name(candidate.get("display_name"))

    exact_checks = [
        (squad_player_name, candidate_common_name, "exact_player_name_common_name"),
        (squad_player_name, candidate_full_name, "exact_player_name_full_name"),
        (squad_shirt_name, candidate_common_name, "exact_shirt_name_common_name"),
        (squad_shirt_name, candidate_last_name, "exact_shirt_name_last_name"),
        (squad_player_name, candidate_display_name, "exact_player_name_display_name"),
        (squad_shirt_name, candidate_display_name, "exact_shirt_name_display_name"),
    ]
    for squad_value, candidate_value, method in exact_checks:
        if squad_value and candidate_value and squad_value == candidate_value:
            name_score = 100.0
            break
    else:
        squad_variants = squad_fuzzy_name_variants(squad_row)
        candidate_variants = fc26_fuzzy_name_variants(candidate)
        if not candidate_variants:
            return 0.0, "no_candidate_name"
        name_score = max(
            fuzzy_name_score(squad_variant, candidate_variant)
            for squad_variant in squad_variants
            for candidate_variant in candidate_variants
            if squad_variant and candidate_variant
        )
        if not primary_token_overlap_ok(squad_row, candidate):
            name_score = min(name_score, 70.0)
        method = "fuzzy_name_country"

    candidate_variants = fc26_name_variants(candidate)
    if not candidate_variants:
        return 0.0, "no_candidate_name"

    squad_club = normalize_club(squad_row.get("club"))
    candidate_club = candidate.get("club_clean") or ""
    club_score = (
        fuzz.token_set_ratio(squad_club, candidate_club)
        if squad_club and candidate_club
        else 50.0
    )
    pos_score = position_compatible(
        str(squad_row.get("position")), candidate.get("player_positions")
    )
    dob_score = precomputed_dob_match_score(squad_row, candidate)
    height_score = precomputed_height_match_score(squad_row, candidate)

    # Name drives the match, while club/position resolve close collisions.
    final_score = (
        (name_score * 0.70)
        + (club_score * 0.10)
        + (pos_score * 0.08)
        + (dob_score * 0.08)
        + (height_score * 0.04)
    )
    return round(float(final_score), 2), method


def confidence_from_score(score: float) -> str:
    if score >= HIGH_CONFIDENCE_SCORE:
        return "high"
    if score >= MEDIUM_CONFIDENCE_SCORE:
        return "medium"
    return "low"


def find_best_match(
    squad_row: pd.Series,
    candidates: pd.DataFrame,
    used_candidate_indexes: set[int],
) -> CandidateMatch:
    available = candidates.loc[~candidates.index.isin(used_candidate_indexes)]
    if available.empty:
        return CandidateMatch(None, "no_country_candidates", 0.0, "none")

    scored: list[tuple[float, str, int]] = []
    for idx, candidate in available.iterrows():
        score, method = score_candidate(squad_row, candidate)
        scored.append((score, method, int(idx)))

    scored.sort(key=lambda item: item[0], reverse=True)
    score, method, idx = scored[0]
    candidate = available.loc[idx]
    confidence = confidence_from_score(score)

    if confidence == "low":
        return CandidateMatch(
            None,
            method,
            score,
            "low",
            best_candidate_name=str(candidate.get("candidate_name", "")),
            best_candidate_country=str(candidate.get("nationality", "")),
            best_candidate_club=str(candidate.get("club_name", "")),
        )

    return CandidateMatch(
        idx,
        method,
        score,
        confidence,
        best_candidate_name=str(candidate.get("candidate_name", "")),
        best_candidate_country=str(candidate.get("nationality", "")),
        best_candidate_club=str(candidate.get("club_name", "")),
    )


def blank_fc26_fields() -> dict[str, Any]:
    return {column: pd.NA for column in FC26_COLUMNS}


def match_fc26(squads: pd.DataFrame, fc26: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    squads = squads.copy()
    squads["_squad_dob_date"] = pd.to_datetime(
        squads["dob"], format="%d/%m/%Y", errors="coerce"
    ).dt.date
    squads["_squad_height_numeric"] = pd.to_numeric(squads["height_cm"], errors="coerce")
    squads["_primary_name_tokens"] = squads.apply(
        lambda row: significant_tokens(row.get("player_name"))
        | significant_tokens(row.get("name_on_shirt")),
        axis=1,
    )

    enriched_rows: list[dict[str, Any]] = []
    unmatched_rows: list[dict[str, Any]] = []
    low_confidence_rows: list[dict[str, Any]] = []
    used_candidate_indexes: set[int] = set()

    fc26_by_country = {
        country: frame
        for country, frame in fc26.groupby("country_clean", dropna=False)
        if country
    }

    for _, squad_row in squads.iterrows():
        squad_base = squad_row.to_dict()
        squad_country = normalize_country(squad_row["country"])
        candidates = fc26_by_country.get(squad_country, pd.DataFrame())
        match = find_best_match(squad_row, candidates, used_candidate_indexes)

        if match.index is None:
            enriched_rows.append({**squad_base, **blank_fc26_fields()})
            unmatched_rows.append(
                {
                    **squad_base,
                    "reason": match.method if match.confidence != "low" else "low_confidence",
                    "best_candidate_name": match.best_candidate_name,
                    "best_candidate_country": match.best_candidate_country,
                    "best_candidate_club": match.best_candidate_club,
                    "best_candidate_score": match.score,
                }
            )
            continue

        used_candidate_indexes.add(match.index)
        candidate = fc26.loc[match.index]
        fc26_values = {
            column: candidate.get(column, pd.NA)
            for column in FC26_COLUMNS
            if column not in {"match_method", "match_score", "match_confidence"}
        }
        fc26_values.update(
            {
                "match_method": match.method,
                "match_score": match.score,
                "match_confidence": match.confidence,
            }
        )
        enriched = {**squad_base, **fc26_values}
        enriched_rows.append(enriched)

        if match.confidence == "medium":
            low_confidence_rows.append(
                {
                    **enriched,
                    "best_candidate_name": match.best_candidate_name,
                    "best_candidate_country": match.best_candidate_country,
                    "best_candidate_club": match.best_candidate_club,
                }
            )

    matched = pd.DataFrame(enriched_rows, columns=MATCHED_COLUMNS)
    unmatched = pd.DataFrame(unmatched_rows, columns=UNMATCHED_COLUMNS)
    low_confidence = pd.DataFrame(low_confidence_rows, columns=LOW_CONFIDENCE_COLUMNS)
    return matched, unmatched, low_confidence


def create_outputs_without_fc26(
    squads: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    matched = squads.copy()
    for column in FC26_COLUMNS:
        matched[column] = pd.NA
    matched = matched[MATCHED_COLUMNS]

    unmatched = squads.copy()
    unmatched["reason"] = "missing_fc26_input"
    unmatched["best_candidate_name"] = ""
    unmatched["best_candidate_country"] = ""
    unmatched["best_candidate_club"] = ""
    unmatched["best_candidate_score"] = pd.NA
    unmatched = unmatched[UNMATCHED_COLUMNS]

    low_confidence = pd.DataFrame(columns=LOW_CONFIDENCE_COLUMNS)
    return matched, unmatched, low_confidence


def create_team_features(matched: pd.DataFrame) -> pd.DataFrame:
    feature_rows: list[dict[str, Any]] = []
    numeric_cols = [
        "overall",
        "potential",
        "age",
        "pace",
        "shooting",
        "passing",
        "dribbling",
        "defending",
        "physical",
        "physic",
    ]
    for col in numeric_cols:
        if col in matched.columns:
            matched[col] = pd.to_numeric(matched[col], errors="coerce")

    for (country, fifa_code), group in matched.groupby(["country", "fifa_code"], sort=False):
        rated = group.dropna(subset=["overall"])
        top_11 = rated.sort_values("overall", ascending=False).head(11)
        top_23 = rated.sort_values("overall", ascending=False).head(23)
        coverage = len(rated) / EXPECTED_PLAYERS_PER_COUNTRY
        feature_rows.append(
            {
                "country": country,
                "fifa_code": fifa_code,
                "squad_players": int(len(group)),
                "fc26_players_matched": int(len(rated)),
                "player_data_coverage": round(float(coverage), 4),
                "player_features_available": int(coverage >= RELIABILITY_THRESHOLD),
                "top_11_avg_overall": top_11["overall"].mean(),
                "top_23_avg_overall": top_23["overall"].mean(),
                "squad_avg_overall": rated["overall"].mean(),
                "squad_avg_age": rated["age"].mean(),
                "avg_pace": rated["pace"].mean(),
                "avg_shooting": rated["shooting"].mean(),
                "avg_passing": rated["passing"].mean(),
                "avg_dribbling": rated["dribbling"].mean(),
                "avg_defending": rated["defending"].mean(),
                "avg_physical": rated["physical"].mean(),
            }
        )
    return pd.DataFrame(feature_rows, columns=TEAM_FEATURE_COLUMNS)


def create_match_quality_report(
    matched: pd.DataFrame,
    unmatched: pd.DataFrame,
    fc26_path: Path | None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (country, fifa_code), group in matched.groupby(["country", "fifa_code"], sort=False):
        country_unmatched = unmatched[unmatched["country"] == country]
        matched_with_rating = pd.to_numeric(group["overall"], errors="coerce").notna().sum()
        high = (group["match_confidence"] == "high").sum()
        medium = (group["match_confidence"] == "medium").sum()
        coverage = matched_with_rating / EXPECTED_PLAYERS_PER_COUNTRY
        rows.append(
            {
                "country": country,
                "fifa_code": fifa_code,
                "squad_players": int(len(group)),
                "fc26_players_matched": int(matched_with_rating),
                "high_confidence_matches": int(high),
                "medium_confidence_matches": int(medium),
                "unmatched_players": int(len(country_unmatched)),
                "player_data_coverage": round(float(coverage), 4),
                "player_features_available": int(coverage >= RELIABILITY_THRESHOLD),
                "below_reliability_threshold": bool(coverage < RELIABILITY_THRESHOLD),
                "status": "matched" if fc26_path else "missing_fc26_input",
            }
        )
    return pd.DataFrame(rows)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False)


def write_sources(
    output_dir: Path,
    pdf_path: Path,
    pdf_source_url: str,
    pdf_parser: str,
    fc26_path: Path | None,
    validation: dict[str, Any],
) -> None:
    sources = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "python_executable_required": PYTHON_EXECUTABLE,
        "fifa_confirmation_article_url": FIFA_CONFIRMATION_ARTICLE_URL,
        "pdf_source_url": pdf_source_url,
        "pdf_source_note": (
            "FIFA official World Cup 2026 SquadLists-English PDF; default URL is "
            "a public mirror of the official 48-page document. Use --pdf-url or "
            "--pdf-path to replace it with FIFA's direct asset URL if available."
        ),
        "source_version_date": SOURCE_VERSION_DATE,
        "pdf_local_path": str(pdf_path),
        "pdf_parser_used": pdf_parser,
        "fc26_path": str(fc26_path) if fc26_path else None,
        "output_dir": str(output_dir),
        "validation": validation,
    }
    (output_dir / "sources.json").write_text(
        json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def print_summary(
    squads: pd.DataFrame,
    matched: pd.DataFrame,
    unmatched: pd.DataFrame,
    output_dir: Path,
) -> None:
    matched_count = pd.to_numeric(matched["overall"], errors="coerce").notna().sum()
    coverage = (matched_count / len(squads) * 100) if len(squads) else 0.0
    print("\nWorld Cup 2026 player database build complete")
    print(f"Countries parsed: {squads['country'].nunique()}")
    print(f"Players parsed: {len(squads)}")
    print(f"FC26 players matched: {matched_count}")
    print(f"Match coverage percentage: {coverage:.2f}%")
    print(f"Unmatched players: {len(unmatched)}")
    print(f"Output folder: {output_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build World Cup 2026 squad and optional EAFC26 player database CSVs."
    )
    parser.add_argument(
        "--fc26-path",
        type=Path,
        default=None,
        help="Optional path to an EAFC26 player ratings CSV/XLSX file.",
    )
    parser.add_argument(
        "--pdf-path",
        type=Path,
        default=None,
        help="Optional local path to FIFA's confirmed squad-list PDF.",
    )
    parser.add_argument(
        "--pdf-url",
        default=DEFAULT_PDF_URL,
        help="PDF URL to download when --pdf-path is not supplied.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output folder. Default: {DEFAULT_OUTPUT_DIR}",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.pdf_path:
        pdf_path = args.pdf_path
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF path does not exist: {pdf_path}")
        pdf_source_url = str(pdf_path)
    else:
        pdf_path = download_pdf(args.pdf_url, output_dir)
        pdf_source_url = args.pdf_url

    squads, pdf_parser = parse_squad_pdf(pdf_path, pdf_source_url)
    squads["_country_sort"] = squads["country"].map(country_sort_key)
    squads = (
        squads.sort_values(["_country_sort", "squad_no"])
        .drop(columns=["_country_sort"])
        .reset_index(drop=True)
    )
    validation = validate_squads(squads)

    if args.fc26_path:
        fc26 = load_fc26_file(args.fc26_path)
        matched, unmatched, low_confidence = match_fc26(squads, fc26)
    else:
        matched, unmatched, low_confidence = create_outputs_without_fc26(squads)

    team_features = create_team_features(matched)
    quality_report = create_match_quality_report(matched, unmatched, args.fc26_path)

    write_csv(squads, output_dir / "squads_2026_confirmed.csv")
    write_csv(matched, output_dir / "squads_2026_fc26_matched.csv")
    write_csv(team_features, output_dir / "team_squad_features_2026.csv")
    write_csv(unmatched, output_dir / "fc26_unmatched_players.csv")
    write_csv(low_confidence, output_dir / "low_confidence_matches.csv")
    write_csv(quality_report, output_dir / "match_quality_report.csv")
    write_sources(output_dir, pdf_path, pdf_source_url, pdf_parser, args.fc26_path, validation)
    print_summary(squads, matched, unmatched, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
