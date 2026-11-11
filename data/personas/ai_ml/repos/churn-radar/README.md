# churn-radar

Customer churn early-warning model. Built 2023. (Synthetic portfolio repository.)

## Architecture
Engineered 140 usage and recharge features, trained gradient-boosted trees with class weighting and explained each score per customer.

## Tech
- Python
- scikit-learn
- XGBoost
- Machine Learning
- Pandas
- SHAP
- MLflow

## Layout
- `main.py`
- `explain.py`
- `track.py`

## Running
```bash
cp .env.example .env
make run
```
