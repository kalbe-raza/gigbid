from scipy import stats

t, p = stats.ttest_ind(control_rev, variant_rev, equal_var=False)
