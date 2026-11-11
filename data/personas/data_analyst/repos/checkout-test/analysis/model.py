import statsmodels.formula.api as smf

fit = smf.ols("log_bookings ~ log_price + C(season) + C(room_type)", data=df).fit()
print(fit.summary())
