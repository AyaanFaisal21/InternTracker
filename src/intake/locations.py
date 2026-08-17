"""Country and remote extraction from free-text location labels.

ATS location strings have no schema: "US, CA, Santa Clara",
"New York, NY (HQ)", "Bengaluru", "Taiwan, Taipei", "Remote". Strategy:
tokenize the label, then match tokens against (1) a canonical country
list with aliases, (2) US state names/codes (US labels rarely name the
country), (3) a small tech-hub city map for bare-city labels.

"Georgia" is treated as the US state, not the country — in tech
recruiting data that reading is almost always correct.

Remote is a separate axis, not a country: `is_remote` flags labels like
"Remote", "US Remote", "Virtual", "Work from home". A hybrid label
("Remote - US") is both remote and its country. Labels that match no
country contribute nothing, so the board can treat an empty country list
as country-unknown.
"""

from __future__ import annotations

import re

# canonical name -> aliases (lowercase). Bare ISO-3166 list plus the
# alias spellings that actually appear in job boards.
_COUNTRIES: dict[str, list[str]] = {
    "United States": ["us", "usa", "u.s.", "u.s.a.", "united states", "united states of america"],
    "United Kingdom": ["uk", "u.k.", "united kingdom", "england", "scotland", "wales", "great britain"],
    "Canada": ["canada"],
    "India": ["india"],
    "China": ["china", "prc"],
    "Taiwan": ["taiwan"],
    "Japan": ["japan"],
    "South Korea": ["south korea", "korea", "republic of korea"],
    "Singapore": ["singapore"],
    "Australia": ["australia"],
    "New Zealand": ["new zealand"],
    "Germany": ["germany", "deutschland"],
    "France": ["france"],
    "Ireland": ["ireland"],
    "Netherlands": ["netherlands", "the netherlands", "holland"],
    "Switzerland": ["switzerland"],
    "Sweden": ["sweden"],
    "Norway": ["norway"],
    "Denmark": ["denmark"],
    "Finland": ["finland"],
    "Poland": ["poland"],
    "Czech Republic": ["czech republic", "czechia"],
    "Austria": ["austria"],
    "Belgium": ["belgium"],
    "Spain": ["spain"],
    "Portugal": ["portugal"],
    "Italy": ["italy"],
    "Greece": ["greece"],
    "Romania": ["romania"],
    "Hungary": ["hungary"],
    "Bulgaria": ["bulgaria"],
    "Croatia": ["croatia"],
    "Serbia": ["serbia"],
    "Slovakia": ["slovakia"],
    "Slovenia": ["slovenia"],
    "Estonia": ["estonia"],
    "Latvia": ["latvia"],
    "Lithuania": ["lithuania"],
    "Ukraine": ["ukraine"],
    "Turkey": ["turkey", "turkiye"],
    "Israel": ["israel"],
    "United Arab Emirates": ["uae", "united arab emirates", "dubai"],
    "Saudi Arabia": ["saudi arabia"],
    "Qatar": ["qatar"],
    "Egypt": ["egypt"],
    "South Africa": ["south africa"],
    "Nigeria": ["nigeria"],
    "Kenya": ["kenya"],
    "Brazil": ["brazil", "brasil"],
    "Mexico": ["mexico"],
    "Argentina": ["argentina"],
    "Chile": ["chile"],
    "Colombia": ["colombia"],
    "Peru": ["peru"],
    "Costa Rica": ["costa rica"],
    "Vietnam": ["vietnam"],
    "Thailand": ["thailand"],
    "Malaysia": ["malaysia"],
    "Indonesia": ["indonesia"],
    "Philippines": ["philippines"],
    "Pakistan": ["pakistan"],
    "Bangladesh": ["bangladesh"],
    "Sri Lanka": ["sri lanka"],
    "Hong Kong": ["hong kong"],
    "Luxembourg": ["luxembourg"],
    "Iceland": ["iceland"],
    "Russia": ["russia"],
    "Armenia": ["armenia"],
    "Kazakhstan": ["kazakhstan"],
}

_ALIAS_TO_COUNTRY: dict[str, str] = {
    alias: canonical for canonical, aliases in _COUNTRIES.items() for alias in aliases
}

_US_STATE_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
}
_US_STATE_NAMES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming",
}

# Canadian province and territory codes ("Calgary, AB" -> Canada). Matched
# case sensitively against the original token: a lowercase "on" is the
# English word (an "On-site" label splits to an "on" token), while boards
# write the codes uppercase.
_CA_PROVINCE_CODES = {
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
}

# Bare-city labels seen on boards that never name the country.
_CITY_HINTS: dict[str, str] = {
    # US hubs and their list-style abbreviations
    "sf": "United States", "nyc": "United States", "la": "United States",
    "bay area": "United States", "san francisco": "United States",
    "los angeles": "United States", "san jose": "United States",
    "santa clara": "United States", "palo alto": "United States",
    "mountain view": "United States", "menlo park": "United States",
    "sunnyvale": "United States", "cupertino": "United States",
    "redmond": "United States", "bellevue": "United States",
    "south sf": "United States", "south san francisco": "United States",
    "seattle": "United States", "boston": "United States",
    "austin": "United States", "chicago": "United States",
    "atlanta": "United States", "denver": "United States",
    "miami": "United States", "dallas": "United States",
    "houston": "United States", "san diego": "United States",
    "philadelphia": "United States", "pittsburgh": "United States",
    "phoenix": "United States", "portland": "United States",
    "washington dc": "United States", "dc": "United States",
    "bengaluru": "India", "bangalore": "India", "mumbai": "India",
    "hyderabad": "India", "chennai": "India", "pune": "India",
    "new delhi": "India", "gurgaon": "India", "gurugram": "India", "noida": "India",
    "dublin": "Ireland", "london": "United Kingdom", "cambridge uk": "United Kingdom",
    "toronto": "Canada", "vancouver": "Canada", "montreal": "Canada",
    "waterloo": "Canada", "ottawa": "Canada",
    "tel aviv": "Israel", "zurich": "Switzerland", "amsterdam": "Netherlands",
    "berlin": "Germany", "munich": "Germany", "paris": "France",
    "tokyo": "Japan", "seoul": "South Korea", "taipei": "Taiwan",
    "shanghai": "China", "beijing": "China", "shenzhen": "China",
    "sydney": "Australia", "melbourne": "Australia",
    "warsaw": "Poland", "stockholm": "Sweden", "sao paulo": "Brazil",
    "mexico city": "Mexico", "dubai": "United Arab Emirates",
}

_SPLIT_RE = re.compile(r"[,;/()–—-]| - ")

# Remote-work markers. Word-bounded, so "Remoteville" stays a place name.
_REMOTE_RE = re.compile(
    r"\b(?:remote|virtual|wfh|work[\s-]+from[\s-]+home|telecommut\w*)\b",
    re.IGNORECASE,
)

# Connector words a remote-marker strip leaves at the front of a token:
# "Remote in USA" tokenizes to "in USA", which matches no alias until the
# connector goes. Stripped before every lookup.
_CONNECTOR_RE = re.compile(
    r"^(?:based\s+in|within|across|in)\s+(?:the\s+)?", re.IGNORECASE
)

# Last-resort label scan for US phrasings the tokenizer cannot resolve
# ("Multiple US Cities"). Case sensitive on purpose: a lowercase "us" is
# almost always the pronoun.
_US_LABEL_RE = re.compile(r"\b(?:USA?|U\.S\.A?\.?)\b")


def is_remote(locations: list[str]) -> bool:
    """True when any location label signals remote work. Hybrid labels
    ("Remote - US") count: the posting is workable remotely."""
    return any(_REMOTE_RE.search(label) for label in locations)


def countries_of(locations: list[str]) -> list[str]:
    """Canonical countries for a posting: real countries only. Remote is
    a separate axis (`is_remote`), and a label that matches nothing adds
    nothing — an empty result means the region is unknown."""
    found: set[str] = set()
    for label in locations:
        before = len(found)
        for raw in _SPLIT_RE.split(label):
            # "US Remote" must still read as the US: drop the remote
            # markers and leading connectors ("Remote in USA" -> "USA"),
            # then match whatever place name remains.
            raw = _REMOTE_RE.sub(" ", raw).strip()
            raw = _CONNECTOR_RE.sub("", raw).strip()
            if not raw:
                continue
            tok = raw.lower()
            if tok in _ALIAS_TO_COUNTRY:
                found.add(_ALIAS_TO_COUNTRY[tok])
            elif tok in _US_STATE_CODES or tok in _US_STATE_NAMES:
                found.add("United States")
            elif raw in _CA_PROVINCE_CODES:
                found.add("Canada")
            elif tok in _CITY_HINTS:
                found.add(_CITY_HINTS[tok])
        if len(found) == before and _US_LABEL_RE.search(label):
            found.add("United States")
    return sorted(found)
