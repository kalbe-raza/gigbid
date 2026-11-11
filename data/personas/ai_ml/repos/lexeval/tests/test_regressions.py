import pytest


@pytest.mark.parametrize("case", load_cases("cases.jsonl"))
def test_clause_extraction(case):
    result = extract(case.contract)
    assert result.clause_type == case.expected
