#!/usr/bin/env python3
"""Build the FIFA World Cup 2026 fixture and bracket database.

The dataset combines FIFA's official match schedule/bracket seed order with a
clean fixture listing for venues and local kickoff times. It also extracts
Annex C from FIFA's competition regulations: all 495 possible mappings for the
eight best third-placed teams into the Round of 32.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
from pypdf import PdfReader


DEFAULT_OUTPUT_DIR = Path("/home/saiaathish/Downloads/world_cup_2026_player_database")
SCHEDULE_PDF_URL = (
    "https://digitalhub.fifa.com/asset/4b5d4417-3343-4732-9cdf-14b6662af407/"
    "FWC26-Match-Schedule_English.pdf"
)
REGULATIONS_PDF_URL = (
    "https://digitalhub.fifa.com/m/636f5c9c6f29771f/original/"
    "FWC2026_regulations_EN.pdf"
)
FIFA_SCHEDULE_ARTICLE_URL = (
    "https://inside.fifa.com/organisation/news/"
    "updated-world-cup-2026-match-schedule-now-available"
)
FIFA_FORMAT_ARTICLE_URL = "https://www.fifa.com/en/articles/groups-how-teams-qualify-tie-breakers"
FOURFOURTWO_FIXTURES_URL = (
    "https://www.fourfourtwo.com/competition/world-cup-2026-fixtures-and-results"
)
SOURCE_VERSION_DATE = "2026-04-10"

COUNTRY_BY_TEAM = {
    "Mexico": "Mexico",
    "Canada": "Canada",
    "USA": "USA",
}

GROUPS = {
    "A": [("A1", "MEX", "Mexico"), ("A2", "RSA", "South Africa"), ("A3", "KOR", "Korea Republic"), ("A4", "CZE", "Czechia")],
    "B": [("B1", "CAN", "Canada"), ("B2", "BIH", "Bosnia And Herzegovina"), ("B3", "QAT", "Qatar"), ("B4", "SUI", "Switzerland")],
    "C": [("C1", "BRA", "Brazil"), ("C2", "MAR", "Morocco"), ("C3", "HAI", "Haiti"), ("C4", "SCO", "Scotland")],
    "D": [("D1", "USA", "USA"), ("D2", "PAR", "Paraguay"), ("D3", "AUS", "Australia"), ("D4", "TUR", "Türkiye")],
    "E": [("E1", "GER", "Germany"), ("E2", "CUW", "Curaçao"), ("E3", "CIV", "Côte D'Ivoire"), ("E4", "ECU", "Ecuador")],
    "F": [("F1", "NED", "Netherlands"), ("F2", "JPN", "Japan"), ("F3", "SWE", "Sweden"), ("F4", "TUN", "Tunisia")],
    "G": [("G1", "BEL", "Belgium"), ("G2", "EGY", "Egypt"), ("G3", "IRN", "IR Iran"), ("G4", "NZL", "New Zealand")],
    "H": [("H1", "ESP", "Spain"), ("H2", "CPV", "Cabo Verde"), ("H3", "KSA", "Saudi Arabia"), ("H4", "URU", "Uruguay")],
    "I": [("I1", "FRA", "France"), ("I2", "SEN", "Senegal"), ("I3", "IRQ", "Iraq"), ("I4", "NOR", "Norway")],
    "J": [("J1", "ARG", "Argentina"), ("J2", "ALG", "Algeria"), ("J3", "AUT", "Austria"), ("J4", "JOR", "Jordan")],
    "K": [("K1", "POR", "Portugal"), ("K2", "COD", "Congo DR"), ("K3", "UZB", "Uzbekistan"), ("K4", "COL", "Colombia")],
    "L": [("L1", "ENG", "England"), ("L2", "CRO", "Croatia"), ("L3", "GHA", "Ghana"), ("L4", "PAN", "Panama")],
}

TEAM_BY_CODE = {
    code: {"seed": seed, "team": team, "group": group}
    for group, teams in GROUPS.items()
    for seed, code, team in teams
}

VENUES = {
    "Estadio Azteca": ("Mexico City Stadium", "Mexico City", "Mexico City", "Mexico", "America/Mexico_City"),
    "Estadio Akron": ("Guadalajara Stadium", "Zapopan", "Guadalajara", "Mexico", "America/Mexico_City"),
    "Estadio BBVA": ("Monterrey Stadium", "Guadalupe", "Monterrey", "Mexico", "America/Monterrey"),
    "BMO Field": ("Toronto Stadium", "Toronto", "Toronto", "Canada", "America/Toronto"),
    "BC Place": ("BC Place Vancouver", "Vancouver", "Vancouver", "Canada", "America/Vancouver"),
    "Lumen Field": ("Seattle Stadium", "Seattle", "Seattle", "USA", "America/Los_Angeles"),
    "SoFi Stadium": ("Los Angeles Stadium", "Inglewood", "Los Angeles", "USA", "America/Los_Angeles"),
    "Levi's Stadium": ("San Francisco Bay Area Stadium", "Santa Clara", "San Francisco Bay Area", "USA", "America/Los_Angeles"),
    "Gillette Stadium": ("Boston Stadium", "Foxborough", "Boston", "USA", "America/New_York"),
    "MetLife Stadium": ("New York New Jersey Stadium", "East Rutherford", "New York New Jersey", "USA", "America/New_York"),
    "Lincoln Financial Field": ("Philadelphia Stadium", "Philadelphia", "Philadelphia", "USA", "America/New_York"),
    "Mercedes-Benz Stadium": ("Atlanta Stadium", "Atlanta", "Atlanta", "USA", "America/New_York"),
    "NRG Stadium": ("Houston Stadium", "Houston", "Houston", "USA", "America/Chicago"),
    "AT&T Stadium": ("Dallas Stadium", "Arlington", "Dallas", "USA", "America/Chicago"),
    "Arrowhead Stadium": ("Kansas City Stadium", "Kansas City", "Kansas City", "USA", "America/Chicago"),
    "Hard Rock Stadium": ("Miami Stadium", "Miami Gardens", "Miami", "USA", "America/New_York"),
}

KICKOFF_ET = {
    1: "15:00", 2: "22:00", 3: "15:00", 4: "21:00", 5: "21:00", 6: "00:00", 7: "18:00", 8: "15:00",
    9: "19:00", 10: "13:00", 11: "16:00", 12: "22:00", 13: "18:00", 14: "12:00", 15: "21:00", 16: "15:00",
    17: "15:00", 18: "18:00", 19: "21:00", 20: "00:00", 21: "19:00", 22: "16:00", 23: "13:00", 24: "22:00",
    25: "12:00", 26: "15:00", 27: "18:00", 28: "21:00", 29: "20:30", 30: "18:00", 31: "23:00", 32: "15:00",
    33: "16:00", 34: "20:00", 35: "13:00", 36: "00:00", 37: "18:00", 38: "12:00", 39: "15:00", 40: "21:00",
    41: "20:00", 42: "17:00", 43: "13:00", 44: "23:00", 45: "16:00", 46: "19:00", 47: "13:00", 48: "22:00",
    49: "18:00", 50: "18:00", 51: "15:00", 52: "15:00", 53: "21:00", 54: "21:00", 55: "16:00", 56: "16:00",
    57: "19:00", 58: "19:00", 59: "22:00", 60: "22:00", 61: "15:00", 62: "15:00", 63: "23:00", 64: "23:00",
    65: "20:00", 66: "20:00", 67: "17:00", 68: "17:00", 69: "22:00", 70: "22:00", 71: "19:30", 72: "19:30",
    73: "15:00", 74: "16:30", 75: "21:00", 76: "13:00", 77: "17:00", 78: "13:00", 79: "21:00", 80: "12:00",
    81: "20:00", 82: "16:00", 83: "19:00", 84: "15:00", 85: "23:00", 86: "18:00", 87: "21:30", 88: "14:00",
    89: "17:00", 90: "13:00", 91: "16:00", 92: "20:00", 93: "15:00", 94: "20:00", 95: "12:00", 96: "16:00",
    97: "16:00", 98: "15:00", 99: "17:00", 100: "21:00", 101: "15:00", 102: "15:00", 103: "17:00", 104: "15:00",
}

# match_no, local_date, group, team_a_code, team_b_code, venue_common, local_time
GROUP_STAGE_ROWS = [
    (1, "2026-06-11", "A", "MEX", "RSA", "Estadio Azteca", "13:00"),
    (2, "2026-06-11", "A", "KOR", "CZE", "Estadio Akron", "20:00"),
    (3, "2026-06-12", "B", "CAN", "BIH", "BMO Field", "15:00"),
    (4, "2026-06-12", "D", "USA", "PAR", "SoFi Stadium", "18:00"),
    (5, "2026-06-13", "C", "HAI", "SCO", "MetLife Stadium", "21:00"),
    (6, "2026-06-13", "D", "AUS", "TUR", "BC Place", "21:00"),
    (7, "2026-06-13", "C", "BRA", "MAR", "Gillette Stadium", "18:00"),
    (8, "2026-06-13", "B", "QAT", "SUI", "Levi's Stadium", "12:00"),
    (9, "2026-06-14", "E", "CIV", "ECU", "Lincoln Financial Field", "19:00"),
    (10, "2026-06-14", "E", "GER", "CUW", "NRG Stadium", "12:00"),
    (11, "2026-06-14", "F", "NED", "JPN", "AT&T Stadium", "15:00"),
    (12, "2026-06-14", "F", "SWE", "TUN", "Estadio BBVA", "20:00"),
    (13, "2026-06-15", "H", "KSA", "URU", "Hard Rock Stadium", "18:00"),
    (14, "2026-06-15", "H", "ESP", "CPV", "Mercedes-Benz Stadium", "12:00"),
    (15, "2026-06-15", "G", "IRN", "NZL", "SoFi Stadium", "18:00"),
    (16, "2026-06-15", "G", "BEL", "EGY", "Lumen Field", "12:00"),
    (17, "2026-06-16", "I", "FRA", "SEN", "MetLife Stadium", "15:00"),
    (18, "2026-06-16", "I", "IRQ", "NOR", "Gillette Stadium", "18:00"),
    (19, "2026-06-16", "J", "ARG", "ALG", "Arrowhead Stadium", "20:00"),
    (20, "2026-06-16", "J", "AUT", "JOR", "Levi's Stadium", "21:00"),
    (21, "2026-06-17", "L", "GHA", "PAN", "BMO Field", "19:00"),
    (22, "2026-06-17", "L", "ENG", "CRO", "AT&T Stadium", "15:00"),
    (23, "2026-06-17", "K", "POR", "COD", "NRG Stadium", "12:00"),
    (24, "2026-06-17", "K", "UZB", "COL", "Estadio Azteca", "20:00"),
    (25, "2026-06-18", "A", "CZE", "RSA", "Mercedes-Benz Stadium", "12:00"),
    (26, "2026-06-18", "B", "SUI", "BIH", "SoFi Stadium", "12:00"),
    (27, "2026-06-18", "B", "CAN", "QAT", "BC Place", "15:00"),
    (28, "2026-06-18", "A", "MEX", "KOR", "Estadio Akron", "19:00"),
    (29, "2026-06-19", "C", "BRA", "HAI", "Gillette Stadium", "20:30"),
    (30, "2026-06-19", "C", "SCO", "MAR", "Lincoln Financial Field", "18:00"),
    (31, "2026-06-19", "D", "TUR", "PAR", "Levi's Stadium", "20:00"),
    (32, "2026-06-19", "D", "USA", "AUS", "Lumen Field", "12:00"),
    (33, "2026-06-20", "E", "GER", "CIV", "BMO Field", "16:00"),
    (34, "2026-06-20", "E", "ECU", "CUW", "Arrowhead Stadium", "19:00"),
    (35, "2026-06-20", "F", "NED", "SWE", "NRG Stadium", "12:00"),
    (36, "2026-06-20", "F", "TUN", "JPN", "Estadio BBVA", "22:00"),
    (37, "2026-06-21", "H", "URU", "CPV", "Hard Rock Stadium", "18:00"),
    (38, "2026-06-21", "H", "ESP", "KSA", "Mercedes-Benz Stadium", "12:00"),
    (39, "2026-06-21", "G", "BEL", "IRN", "SoFi Stadium", "12:00"),
    (40, "2026-06-21", "G", "NZL", "EGY", "BC Place", "18:00"),
    (41, "2026-06-22", "I", "NOR", "SEN", "MetLife Stadium", "20:00"),
    (42, "2026-06-22", "I", "FRA", "IRQ", "Lincoln Financial Field", "17:00"),
    (43, "2026-06-22", "J", "ARG", "AUT", "AT&T Stadium", "12:00"),
    (44, "2026-06-22", "J", "JOR", "ALG", "Levi's Stadium", "20:00"),
    (45, "2026-06-23", "L", "ENG", "GHA", "Gillette Stadium", "16:00"),
    (46, "2026-06-23", "L", "PAN", "CRO", "BMO Field", "19:00"),
    (47, "2026-06-23", "K", "POR", "UZB", "NRG Stadium", "12:00"),
    (48, "2026-06-23", "K", "COL", "COD", "Estadio Akron", "20:00"),
    (49, "2026-06-24", "C", "SCO", "BRA", "Hard Rock Stadium", "18:00"),
    (50, "2026-06-24", "C", "MAR", "HAI", "Mercedes-Benz Stadium", "18:00"),
    (51, "2026-06-24", "B", "SUI", "CAN", "BC Place", "12:00"),
    (52, "2026-06-24", "B", "BIH", "QAT", "Lumen Field", "12:00"),
    (53, "2026-06-24", "A", "CZE", "MEX", "Estadio Azteca", "19:00"),
    (54, "2026-06-24", "A", "RSA", "KOR", "Estadio BBVA", "19:00"),
    (55, "2026-06-25", "E", "CUW", "CIV", "Lincoln Financial Field", "16:00"),
    (56, "2026-06-25", "E", "ECU", "GER", "MetLife Stadium", "16:00"),
    (57, "2026-06-25", "F", "JPN", "SWE", "AT&T Stadium", "18:00"),
    (58, "2026-06-25", "F", "TUN", "NED", "Arrowhead Stadium", "18:00"),
    (59, "2026-06-25", "D", "TUR", "USA", "SoFi Stadium", "19:00"),
    (60, "2026-06-25", "D", "PAR", "AUS", "Levi's Stadium", "19:00"),
    (61, "2026-06-26", "I", "NOR", "FRA", "Gillette Stadium", "15:00"),
    (62, "2026-06-26", "I", "SEN", "IRQ", "BMO Field", "15:00"),
    (63, "2026-06-26", "G", "EGY", "IRN", "Lumen Field", "20:00"),
    (64, "2026-06-26", "G", "NZL", "BEL", "BC Place", "20:00"),
    (65, "2026-06-26", "H", "CPV", "KSA", "NRG Stadium", "19:00"),
    (66, "2026-06-26", "H", "URU", "ESP", "Estadio Akron", "18:00"),
    (67, "2026-06-27", "L", "PAN", "ENG", "MetLife Stadium", "17:00"),
    (68, "2026-06-27", "L", "CRO", "GHA", "Lincoln Financial Field", "17:00"),
    (69, "2026-06-27", "J", "ALG", "AUT", "Arrowhead Stadium", "21:00"),
    (70, "2026-06-27", "J", "JOR", "ARG", "AT&T Stadium", "21:00"),
    (71, "2026-06-27", "K", "COL", "POR", "Hard Rock Stadium", "19:30"),
    (72, "2026-06-27", "K", "COD", "UZB", "Mercedes-Benz Stadium", "19:30"),
]

# match_no, stage, local_date, seed_a, seed_b, venue_common
KNOCKOUT_ROWS = [
    (73, "Round of 32", "2026-06-28", "2A", "2B", "SoFi Stadium"),
    (74, "Round of 32", "2026-06-29", "1E", "3ABCDF", "Gillette Stadium"),
    (75, "Round of 32", "2026-06-29", "1F", "2C", "Estadio BBVA"),
    (76, "Round of 32", "2026-06-29", "1C", "2F", "NRG Stadium"),
    (77, "Round of 32", "2026-06-30", "1I", "3CDFGH", "MetLife Stadium"),
    (78, "Round of 32", "2026-06-30", "2E", "2I", "AT&T Stadium"),
    (79, "Round of 32", "2026-06-30", "1A", "3CEFHI", "Estadio Azteca"),
    (80, "Round of 32", "2026-07-01", "1L", "3EHIJK", "Mercedes-Benz Stadium"),
    (81, "Round of 32", "2026-07-01", "1D", "3BEFIJ", "Levi's Stadium"),
    (82, "Round of 32", "2026-07-01", "1G", "3AEHIJ", "Lumen Field"),
    (83, "Round of 32", "2026-07-02", "2K", "2L", "BMO Field"),
    (84, "Round of 32", "2026-07-02", "1H", "2J", "SoFi Stadium"),
    (85, "Round of 32", "2026-07-02", "1B", "3EFGIJ", "BC Place"),
    (86, "Round of 32", "2026-07-03", "1J", "2H", "Hard Rock Stadium"),
    (87, "Round of 32", "2026-07-03", "1K", "3DEIJL", "Arrowhead Stadium"),
    (88, "Round of 32", "2026-07-03", "2D", "2G", "AT&T Stadium"),
    (89, "Round of 16", "2026-07-04", "W74", "W77", "Lincoln Financial Field"),
    (90, "Round of 16", "2026-07-04", "W73", "W75", "NRG Stadium"),
    (91, "Round of 16", "2026-07-05", "W76", "W78", "MetLife Stadium"),
    (92, "Round of 16", "2026-07-05", "W79", "W80", "Estadio Azteca"),
    (93, "Round of 16", "2026-07-06", "W83", "W84", "AT&T Stadium"),
    (94, "Round of 16", "2026-07-06", "W81", "W82", "Lumen Field"),
    (95, "Round of 16", "2026-07-07", "W86", "W88", "Mercedes-Benz Stadium"),
    (96, "Round of 16", "2026-07-07", "W85", "W87", "BC Place"),
    (97, "Quarterfinal", "2026-07-09", "W89", "W90", "Gillette Stadium"),
    (98, "Quarterfinal", "2026-07-10", "W93", "W94", "SoFi Stadium"),
    (99, "Quarterfinal", "2026-07-11", "W91", "W92", "Hard Rock Stadium"),
    (100, "Quarterfinal", "2026-07-11", "W95", "W96", "Arrowhead Stadium"),
    (101, "Semifinal", "2026-07-14", "W97", "W98", "AT&T Stadium"),
    (102, "Semifinal", "2026-07-15", "W99", "W100", "Mercedes-Benz Stadium"),
    (103, "Third Place", "2026-07-18", "L101", "L102", "Hard Rock Stadium"),
    (104, "Final", "2026-07-19", "W101", "W102", "MetLife Stadium"),
]


def download(url: str, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        return target
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        target.write_bytes(urlopen(request, timeout=60).read())
    except URLError as exc:
        raise RuntimeError(f"Could not download {url}: {exc}") from exc
    return target


def venue_fields(venue_common: str) -> dict[str, str]:
    official, city, host_city, country, timezone_name = VENUES[venue_common]
    return {
        "venue_common": venue_common,
        "venue_fifa": official,
        "city": city,
        "host_city": host_city,
        "host_country": country,
        "timezone": timezone_name,
    }


def kickoff_datetimes(local_date: str, local_time: str, timezone_name: str) -> dict[str, str]:
    local_dt = datetime.fromisoformat(f"{local_date}T{local_time}:00").replace(
        tzinfo=ZoneInfo(timezone_name)
    )
    et_dt = local_dt.astimezone(ZoneInfo("America/New_York"))
    utc_dt = local_dt.astimezone(timezone.utc)
    return {
        "match_date_local": local_dt.date().isoformat(),
        "kickoff_local": local_dt.strftime("%H:%M"),
        "kickoff_et_date": et_dt.date().isoformat(),
        "kickoff_et": et_dt.strftime("%H:%M"),
        "kickoff_utc": utc_dt.isoformat().replace("+00:00", "Z"),
    }


def kickoff_from_et(local_date: str, kickoff_et: str, timezone_name: str) -> dict[str, str]:
    et_dt = datetime.fromisoformat(f"{local_date}T{kickoff_et}:00").replace(
        tzinfo=ZoneInfo("America/New_York")
    )
    local_dt = et_dt.astimezone(ZoneInfo(timezone_name))
    utc_dt = et_dt.astimezone(timezone.utc)
    return {
        "match_date_local": local_dt.date().isoformat(),
        "kickoff_local": local_dt.strftime("%H:%M"),
        "kickoff_et_date": et_dt.date().isoformat(),
        "kickoff_et": et_dt.strftime("%H:%M"),
        "kickoff_utc": utc_dt.isoformat().replace("+00:00", "Z"),
    }


def is_neutral(team_a: str | None, team_b: str | None, host_country: str) -> int:
    for team in [team_a, team_b]:
        if COUNTRY_BY_TEAM.get(team) == host_country:
            return 0
    return 1


def group_assignment_rows() -> pd.DataFrame:
    rows = []
    for group, teams in GROUPS.items():
        for seed, code, team in teams:
            rows.append(
                {
                    "group": group,
                    "seed": seed,
                    "team_code": code,
                    "team": team,
                    "group_position": int(seed[1]),
                }
            )
    return pd.DataFrame(rows)


def group_fixture_rows() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for match_no, date, group, code_a, code_b, venue_common, local_time in GROUP_STAGE_ROWS:
        venue = venue_fields(venue_common)
        dt = kickoff_datetimes(date, local_time, venue["timezone"])
        team_a = TEAM_BY_CODE[code_a]
        team_b = TEAM_BY_CODE[code_b]
        rows.append(
            {
                "match_no": match_no,
                "stage": "Group",
                "round": "Group Stage",
                "group": group,
                "team_a_seed": team_a["seed"],
                "team_b_seed": team_b["seed"],
                "team_a_code": code_a,
                "team_b_code": code_b,
                "team_a": team_a["team"],
                "team_b": team_b["team"],
                **dt,
                **venue,
                "neutral_site": is_neutral(team_a["team"], team_b["team"], venue["host_country"]),
                "source_url": FOURFOURTWO_FIXTURES_URL,
                "official_schedule_url": SCHEDULE_PDF_URL,
                "source_version_date": SOURCE_VERSION_DATE,
            }
        )
    return pd.DataFrame(rows)


def knockout_rows() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for match_no, stage, date, seed_a, seed_b, venue_common in KNOCKOUT_ROWS:
        venue = venue_fields(venue_common)
        dt = kickoff_from_et(date, KICKOFF_ET[match_no], venue["timezone"])
        rows.append(
            {
                "match_no": match_no,
                "stage": stage,
                "round": stage,
                "group": "",
                "team_a_seed": seed_a,
                "team_b_seed": seed_b,
                "team_a_code": "",
                "team_b_code": "",
                "team_a": seed_a,
                "team_b": seed_b,
                **dt,
                **venue,
                "neutral_site": 1,
                "source_url": SCHEDULE_PDF_URL,
                "official_schedule_url": SCHEDULE_PDF_URL,
                "source_version_date": SOURCE_VERSION_DATE,
            }
        )
    return pd.DataFrame(rows)


def third_place_slot_rows() -> pd.DataFrame:
    slot_map = [
        (74, "1E", "3ABCDF", "A,B,C,D,F", "slot_1E"),
        (77, "1I", "3CDFGH", "C,D,F,G,H", "slot_1I"),
        (79, "1A", "3CEFHI", "C,E,F,H,I", "slot_1A"),
        (80, "1L", "3EHIJK", "E,H,I,J,K", "slot_1L"),
        (81, "1D", "3BEFIJ", "B,E,F,I,J", "slot_1D"),
        (82, "1G", "3AEHIJ", "A,E,H,I,J", "slot_1G"),
        (85, "1B", "3EFGIJ", "E,F,G,I,J", "slot_1B"),
        (87, "1K", "3DEIJL", "D,E,I,J,L", "slot_1K"),
    ]
    return pd.DataFrame(
        [
            {
                "round": "Round of 32",
                "match_no": match_no,
                "group_winner_seed": winner_seed,
                "third_place_placeholder": placeholder,
                "candidate_third_place_groups": groups,
                "annex_c_assignment_column": annex_column,
            }
            for match_no, winner_seed, placeholder, groups, annex_column in slot_map
        ]
    )


def ranking_rule_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            (1, "points", "Most points obtained in all group matches."),
            (2, "goal_difference", "Superior goal difference in all group matches."),
            (3, "goals_scored", "Greatest number of goals scored in all group matches."),
            (4, "team_conduct_score", "Highest team conduct score / fair-play conduct score."),
            (5, "fifa_ranking_or_lots", "Final FIFA procedure if still tied; keep this explicit in simulation config."),
        ],
        columns=["rank_order", "criterion", "description"],
    )


def annex_c_rows(regulations_pdf_path: Path) -> pd.DataFrame:
    reader = PdfReader(str(regulations_pdf_path))
    rows: list[dict[str, Any]] = []
    header = ["slot_1A", "slot_1B", "slot_1D", "slot_1E", "slot_1G", "slot_1I", "slot_1K", "slot_1L"]
    pattern = re.compile(r"^(\d{1,3})\s+((?:3[A-L]\s+){7}3[A-L])$")
    for page_index in range(79, min(98, len(reader.pages))):
        text = reader.pages[page_index].extract_text() or ""
        for line in text.splitlines():
            line = " ".join(line.split())
            match = pattern.match(line)
            if not match:
                continue
            option = int(match.group(1))
            values = match.group(2).split()
            qualified_groups = sorted(value[1:] for value in values)
            row = {
                "option": option,
                "qualified_third_groups_key": "".join(qualified_groups),
                "qualified_third_groups": ",".join(qualified_groups),
            }
            row.update(dict(zip(header, values, strict=True)))
            row.update(
                {
                    "match_79_1A_assignment": row["slot_1A"],
                    "match_85_1B_assignment": row["slot_1B"],
                    "match_81_1D_assignment": row["slot_1D"],
                    "match_74_1E_assignment": row["slot_1E"],
                    "match_82_1G_assignment": row["slot_1G"],
                    "match_77_1I_assignment": row["slot_1I"],
                    "match_87_1K_assignment": row["slot_1K"],
                    "match_80_1L_assignment": row["slot_1L"],
                }
            )
            rows.append(row)

    df = pd.DataFrame(rows).sort_values("option").reset_index(drop=True)
    if len(df) != 495 or set(df["option"]) != set(range(1, 496)):
        raise ValueError(f"Annex C extraction failed: expected 495 options, got {len(df)}")
    return df


def validate(fixtures: pd.DataFrame, groups: pd.DataFrame, annex_c: pd.DataFrame) -> dict[str, Any]:
    errors = []
    group_stage = fixtures[fixtures["stage"] == "Group"]
    knockout = fixtures[fixtures["stage"] != "Group"]
    if len(fixtures) != 104:
        errors.append(f"Expected 104 fixtures, got {len(fixtures)}.")
    if len(group_stage) != 72:
        errors.append(f"Expected 72 group fixtures, got {len(group_stage)}.")
    if len(knockout) != 32:
        errors.append(f"Expected 32 knockout fixtures, got {len(knockout)}.")
    if groups["team"].nunique() != 48:
        errors.append(f"Expected 48 teams in group assignments, got {groups['team'].nunique()}.")
    if sorted(group_stage["group"].unique()) != list("ABCDEFGHIJKL"):
        errors.append("Group fixtures do not cover groups A through L.")
    per_group = group_stage.groupby("group").size()
    bad_group_counts = per_group[per_group != 6]
    if not bad_group_counts.empty:
        errors.append("Groups without six fixtures: " + bad_group_counts.to_string())
    if fixtures["match_no"].duplicated().any():
        errors.append("Duplicate match_no values found.")
    if sorted(fixtures["match_no"].tolist()) != list(range(1, 105)):
        errors.append("Match numbers are not exactly 1 through 104.")
    if len(annex_c) != 495:
        errors.append(f"Expected 495 Annex C rows, got {len(annex_c)}.")
    if errors:
        raise ValueError("Fixture validation failed: " + " | ".join(errors))
    return {
        "fixture_rows": int(len(fixtures)),
        "group_stage_rows": int(len(group_stage)),
        "knockout_rows": int(len(knockout)),
        "teams": int(groups["team"].nunique()),
        "groups": list("ABCDEFGHIJKL"),
        "group_matches_per_group": {k: int(v) for k, v in per_group.items()},
        "annex_c_rows": int(len(annex_c)),
        "errors": [],
    }


def write_sources(output_dir: Path, schedule_pdf: Path, regulations_pdf: Path, validation: dict[str, Any]) -> None:
    sources = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(output_dir),
        "source_version_date": SOURCE_VERSION_DATE,
        "fifa_schedule_article_url": FIFA_SCHEDULE_ARTICLE_URL,
        "fifa_schedule_pdf_url": SCHEDULE_PDF_URL,
        "fifa_schedule_pdf_local_path": str(schedule_pdf),
        "fifa_regulations_pdf_url": REGULATIONS_PDF_URL,
        "fifa_regulations_pdf_local_path": str(regulations_pdf),
        "fifa_format_article_url": FIFA_FORMAT_ARTICLE_URL,
        "fixture_listing_crosscheck_url": FOURFOURTWO_FIXTURES_URL,
        "notes": [
            "Match numbers, kickoff ET times, groups and knockout seeds are from FIFA's official match schedule PDF.",
            "Venue/common stadium names and local kickoff times were cross-checked against FourFourTwo's full fixture listing.",
            "Annex C is extracted from FIFA World Cup 26 regulations and contains all 495 third-place placement options.",
        ],
        "validation": validation,
    }
    (output_dir / "fixture_sources.json").write_text(
        json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build World Cup 2026 fixture/bracket CSV datasets.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--schedule-pdf-path", type=Path, default=None)
    parser.add_argument("--regulations-pdf-path", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir: Path = args.output_dir
    source_dir = output_dir / "_source"
    output_dir.mkdir(parents=True, exist_ok=True)

    schedule_pdf = args.schedule_pdf_path or download(
        SCHEDULE_PDF_URL, source_dir / "FWC26-Match-Schedule_English.pdf"
    )
    regulations_pdf = args.regulations_pdf_path or download(
        REGULATIONS_PDF_URL, source_dir / "FWC2026_regulations_EN.pdf"
    )

    groups = group_assignment_rows()
    group_fixtures = group_fixture_rows()
    knockout = knockout_rows()
    fixtures = pd.concat([group_fixtures, knockout], ignore_index=True).sort_values("match_no")
    third_place_slots = third_place_slot_rows()
    third_place_rules = ranking_rule_rows()
    annex_c = annex_c_rows(regulations_pdf)
    validation = validate(fixtures, groups, annex_c)

    groups.to_csv(output_dir / "groups_2026.csv", index=False)
    fixtures.to_csv(output_dir / "fixtures_2026.csv", index=False)
    group_fixtures.to_csv(output_dir / "group_stage_fixtures_2026.csv", index=False)
    knockout.to_csv(output_dir / "knockout_bracket_2026.csv", index=False)
    third_place_slots.to_csv(output_dir / "third_place_slots_2026.csv", index=False)
    third_place_rules.to_csv(output_dir / "third_place_ranking_rules_2026.csv", index=False)
    annex_c.to_csv(output_dir / "third_place_annex_c_2026.csv", index=False)
    write_sources(output_dir, schedule_pdf, regulations_pdf, validation)

    print("\nWorld Cup 2026 fixture database build complete")
    print(f"Fixtures: {len(fixtures)}")
    print(f"Group-stage fixtures: {len(group_fixtures)}")
    print(f"Knockout fixtures: {len(knockout)}")
    print(f"Teams: {groups['team'].nunique()}")
    print(f"Annex C third-place mappings: {len(annex_c)}")
    print(f"Output folder: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
