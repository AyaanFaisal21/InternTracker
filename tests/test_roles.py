from intake.roles import classify_role


def test_role_classification():
    assert classify_role("Software Engineer Intern") == "SWE"
    assert classify_role("Machine Learning Intern") == "Data/ML"
    assert classify_role("Applied Research Intern, NLP") == "Data/ML"
    assert classify_role("Product Management Intern") == "PM/TPM"
    assert classify_role("Forward Deployed Software Engineer Intern") == "FDE/Solutions"
    assert classify_role("Application Engineer Intern") == "FDE/Solutions"
    assert classify_role("Hardware Technologies Undergrad Engineering Internships") == "Hardware"
    assert classify_role("Quantitative Trading Intern") == "Quant"
    assert classify_role("Culinary Intern") == "Other"
