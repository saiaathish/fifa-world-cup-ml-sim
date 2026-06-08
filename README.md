# FIFA World Cup 2026 Machine Learning Simulation

Predict and simulate the 2026 FIFA World Cup using machine learning, historical match statistics, Elo ratings, FIFA rankings, and programmatic Annex C bracket mappings.

This repository hosts a sports analytics/ML pipeline designed to forecast the performance of teams in the 2026 FIFA World Cup. It uses historic match results, Elo, and form factors to train classifiers and runs a Monte Carlo simulation (5,000 trials) to forecast group standings and knockout bracket progressions.

---

## Project Structure

The repository has the following clean layout:
- `notebooks/fifa_wc_ml_sim.ipynb`: Full step-by-step Jupyter Notebook detailing the data preparation, model training, tuning, and simulation.
- `src/`: Modularized helper files extracted directly from the notebook cells:
  - `data_cleaning.py`: Normalization, abbreviation mappings, and datetime safeties.
  - `feature_engineering.py`: Rolling form metrics, Elo mappings, and temporal merging.
  - `train_model.py`: Multi-class model training, sample weighting, and decision tuning.
  - `bracket_simulation.py`: Group stages, FIFA Annex C qualifiers resolver, and Monte Carlo tree runner.
  - `visualization.py`: Horizontally oriented bar charts for champion likelihoods.
- `outputs/`: Pre-computed CSV files and visualization plots of final simulation results.
- `docs/methodology.md`: In-depth analytical documentation of the ML and simulation strategy.
- `data/README.md`: Documentation on datasets and input folders.
- `requirements.txt`: Project package dependencies.

---

## Datasets

The raw data consists of public datasets that are downloaded or expected in the workspace structure:
1. **International Football Results (1872-2024):** Historic match details.
2. **FIFA World Rankings (1992-2024):** Official national rankings.
3. **World Football Elo Ratings:** Empirical team Elo indicators.
4. **FIFA World Cup 2026 Group Fixtures:** Official group schedules.
5. **FIFA World Cup 2026 Teams & Extra Features:** FC26/squad metrics.
6. **FIFA World Cup Annex C Third-Place Table:** Mapping combinations for advancing 3rd-place teams.

---

## Model Pipeline & Approach

1. **Pre-processing:** Clean team names through abbreviation mappings (`TEAM_ALIASES`) and standardize date dimensions.
2. **Feature Generation:** Compute a 12-match rolling form index (win/draw/loss rates, goal values) and execute historical temporal merges (`merge_asof`) for Elo/FIFA data.
3. **Model Selection:** Fit Random Forest, Extra Trees, and HistGradientBoosting classifiers. We apply sample weights to prioritize recent match outcomes and major competitions.
4. **Boundary Tuning:** Adjust decision thresholds through multiplier grid search, improving test set prediction accuracy to **58.04%** (majority baseline: **47.32%**).
5. **Tournament Simulation:** Run group games, select qualifiers (including 3rd-place combinations mapped via Annex C logic), and run knockout games to crown the winner.

---

## Key Results

The model's final predictions are based on 5,000 simulation runs:
- **Tuned Model Accuracy:** ~58.04%
- **Baseline Accuracy:** 47.32%
- **Top Contenders:** France, England, Argentina, Spain, and Brazil occupy the top tier with the highest champion probabilities.

Visualized results are saved under `outputs/world_cup_2026_winner_probabilities_real_bracket.png`.

---

## How to Run

1. Clone the repository:
   ```bash
   git clone https://github.com/saiaathish/fifa-world-cup-ml-sim.git
   cd fifa-world-cup-ml-sim
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Open and run the Jupyter notebook under `notebooks/fifa_wc_ml_sim.ipynb` to regenerate the predictions and model weights.

---

## Limitations & Future Work

- **Introductory Nature:** This is a portfolio simulation project designed to demonstrate ML pipeline structuring and bracket logic, not an absolute forecasting system.
- **Form Limitations:** Sigmoid form calculations are based on international fixtures and do not account for club-level injuries or mid-season squad adjustments.
- **Future Improvements:** Integrating real-time betting market odds, club-level player tracking data, and advanced deep learning sequence models (LSTMs/Transformers).
