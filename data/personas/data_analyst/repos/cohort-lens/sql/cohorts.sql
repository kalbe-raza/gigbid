SELECT DATE_TRUNC(first_paid, MONTH) AS cohort,
       DATE_DIFF(activity_month, first_paid, MONTH) AS month_n,
       COUNT(DISTINCT customer_id) AS active
FROM `analytics.subscriptions`
GROUP BY 1, 2
