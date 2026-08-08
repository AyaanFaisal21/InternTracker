from intake.locations import countries_of


def test_workday_style_labels():
    assert countries_of(["US, CA, Santa Clara"]) == ["United States"]
    assert countries_of(["Taiwan, Taipei"]) == ["Taiwan"]
    assert countries_of(["China, Shanghai"]) == ["China"]


def test_state_only_labels_imply_us():
    assert countries_of(["New York, NY (HQ)"]) == ["United States"]
    assert countries_of(["Palo Alto, CA"]) == ["United States"]


def test_bare_city_hints():
    assert countries_of(["Bengaluru"]) == ["India"]
    assert countries_of(["Dublin"]) == ["Ireland"]


def test_georgia_reads_as_us_state():
    assert countries_of(["Atlanta, Georgia"]) == ["United States"]


def test_remote_and_unknown():
    assert countries_of(["Remote"]) == ["Remote"]
    assert countries_of(["Springfield Somewhere"]) == ["Unknown"]


def test_multi_location_posting():
    got = countries_of(["US, CA, Santa Clara", "Toronto", "Remote - Europe"])
    assert got == ["Canada", "Remote", "United States"]
