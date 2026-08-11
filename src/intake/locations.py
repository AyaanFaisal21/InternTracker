"""Country extraction from free-text location labels.

ATS location strings have no schema: "US, CA, Santa Clara",
"New York, NY (HQ)", "Bengaluru", "Taiwan, Taipei", "Remote". Strategy:
tokenize the label, then match tokens against (1) a canonical country
list with aliases, (2) US state names/codes (US labels rarely name the
country), (3) a small tech-hub city map for bare-city labels.

"Georgia" is treated as the US state, not the country — in tech
recruiting data that reading is almost always correct.
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


def countries_of(locations: list[str]) -> list[str]:
    """Canonical countries for a posting. "Remote" is its own bucket.
    Labels that match nothing yield "Unknown" so they stay filterable."""
    found: set[str] = set()
    for label in locations:
        matched = False
        tokens = [t.strip().lower() for t in _SPLIT_RE.split(label) if t.strip()]
        for tok in tokens:
            if tok in _ALIAS_TO_COUNTRY:
                found.add(_ALIAS_TO_COUNTRY[tok])
                matched = True
            elif tok in _US_STATE_CODES or tok in _US_STATE_NAMES:
                found.add("United States")
                matched = True
            elif tok in _CITY_HINTS:
                found.add(_CITY_HINTS[tok])
                matched = True
            elif "remote" in tok:
                found.add("Remote")
                matched = True
        if not matched and label.strip():
            found.add("Unknown")
    return sorted(found)
