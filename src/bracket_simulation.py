import re
from collections import defaultdict, deque
import time
import numpy as np
import pandas as pd
from src.data_cleaning import clean_text, team_key, parse_date_series, standardize_column_names, pick_col, to_numeric_clean, minmax_score, group_letter

final_team_recent = {}
strength_lookup = {}
USE_ELO_IN_MAIN = True
model = None
features = []
best_result = {}
knockout_prob_cache = {}
annex_mapping = {}

# Fixed 2026 Round of 32 match slots.
# Third-place slots are resolved later using Annex C.
ROUND_OF_32 = {
    73: ("2A", "2B"),
    74: ("1E", "3_FOR_1E"),
    75: ("1F", "2C"),
    76: ("1C", "2F"),
    77: ("1I", "3_FOR_1I"),
    78: ("2E", "2I"),
    79: ("1A", "3_FOR_1A"),
    80: ("1L", "3_FOR_1L"),
    81: ("1D", "3_FOR_1D"),
    82: ("1G", "3_FOR_1G"),
    83: ("2K", "2L"),
    84: ("1H", "2J"),
    85: ("1B", "3_FOR_1B"),
    86: ("1J", "2H"),
    87: ("1K", "3_FOR_1K"),
    88: ("2D", "2G"),
}

ROUND_OF_16 = {
    89: (74, 77),
    90: (73, 75),
    91: (76, 78),
    92: (79, 80),
    93: (83, 84),
    94: (81, 82),
    95: (86, 88),
    96: (85, 87),
}

QUARTERFINALS = {
    97: (89, 90),
    98: (93, 94),
    99: (91, 92),
    100: (95, 96),
}

SEMIFINALS = {
    101: (97, 98),
    102: (99, 100),
}

FINAL = {104: (101, 102)}

# Annex C columns, in order used by FIFA table:
# 1A vs, 1B vs, 1D vs, 1E vs, 1G vs, 1I vs, 1K vs, 1L vs
ANNEX_SLOT_ORDER = ["1A", "1B", "1D", "1E", "1G", "1I", "1K", "1L"]

SLOT_TO_MATCH = {
    "1A": 79,
    "1B": 85,
    "1D": 81,
    "1E": 74,
    "1G": 82,
    "1I": 77,
    "1K": 87,
    "1L": 80,
}

def extract_group_set(value):
    if pd.isna(value):
        return tuple()
    s = str(value).upper()
    letters = re.findall(r"[A-L]", s)
    # Sometimes row numbers create noise; keep unique in order.
    seen = []
    for x in letters:
        if x not in seen:
            seen.append(x)
    return tuple(sorted(seen))

def extract_third_code(value):
    if pd.isna(value):
        return None
    s = str(value).upper().strip()
    # Accept values like 3E, "3rd Group E", "E"
    m = re.search(r"3\s*([A-L])", s)
    if m:
        return f"3{m.group(1)}"
    m = re.search(r"\b([A-L])\b", s)
    if m:
        return f"3{m.group(1)}"
    return None

def build_annex_mapping(annex_df):
    if annex_df is None:
        raise ValueError("third_place_annex_c_2026.csv was not found. Add it to the Kaggle input for exact third-place mapping.")

    df = standardize_column_names(annex_df)
    print("Standardized Annex C columns:")
    print(df.columns.tolist())

    # Try direct known destination columns first.
    slot_col_candidates = {}
    for slot in ANNEX_SLOT_ORDER:
        candidates = [
            slot.lower(),
            f"{slot.lower()}_vs",
            f"{slot.lower()}_opponent",
            f"slot_{slot.lower()}",
            f"winner_group_{slot[1].lower()}",
            f"group_{slot[1].lower()}_winner",
            f"vs_{slot.lower()}",
        ]
        found = None
        for c in candidates:
            if c in df.columns:
                found = c
                break
        if found is None:
            # fuzzy fallback: column contains slot letters
            for c in df.columns:
                cleaned = c.replace("_", "").lower()
                if slot.lower() in cleaned and ("vs" in cleaned or "opponent" in cleaned or "winner" in cleaned):
                    found = c
                    break
        slot_col_candidates[slot] = found

    # Find column containing the eight qualifying third-place group letters.
    group_col = None
    for c in df.columns:
        if any(x in c for x in ["third", "advance", "qualified", "combination", "groups"]):
            # choose a column whose parsed group set often has 8 groups
            sample_sets = df[c].head(20).apply(extract_group_set)
            if sample_sets.apply(len).max() >= 6:
                group_col = c
                break

    if group_col is None:
        # fallback: first object column with many group letters
        for c in df.columns:
            sample_sets = df[c].head(20).apply(extract_group_set)
            if sample_sets.apply(len).max() >= 6:
                group_col = c
                break

    print("Detected Annex group_col:", group_col)
    print("Detected Annex slot columns:", slot_col_candidates)

    if group_col is None:
        raise ValueError("Could not detect the Annex C qualifying groups column.")

    if any(v is None for v in slot_col_candidates.values()):
        # A common CSV style may have the 8 slot columns directly after the group column.
        cols = list(df.columns)
        group_idx = cols.index(group_col)
        possible_slot_cols = cols[group_idx + 1: group_idx + 9]
        if len(possible_slot_cols) == 8:
            slot_col_candidates = dict(zip(ANNEX_SLOT_ORDER, possible_slot_cols))
            print("Fallback slot columns by position:", slot_col_candidates)
        else:
            raise ValueError("Could not detect all 8 Annex C slot columns. Check third_place_annex_c_2026.csv format.")

    mapping = {}
    for _, row in df.iterrows():
        key = extract_group_set(row[group_col])
        if len(key) != 8:
            continue
        row_map = {}
        for slot, col in slot_col_candidates.items():
            code = extract_third_code(row[col])
            if code is not None:
                row_map[slot] = code
        if len(row_map) == 8:
            mapping[key] = row_map

    if not mapping:
        raise ValueError("Annex C mapping was empty after parsing.")

    print("Annex mappings parsed:", len(mapping))
    return mapping

def get_history_for_team(team_clean):
    recent = final_team_recent.get(team_clean)
    if recent is None or len(recent) == 0:
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

def get_strength_for_team(team_clean):
    data = strength_lookup.get(team_clean, {})
    return {
        "fifa_rank": data.get("adjusted_fifa_rank", data.get("forecast_fifa_rank", np.nan)),
        "raw_fifa_rank": data.get("forecast_fifa_rank", np.nan),
        "fifa_points": data.get("latest_fifa_total_points", 0),
        "elo": data.get("latest_elo", np.nan),
        "power_score": data.get("power_score", 0.5),
        "forecast_rank": data.get("forecast_fifa_rank", np.nan)
    }

def safe_rank_strength(rank):
    if pd.isna(rank) or rank <= 0:
        return 0
    return 1 / rank

def build_future_match_features(home_team_clean, away_team_clean, is_neutral=1, is_world_cup=1, is_friendly=0):
    hh = get_history_for_team(home_team_clean)
    ah = get_history_for_team(away_team_clean)
    hs = get_strength_for_team(home_team_clean)
    aws = get_strength_for_team(away_team_clean)

    home_rank = hs["fifa_rank"]
    away_rank = aws["fifa_rank"]
    home_points = hs["fifa_points"]
    away_points = aws["fifa_points"]

    row = {
        "home_matches_before": hh["matches"],
        "away_matches_before": ah["matches"],
        "matches_before_diff": hh["matches"] - ah["matches"],
        "home_win_rate_before": hh["win_rate"],
        "away_win_rate_before": ah["win_rate"],
        "win_rate_diff": hh["win_rate"] - ah["win_rate"],
        "home_draw_rate_before": hh["draw_rate"],
        "away_draw_rate_before": ah["draw_rate"],
        "draw_rate_diff": hh["draw_rate"] - ah["draw_rate"],
        "home_avg_goals_for_before": hh["avg_goals_for"],
        "away_avg_goals_for_before": ah["avg_goals_for"],
        "avg_goals_for_diff": hh["avg_goals_for"] - ah["avg_goals_for"],
        "home_avg_goals_against_before": hh["avg_goals_against"],
        "away_avg_goals_against_before": ah["avg_goals_against"],
        "avg_goals_against_diff": hh["avg_goals_against"] - ah["avg_goals_against"],
        "home_goal_diff_per_match_before": hh["goal_diff_per_match"],
        "away_goal_diff_per_match_before": ah["goal_diff_per_match"],
        "goal_diff_per_match_diff": hh["goal_diff_per_match"] - ah["goal_diff_per_match"],
        "is_neutral": is_neutral,
        "is_world_cup": is_world_cup,
        "is_friendly": is_friendly,
        "is_qualifier": 0,
        "is_nations_league": 0,
        "is_continental_cup": 0,
        "home_fifa_rank": home_rank,
        "away_fifa_rank": away_rank,
        "fifa_rank_advantage": away_rank - home_rank,
        "rank_gap_abs": abs(home_rank - away_rank),
        "home_rank_strength": safe_rank_strength(home_rank),
        "away_rank_strength": safe_rank_strength(away_rank),
        "rank_strength_diff": safe_rank_strength(home_rank) - safe_rank_strength(away_rank),
        "home_is_top10": int(home_rank <= 10),
        "away_is_top10": int(away_rank <= 10),
        "top10_diff": int(home_rank <= 10) - int(away_rank <= 10),
        "home_is_top25": int(home_rank <= 25),
        "away_is_top25": int(away_rank <= 25),
        "top25_diff": int(home_rank <= 25) - int(away_rank <= 25),
        "home_fifa_total_points": home_points,
        "away_fifa_total_points": away_points,
        "fifa_points_diff": home_points - away_points,
    }

    if USE_ELO_IN_MAIN:
        row["home_elo"] = hs["elo"]
        row["away_elo"] = aws["elo"]
        row["elo_diff"] = hs["elo"] - aws["elo"]
        row["elo_diff_abs"] = abs(row["elo_diff"])
        row["home_elo_strength"] = hs["elo"] / 2000
        row["away_elo_strength"] = aws["elo"] / 2000

    row["attack_form_diff"] = row["avg_goals_for_diff"] - row["avg_goals_against_diff"]
    row["form_score_diff"] = (
        0.55 * row["win_rate_diff"] +
        0.20 * row["draw_rate_diff"] +
        0.25 * row["goal_diff_per_match_diff"]
    )
    return row

def current_power_match_probs(a, b):
    sa = get_strength_for_team(a)
    sb = get_strength_for_team(b)
    diff = sa["power_score"] - sb["power_score"]
    POWER_SLOPE = 3.2
    a_no_draw = 1 / (1 + np.exp(-diff * POWER_SLOPE))
    draw = np.clip(0.27 - abs(diff) * 0.10, 0.17, 0.29)
    return (1 - draw) * a_no_draw, draw, (1 - draw) * (1 - a_no_draw)

def get_team_forecast_rank(team_clean):
    data = strength_lookup.get(team_clean, {})
    rank = data.get("forecast_fifa_rank", data.get("adjusted_fifa_rank", 999))
    return 999 if pd.isna(rank) else float(rank)

def sample_score(result, rng):
    # Keeps scores plausible but simple.
    if result == 2:
        home_goals = int(rng.integers(1, 5))
        away_goals = int(rng.integers(0, home_goals))
    elif result == 0:
        away_goals = int(rng.integers(1, 5))
        home_goals = int(rng.integers(0, away_goals))
    else:
        goals = int(rng.integers(0, 4))
        home_goals = goals
        away_goals = goals
    return home_goals, away_goals

def simulate_group_stage_once(fixture_probs, rng):
    teams = sorted(set(fixture_probs["home_team_clean"]) | set(fixture_probs["away_team_clean"]))
    standings = {
        team: {
            "team_clean": team,
            "points": 0,
            "played": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_for": 0,
            "goals_against": 0,
            "goal_diff": 0,
            "group": None,
            "forecast_fifa_rank": get_team_forecast_rank(team),
        }
        for team in teams
    }
    match_records = []

    for _, match in fixture_probs.sort_values("match_order").iterrows():
        home = match["home_team_clean"]
        away = match["away_team_clean"]
        group = group_letter(match["group"])

        probs = np.array([match["away_win_prob"], match["draw_prob"], match["home_win_prob"]])
        probs = probs / probs.sum()
        result = rng.choice([0, 1, 2], p=probs)
        home_goals, away_goals = sample_score(result, rng)

        standings[home]["group"] = group
        standings[away]["group"] = group

        standings[home]["played"] += 1
        standings[away]["played"] += 1

        standings[home]["goals_for"] += home_goals
        standings[home]["goals_against"] += away_goals
        standings[away]["goals_for"] += away_goals
        standings[away]["goals_against"] += home_goals

        if result == 2:
            standings[home]["points"] += 3
            standings[home]["wins"] += 1
            standings[away]["losses"] += 1
        elif result == 0:
            standings[away]["points"] += 3
            standings[away]["wins"] += 1
            standings[home]["losses"] += 1
        else:
            standings[home]["points"] += 1
            standings[away]["points"] += 1
            standings[home]["draws"] += 1
            standings[away]["draws"] += 1

        match_records.append({
            "group": group,
            "home_team_clean": home,
            "away_team_clean": away,
            "home_goals": home_goals,
            "away_goals": away_goals,
        })

    table = pd.DataFrame(standings.values())
    table["goal_diff"] = table["goals_for"] - table["goals_against"]
    matches_played = pd.DataFrame(match_records)
    return table, matches_played

def h2h_stats_for_tied(tied_teams, group_matches):
    tied_set = set(tied_teams)
    stats = {t: {"h2h_points": 0, "h2h_gd": 0, "h2h_gf": 0} for t in tied_teams}

    for _, m in group_matches.iterrows():
        h, a = m["home_team_clean"], m["away_team_clean"]
        if h not in tied_set or a not in tied_set:
            continue
        hg, ag = m["home_goals"], m["away_goals"]
        stats[h]["h2h_gf"] += hg
        stats[a]["h2h_gf"] += ag
        stats[h]["h2h_gd"] += hg - ag
        stats[a]["h2h_gd"] += ag - hg
        if hg > ag:
            stats[h]["h2h_points"] += 3
        elif hg < ag:
            stats[a]["h2h_points"] += 3
        else:
            stats[h]["h2h_points"] += 1
            stats[a]["h2h_points"] += 1

    return pd.DataFrame([
        {"team_clean": t, **vals}
        for t, vals in stats.items()
    ])

def rank_one_group(group_table, group_matches):
    group_table = group_table.copy()
    ranked_parts = []

    # First split by points, then apply H2H among teams on same points.
    for pts, tied in group_table.groupby("points", sort=False):
        tied = tied.copy()
        if len(tied) > 1:
            h2h = h2h_stats_for_tied(tied["team_clean"].tolist(), group_matches)
            tied = tied.merge(h2h, on="team_clean", how="left")
        else:
            tied["h2h_points"] = 0
            tied["h2h_gd"] = 0
            tied["h2h_gf"] = 0

        tied = tied.sort_values(
            [
                "points",
                "h2h_points",
                "h2h_gd",
                "h2h_gf",
                "goal_diff",
                "goals_for",
                "forecast_fifa_rank",
            ],
            ascending=[False, False, False, False, False, False, True]
        )
        ranked_parts.append(tied)

    ranked = pd.concat(ranked_parts, ignore_index=True)
    ranked = ranked.sort_values(
        [
            "points",
            "h2h_points",
            "h2h_gd",
            "h2h_gf",
            "goal_diff",
            "goals_for",
            "forecast_fifa_rank",
        ],
        ascending=[False, False, False, False, False, False, True]
    ).reset_index(drop=True)

    ranked["group_rank"] = np.arange(1, len(ranked) + 1)
    return ranked

def get_group_stage_qualifiers(group_table, matches_played):
    ranked_groups = []
    group_position_lookup = {}

    for group, group_df in group_table.groupby("group"):
        gm = matches_played[matches_played["group"] == group]
        ranked = rank_one_group(group_df, gm)
        ranked_groups.append(ranked)

        for _, row in ranked.iterrows():
            position_code = f"{int(row['group_rank'])}{group}"
            group_position_lookup[position_code] = row["team_clean"]

    ranked_all = pd.concat(ranked_groups, ignore_index=True)

    top_two = ranked_all[ranked_all["group_rank"] <= 2].copy()

    third_place = ranked_all[ranked_all["group_rank"] == 3].copy()
    best_thirds = third_place.sort_values(
        ["points", "goal_diff", "goals_for", "forecast_fifa_rank"],
        ascending=[False, False, False, True]
    ).head(8).copy()

    qualifiers = pd.concat([top_two, best_thirds], ignore_index=True)

    return qualifiers, ranked_all, best_thirds, group_position_lookup

def resolve_third_place_slots(best_thirds, group_position_lookup, annex_mapping):
    third_groups = tuple(sorted(best_thirds["group"].astype(str).str.upper().tolist()))

    if len(third_groups) != 8:
        raise ValueError(f"Expected 8 best third-place groups, got {third_groups}")

    if third_groups not in annex_mapping:
        raise KeyError(
            f"Annex C mapping not found for third-place group combination: {third_groups}. "
            f"Check third_place_annex_c_2026.csv parsing."
        )

    slot_to_third_code = annex_mapping[third_groups]

    resolved = {}
    for slot, third_code in slot_to_third_code.items():
        team = group_position_lookup.get(third_code)
        if team is None:
            raise KeyError(f"Third-place code {third_code} not found in group_position_lookup.")
        resolved[f"3_FOR_{slot}"] = team

    return resolved, slot_to_third_code

def knockout_prob_uncached(a, b):
    row = build_future_match_features(a, b, is_neutral=1, is_world_cup=1, is_friendly=0)
    X_row = pd.DataFrame([row])[features]

    probs = model.predict_proba(X_row)[0]
    adj = probs.copy()
    for idx, cls in enumerate(model.classes_):
        if cls == 0:
            adj[idx] *= best_result["away_weight"]
        elif cls == 1:
            adj[idx] *= best_result["draw_weight"]
        elif cls == 2:
            adj[idx] *= best_result["home_weight"]
    adj = adj / adj.sum()

    ml_a_win = ml_draw = ml_b_win = 0
    for idx, cls in enumerate(model.classes_):
        if cls == 2:
            ml_a_win = adj[idx]
        elif cls == 1:
            ml_draw = adj[idx]
        elif cls == 0:
            ml_b_win = adj[idx]

    power_a_win, power_draw, power_b_win = current_power_match_probs(a, b)
    sa, sb = get_strength_for_team(a), get_strength_for_team(b)
    diff = sa["power_score"] - sb["power_score"]
    a_no_draw = 1 / (1 + np.exp(-diff * 3.2))

    a_adv_ml = ml_a_win + ml_draw * a_no_draw
    b_adv_ml = ml_b_win + ml_draw * (1 - a_no_draw)
    a_adv_power = power_a_win + power_draw * a_no_draw
    b_adv_power = power_b_win + power_draw * (1 - a_no_draw)

    ML_KNOCKOUT_WEIGHT = 0.55
    POWER_KNOCKOUT_WEIGHT = 0.45

    fa = ML_KNOCKOUT_WEIGHT * a_adv_ml + POWER_KNOCKOUT_WEIGHT * a_adv_power
    fb = ML_KNOCKOUT_WEIGHT * b_adv_ml + POWER_KNOCKOUT_WEIGHT * b_adv_power

    total = fa + fb
    return fa / total, fb / total

def predict_neutral_match_prob(a, b):
    if (a, b) in knockout_prob_cache:
        return knockout_prob_cache[(a, b)]
    if (b, a) in knockout_prob_cache:
        pb, pa = knockout_prob_cache[(b, a)]
        return pa, pb
    return 0.5, 0.5

def play_match(a, b, rng):
    pa, pb = predict_neutral_match_prob(a, b)
    return rng.choice([a, b], p=[pa, pb])

def slot_to_team(slot, group_position_lookup, third_slot_resolution):
    if isinstance(slot, int):
        raise ValueError("slot_to_team expects position slot string, not match number.")
    if slot in group_position_lookup:
        return group_position_lookup[slot]
    if slot in third_slot_resolution:
        return third_slot_resolution[slot]
    raise KeyError(f"Could not resolve slot: {slot}")

def simulate_exact_knockout(group_position_lookup, best_thirds, rng, keep_trace=False):
    third_slot_resolution, annex_used = resolve_third_place_slots(best_thirds, group_position_lookup, annex_mapping)

    winners = {}
    trace = []

    # Round of 32
    for match_no in sorted(ROUND_OF_32):
        s1, s2 = ROUND_OF_32[match_no]
        t1 = slot_to_team(s1, group_position_lookup, third_slot_resolution)
        t2 = slot_to_team(s2, group_position_lookup, third_slot_resolution)
        winner = play_match(t1, t2, rng)
        winners[match_no] = winner
        if keep_trace:
            trace.append({"stage": "Round of 32", "match_no": match_no, "team_a": t1, "team_b": t2, "winner": winner, "slot_a": s1, "slot_b": s2})

    # Round of 16
    for match_no in sorted(ROUND_OF_16):
        m1, m2 = ROUND_OF_16[match_no]
        t1 = winners[m1]
        t2 = winners[m2]
        winner = play_match(t1, t2, rng)
        winners[match_no] = winner
        if keep_trace:
            trace.append({"stage": "Round of 16", "match_no": match_no, "team_a": t1, "team_b": t2, "winner": winner, "slot_a": f"W{m1}", "slot_b": f"W{m2}"})

    # Quarterfinals
    for match_no in sorted(QUARTERFINALS):
        m1, m2 = QUARTERFINALS[match_no]
        t1 = winners[m1]
        t2 = winners[m2]
        winner = play_match(t1, t2, rng)
        winners[match_no] = winner
        if keep_trace:
            trace.append({"stage": "Quarterfinal", "match_no": match_no, "team_a": t1, "team_b": t2, "winner": winner, "slot_a": f"W{m1}", "slot_b": f"W{m2}"})

    # Semifinals
    semifinal_losers = []
    for match_no in sorted(SEMIFINALS):
        m1, m2 = SEMIFINALS[match_no]
        t1 = winners[m1]
        t2 = winners[m2]
        winner = play_match(t1, t2, rng)
        loser = t2 if winner == t1 else t1
        winners[match_no] = winner
        semifinal_losers.append(loser)
        if keep_trace:
            trace.append({"stage": "Semifinal", "match_no": match_no, "team_a": t1, "team_b": t2, "winner": winner, "slot_a": f"W{m1}", "slot_b": f"W{m2}"})

    # Final
    m1, m2 = FINAL[104]
    t1 = winners[m1]
    t2 = winners[m2]
    champion = play_match(t1, t2, rng)
    winners[104] = champion
    if keep_trace:
        trace.append({"stage": "Final", "match_no": 104, "team_a": t1, "team_b": t2, "winner": champion, "slot_a": f"W{m1}", "slot_b": f"W{m2}"})
        return champion, pd.DataFrame(trace), annex_used

    return champion

def simulate_tournament_once_real_bracket(fixture_probs, rng, keep_trace=False):
    group_table, matches_played = simulate_group_stage_once(fixture_probs, rng)
    qualifiers, ranked_all, best_thirds, group_position_lookup = get_group_stage_qualifiers(group_table, matches_played)

    if keep_trace:
        champion, trace, annex_used = simulate_exact_knockout(group_position_lookup, best_thirds, rng, keep_trace=True)
        return champion, ranked_all, best_thirds, trace, annex_used

    champion = simulate_exact_knockout(group_position_lookup, best_thirds, rng, keep_trace=False)
    return champion

def run_monte_carlo_simulation(fixture_features_model, team_strength_2026, n_simulations=5000, seed=2026):
    global knockout_prob_cache
    winner_counts = defaultdict(int)
    stage_counts = defaultdict(lambda: defaultdict(int))

    rng = np.random.default_rng(seed)
    start = time.time()

    for i in range(n_simulations):
        champion, ranked_all, best_thirds, trace, annex_used = simulate_tournament_once_real_bracket(
            fixture_features_model,
            rng,
            keep_trace=True
        )

        champion = str(champion)
        winner_counts[champion] += 1

        # Count stage appearances/wins from trace
        qualifiers = set(trace[trace["stage"] == "Round of 32"]["team_a"]).union(
            set(trace[trace["stage"] == "Round of 32"]["team_b"])
        )

        for t in qualifiers:
            stage_counts[str(t)]["round_of_32"] += 1

        for stage_name, count_name in [
            ("Round of 16", "round_of_16"),
            ("Quarterfinal", "quarterfinal"),
            ("Semifinal", "semifinal"),
            ("Final", "final"),
        ]:
            teams_stage = set(trace[trace["stage"] == stage_name]["team_a"]).union(
                set(trace[trace["stage"] == stage_name]["team_b"])
            )

            for t in teams_stage:
                stage_counts[str(t)][count_name] += 1

        stage_counts[champion]["champion"] += 1

        if (i + 1) % 500 == 0:
            print(f"Completed {i + 1}/{n_simulations} in {time.time() - start:.2f}s")

    winner_probs = pd.DataFrame([
        {
            "team_clean": team,
            "titles": count,
            "win_probability": count / n_simulations
        }
        for team, count in winner_counts.items()
    ])

    team_names = team_strength_2026[["team_clean", "country"]].drop_duplicates()

    winner_probs = winner_probs.merge(
        team_names,
        on="team_clean",
        how="left"
    )

    winner_probs = winner_probs.sort_values(
        "win_probability",
        ascending=False
    ).reset_index(drop=True)

    stage_rows = []

    for team, counts in stage_counts.items():
        row = {"team_clean": team}

        for col in [
            "round_of_32",
            "round_of_16",
            "quarterfinal",
            "semifinal",
            "final",
            "champion"
        ]:
            row[col + "_prob"] = counts[col] / n_simulations

        stage_rows.append(row)

    stage_probs = pd.DataFrame(stage_rows).merge(
        team_names,
        on="team_clean",
        how="left"
    )

    stage_probs = stage_probs.sort_values(
        "champion_prob",
        ascending=False
    ).reset_index(drop=True)

    print("Finished in", round(time.time() - start, 2), "seconds")
    return winner_probs, stage_probs
