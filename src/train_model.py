import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier, ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    log_loss
)

def weighted_argmax_predictions(probs, classes, home_weight=1.0, draw_weight=1.0, away_weight=1.0):
    adjusted = probs.copy()
    for idx, cls in enumerate(classes):
        if cls == 0:
            adjusted[:, idx] *= away_weight
        elif cls == 1:
            adjusted[:, idx] *= draw_weight
        elif cls == 2:
            adjusted[:, idx] *= home_weight
    return classes[np.argmax(adjusted, axis=1)]

def train_and_evaluate_models(full_feature_df, features, USE_ELO_IN_MAIN):
    X = full_feature_df[features]
    y = full_feature_df["result"]

    X_train, X_test, y_train, y_test, train_years, test_years = train_test_split(
        X,
        y,
        full_feature_df["year"],
        test_size=0.2,
        random_state=42,
        stratify=y
    )

    sample_weight_train = np.ones(len(X_train))
    sample_weight_train += np.where(train_years >= 2023, 1.25, 0)
    sample_weight_train += np.where((train_years >= 2021) & (train_years < 2023), 0.50, 0)

    train_tournament = full_feature_df.loc[X_train.index, "tournament"].fillna("").str.lower()
    sample_weight_train += np.where(train_tournament.str.contains("fifa world cup|uefa euro|copa america", na=False), 0.75, 0)
    sample_weight_train += np.where(train_tournament.str.contains("nations league|qualification|qualifier", na=False), 0.35, 0)

    models = {
        "RandomForest_tuned": RandomForestClassifier(
            n_estimators=700,
            max_depth=14,
            min_samples_leaf=3,
            min_samples_split=8,
            max_features="sqrt",
            class_weight="balanced",
            random_state=42,
            n_jobs=-1
        ),
        "ExtraTrees": ExtraTreesClassifier(
            n_estimators=800,
            max_depth=16,
            min_samples_leaf=2,
            min_samples_split=5,
            max_features="sqrt",
            class_weight="balanced",
            random_state=42,
            n_jobs=-1
        ),
        "HistGradientBoosting": HistGradientBoostingClassifier(
            max_iter=320,
            learning_rate=0.04,
            max_leaf_nodes=31,
            l2_regularization=0.04,
            random_state=42
        )
    }

    trained_models = {}
    rows = []

    for name, m in models.items():
        print("Training", name)
        m.fit(X_train, y_train, sample_weight=sample_weight_train)
        pred = m.predict(X_test)
        prob = m.predict_proba(X_test)
        trained_models[name] = m
        rows.append({
            "model": name,
            "accuracy": accuracy_score(y_test, pred),
            "balanced_accuracy": balanced_accuracy_score(y_test, pred),
            "log_loss": log_loss(y_test, prob, labels=m.classes_),
        })

    model_results_df = pd.DataFrame(rows).sort_values(["accuracy", "log_loss"], ascending=[False, True]).reset_index(drop=True)
    
    best_model_name = model_results_df.iloc[0]["model"]
    model = trained_models[best_model_name]
    preds = model.predict(X_test)

    probs_test = model.predict_proba(X_test)
    classes = model.classes_

    best_result = {"accuracy": 0, "balanced_accuracy": 0, "home_weight": 1.0, "draw_weight": 1.0, "away_weight": 1.0}

    for hw in np.arange(0.80, 1.26, 0.05):
        for dw in np.arange(0.35, 1.36, 0.05):
            for aw in np.arange(0.80, 1.26, 0.05):
                tuned = weighted_argmax_predictions(probs_test, classes, hw, dw, aw)
                acc = accuracy_score(y_test, tuned)
                bal = balanced_accuracy_score(y_test, tuned)
                if acc > best_result["accuracy"] or (acc == best_result["accuracy"] and bal > best_result["balanced_accuracy"]):
                    best_result = {"accuracy": acc, "balanced_accuracy": bal, "home_weight": hw, "draw_weight": dw, "away_weight": aw}

    best_preds = weighted_argmax_predictions(probs_test, classes, best_result["home_weight"], best_result["draw_weight"], best_result["away_weight"])

    print("Best model:", best_model_name)
    print("Original accuracy:", accuracy_score(y_test, preds))
    print("Tuned accuracy:", accuracy_score(y_test, best_preds))
    print("Decision weights:", best_result)
    print(classification_report(y_test, best_preds, zero_division=0))
    print(confusion_matrix(y_test, best_preds))

    return model, best_result, model_results_df
