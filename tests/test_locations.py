from intake.locations import countries_of, is_remote


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


def test_no_pseudo_countries():
    # Remote is its own axis and unmatched labels add nothing: an empty
    # list means region unknown, which the board shows in every region.
    assert countries_of(["Remote"]) == []
    assert countries_of(["Springfield Somewhere"]) == []


def test_multi_location_posting():
    got = countries_of(["US, CA, Santa Clara", "Toronto", "Remote - Europe"])
    assert got == ["Canada", "United States"]


def test_us_city_abbreviations():
    assert countries_of(["SF", "NYC"]) == ["United States"]


def test_remote_pure():
    assert is_remote(["Remote"]) is True
    assert is_remote(["remote"]) is True
    assert is_remote(["Remote - US"]) is True
    assert is_remote(["US Remote"]) is True


def test_remote_virtual_and_wfh_phrasings():
    assert is_remote(["Virtual"]) is True
    assert is_remote(["Work from home"]) is True
    assert is_remote(["Work-From-Home"]) is True
    assert is_remote(["WFH"]) is True
    assert is_remote(["Telecommute"]) is True


def test_remote_hybrid_keeps_country():
    # A remote+country hybrid is both remote and its country.
    assert countries_of(["Remote - US"]) == ["United States"]
    assert countries_of(["US Remote"]) == ["United States"]
    assert countries_of(["Remote, Canada"]) == ["Canada"]
    assert is_remote(["Remote, Canada"]) is True
    # "Hybrid" alone is office-tied, and the place still resolves.
    assert countries_of(["Hybrid - Santa Clara"]) == ["United States"]
    assert is_remote(["Hybrid - Santa Clara"]) is False


def test_remote_negatives():
    assert is_remote(["Remoteville"]) is False
    assert countries_of(["Remoteville"]) == []
    assert is_remote(["New York, NY (HQ)"]) is False
    assert is_remote([]) is False


def test_remote_in_country_derives_and_stays_remote():
    # "Remote in USA" left an "in usa" token that matched no alias, so 33
    # prod rows served no country. Connector strip exposes the country.
    for label, country in [
        ("Remote in USA", "United States"),
        ("Remote in US", "United States"),
        ("Remote in Canada", "Canada"),
    ]:
        assert countries_of([label]) == [country]
        assert is_remote([label]) is True
    assert countries_of(["Remote in USA", "Remote in Canada"]) == [
        "Canada", "United States",
    ]


def test_leading_connector_variants():
    assert countries_of(["Based in USA"]) == ["United States"]
    assert countries_of(["Remote within Canada"]) == ["Canada"]
    assert countries_of(["Remote across the US"]) == ["United States"]


def test_canadian_province_codes():
    assert countries_of(["Calgary, AB"]) == ["Canada"]
    for code in ("AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU",
                 "ON", "PE", "QC", "SK", "YT"):
        assert countries_of([f"Somewhere, {code}"]) == ["Canada"], code


def test_province_codes_stay_case_sensitive():
    # An "On-site" label splits to an "on" token; lowercase must never
    # read as Ontario.
    assert countries_of(["On-site"]) == []
    assert countries_of(["on-site"]) == []


def test_south_sf_is_us():
    assert countries_of(["South SF"]) == ["United States"]
    assert countries_of(["South San Francisco"]) == ["United States"]


def test_multiple_us_cities_is_us():
    assert countries_of(["Multiple US Cities"]) == ["United States"]


def test_virtual_stays_remote_only():
    # remote axis yes, but no country invented
    assert is_remote(["Virtual"]) is True
    assert countries_of(["Virtual"]) == []
