from collections import deque
import numpy as np
import pandas as pd
from src.data_cleaning import clean_text, team_key, parse_date_series, standardize_column_names, pick_col, to_numeric_clean, minmax_score, group_letter

final_team_recent = {}

def build_recent_team_history_features(df, window=12, start_year=2018):
    df = df.copy()
    df = df[df["date"].dt.year >= start_year].sort_values("date").reset_index(drop=True)

    recent_by_team = {}
    rows = []

    def get_recent(team):
        return recent_by_team.get(team, deque(maxlen=window))

    def stats(team):
        recent = get_recent(team)
        if len(recent) == 0:
            return {
                "matches": 0,
                "win_rate": 0.5,
                "draw_rate": 0.25,
                "avg_goals_for": 1.0,
                "avg_goals_against": 1.0,
                "goal_diff_per_match": 0.0,
            }
        wins = sum(1 for m in recent if m["result"] == "win")
        draws = sum(1 for m in recent if m["result"] == "draw")
        gf = sum(m["goals_for"] for m in recent)
        ga = sum(m["goals_against"] for m in recent)
        n = len(recent)
        return {
            "matches": n,
            "win_rate": wins / n,
            "draw_rate": draws / n,
            "avg_goals_for": gf / n,
            "avg_goals_against": ga / n,
            "goal_diff_per_match": (gf - ga) / n,
        }

    for _, row in df.iterrows():
        home = row["home_team_clean"]
        away = row["away_team_clean"]

        if home not in recent_by_team:
            recent_by_team[home] = deque(maxlen=window)
        if away not in recent_by_team:
            recent_by_team[away] = deque(maxlen=window)

        hs = stats(home)
        as_ = stats(away)

        rows.append({
            "date": row["date"],
            "year": row["date"].year,
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "home_team_clean": home,
            "away_team_clean": away,
            "tournament": row["tournament"],

            "home_matches_before": hs["matches"],
            "away_matches_before": as_["matches"],
            "matches_before_diff": hs["matches"] - as_["matches"],

            "home_win_rate_before": hs["win_rate"],
            "away_win_rate_before": as_["win_rate"],
            "win_rate_diff": hs["win_rate"] - as_["win_rate"],

            "home_draw_rate_before": hs["draw_rate"],
            "away_draw_rate_before": as_["draw_rate"],
            "draw_rate_diff": hs["draw_rate"] - as_["draw_rate"],

            "home_avg_goals_for_before": hs["avg_goals_for"],
            "away_avg_goals_for_before": as_["avg_goals_for"],
            "avg_goals_for_diff": hs["avg_goals_for"] - as_["avg_goals_for"],

            "home_avg_goals_against_before": hs["avg_goals_against"],
            "away_avg_goals_against_before": as_["avg_goals_against"],
            "avg_goals_against_diff": hs["avg_goals_against"] - as_["avg_goals_against"],

            "home_goal_diff_per_match_before": hs["goal_diff_per_match"],
            "away_goal_diff_per_match_before": as_["goal_diff_per_match"],
            "goal_diff_per_match_diff": hs["goal_diff_per_match"] - as_["goal_diff_per_match"],

            "is_neutral": row["is_neutral"],
            "is_world_cup": row["is_world_cup"],
            "is_friendly": row["is_friendly"],
            "result": row["result"],
        })

        if row["home_score"] > row["away_score"]:
            hr, ar = "win", "loss"
        elif row["home_score"] < row["away_score"]:
            hr, ar = "loss", "win"
        else:
            hr, ar = "draw", "draw"

        recent_by_team[home].append({"result": hr, "goals_for": row["home_score"], "goals_against": row["away_score"]})
        recent_by_team[away].append({"result": ar, "goals_for": row["away_score"], "goals_against": row["home_score"]})

    return pd.DataFrame(rows), recent_by_team

def prepare_elo(elo_df):
    if elo_df is None:
        return pd.DataFrame(columns=["date", "team_clean", "elo"])
    df = standardize_column_names(elo_df)
    print("Elo columns:", df.columns.tolist())

    date_col = pick_col(df, ["date", "rank_date", "match_date"])
    team_col = pick_col(df, ["team", "country", "country_full", "nation", "name"])
    rating_col = pick_col(df, ["elo", "rating", "elorating", "elo_rating"])

    if date_col is None or team_col is None or rating_col is None:
        print("Could not detect Elo columns.")
        return pd.DataFrame(columns=["date", "team_clean", "elo"])

    out = pd.DataFrame({
        "date": parse_date_series(df[date_col]),
        "team_clean": df[team_col].apply(team_key),
        "elo": to_numeric_clean(df[rating_col]),
    })
    out = out.dropna(subset=["date", "team_clean", "elo"]).sort_values(["team_clean", "date"]).reset_index(drop=True)
    return out

def add_latest_features(match_df, ref_df, side, ref_date_col, value_cols, prefix):
    df = match_df.copy()
    if "_row_id" not in df.columns:
        df["_row_id"] = np.arange(len(df))

    team_col = f"{side}_team_clean"
    left = df[["_row_id", "date", team_col]].rename(columns={team_col: "team_clean"})
    pieces = []

    for team, group in left.groupby("team_clean"):
        group = group.sort_values("date")
        ref_team = ref_df[ref_df["team_clean"] == team].copy().sort_values(ref_date_col)

        if ref_team.empty:
            missing = group[["_row_id"]].copy()
            missing[f"{prefix}_ref_date"] = pd.NaT
            for col in value_cols:
                missing[f"{prefix}_{col}"] = np.nan
            pieces.append(missing)
            continue

        merged = pd.merge_asof(
            group,
            ref_team[[ref_date_col] + value_cols],
            left_on="date",
            right_on=ref_date_col,
            direction="backward"
        )

        keep = ["_row_id", ref_date_col] + value_cols
        merged = merged[keep].rename(columns={ref_date_col: f"{prefix}_ref_date"})
        for col in value_cols:
            merged = merged.rename(columns={col: f"{prefix}_{col}"})
        pieces.append(merged)

    feature_data = pd.concat(pieces, ignore_index=True)
    return df.merge(feature_data, on="_row_id", how="left")

def recent_form_component(team_clean):
    recent = final_team_recent.get(team_clean)
    if recent is None or len(recent) == 0:
        return 0.5
    wins = sum(1 for m in recent if m["result"] == "win")
    draws = sum(1 for m in recent if m["result"] == "draw")
    gf = sum(m["goals_for"] for m in recent)
    ga = sum(m["goals_against"] for m in recent)
    n = len(recent)
    win_rate = wins / n
    draw_rate = draws / n
    gd_score = 1 / (1 + np.exp(-((gf - ga) / n)))
    return 0.60 * win_rate + 0.15 * draw_rate + 0.25 * gd_score
