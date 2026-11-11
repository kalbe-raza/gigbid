import mlflow

mlflow.set_experiment("churn")
with mlflow.start_run():
    mlflow.log_params(params)
    mlflow.log_metric("recall_at_alerts", recall)
    mlflow.sklearn.log_model(model, "model")
