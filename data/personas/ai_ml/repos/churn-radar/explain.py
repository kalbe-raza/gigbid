import shap

explainer = shap.TreeExplainer(model)
shap_values = explainer.shap_values(X_sample)
top_reasons = shap_values.argsort(axis=1)[:, -3:]
