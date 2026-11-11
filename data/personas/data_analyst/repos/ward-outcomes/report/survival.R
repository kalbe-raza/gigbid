library(survival)
fit <- coxph(Surv(days, readmitted) ~ age + comorbidity + ward, data = patients)
