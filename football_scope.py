"""Name/country allowlist for senior men's competitions, applied before fetching matches."""

import re
import unicodedata


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", (value or "").casefold()).replace("ł", "l")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


COUNTRIES = {
    "EN": ("anglia", "england", "english", "eng"),
    "ES": ("hiszpania", "spain", "spanish"),
    "IT": ("wlochy", "italy", "italian"),
    "DE": ("niemcy", "germany", "german"),
    "FR": ("francja", "france", "french"),
    "PL": ("polska", "poland", "polish"),
    "PT": ("portugalia", "portugal", "portuguese"),
    "NL": ("holandia", "netherlands", "dutch"),
    "BE": ("belgia", "belgium", "belgian"),
    "TR": ("turcja", "turkey", "turkiye", "turkish"),
    "SC": ("szkocja", "scotland", "scottish"),
    "AT": ("austria", "austrian"),
    "CH": ("szwajcaria", "switzerland", "swiss"),
    "CZ": ("czechy", "czechia", "czech republic", "czech"),
    "DK": ("dania", "denmark", "danish"),
    "NO": ("norwegia", "norway", "norwegian"),
    "SE": ("szwecja", "sweden", "swedish"),
    "GR": ("grecja", "greece", "greek"),
}
CODE_ALIASES = {"ENG": "EN", "GB-ENG": "EN", "SCO": "SC", "GB-SCT": "SC", "ND": "SC"}

# Exact normalized names after removing an optional country prefix.
# Numeric division names are country-specific: Polish 2. Liga is tier 3,
# Norwegian 1. divisjon is tier 2, but English League 2 is outside the scope.
LEAGUES = {
    "EN": {"premier league", "epl", "championship", "efl championship", "league one", "league 1", "efl league one"},
    "ES": {"la liga", "laliga", "laliga ea sports", "primera division", "liga primera", "segunda", "segunda division", "laliga hypermotion", "2 liga"},
    "IT": {"serie a", "serie a enilive", "serie b", "serie bkt"},
    "DE": {"bundesliga", "1 bundesliga", "2 bundesliga", "bundesliga 2", "3 liga"},
    "FR": {"ligue 1", "ligue 1 mcdonald s", "ligue 2", "ligue 2 bkt"},
    "PL": {"ekstraklasa", "pko ekstraklasa", "pko bp ekstraklasa", "i liga", "ii liga", "1 liga", "2 liga", "betclic 1 liga", "betclic 2 liga", "fortuna 1 liga"},
    "PT": {"liga betclic", "primeira liga", "liga portugal", "liga portugal betclic", "segunda liga", "liga portugal 2", "liga portugal 2 meu super", "liga pro", "2 liga"},
    "NL": {"eredivisie", "eerste divisie", "keuken kampioen divisie", "2 liga"},
    "BE": {"pro league", "jupiler pro league", "first division a", "challenger pro league", "first division b", "1 liga", "2 liga"},
    "TR": {"super lig", "super league", "1 lig", "lig 1", "tff 1 lig"},
    "SC": {"premiership", "premier league", "championship"},
    "AT": {"bundesliga", "2 liga", "erste liga"},
    "CH": {"super league", "superliga", "challenge league", "2 liga"},
    "CZ": {"first league", "chance liga", "1 liga", "2 liga", "f nl", "fnl", "narodni liga", "chance narodni liga"},
    "DK": {"superliga", "superligaen", "1st division", "1 division", "dywizja 1", "nordicbet liga", "betinia liga"},
    "NO": {"eliteserien", "1 divisjon", "obos ligaen", "obos liga"},
    "SE": {"allsvenskan", "superettan"},
    "GR": {"super league", "super league 1", "super league 2", "superliga", "1 liga", "2 liga"},
}
CUPS = {
    "EN": {"fa cup", "efl cup", "league cup", "carabao cup", "puchar ligi", "community shield"},
    "ES": {"copa del rey", "puchar", "supercopa", "supercopa de espana", "superpuchar"},
    "IT": {"coppa italia", "puchar", "supercoppa italiana", "superpuchar"},
    "DE": {"dfb pokal", "puchar", "supercup", "superpuchar"},
    "FR": {"coupe de france", "puchar", "trophee des champions", "superpuchar"},
    "PL": {"puchar polski", "puchar", "superpuchar polski", "superpuchar"},
    "PT": {"taca de portugal", "taca da liga", "puchar", "puchar ligi", "supertaca", "superpuchar"},
    "NL": {"knvb beker", "knvb cup", "puchar", "johan cruyff shield", "superpuchar"},
    "BE": {"belgian cup", "croky cup", "puchar", "superpuchar"},
    "TR": {"turkish cup", "turkiye kupasi", "puchar", "superpuchar"},
    "SC": {"fa cup", "scottish cup", "league cup", "puchar", "puchar ligi"},
    "AT": {"ofb cup", "puchar", "superpuchar"},
    "CH": {"swiss cup", "schweizer cup", "puchar", "superpuchar"},
    "CZ": {"mol cup", "pohar facr", "puchar", "superpuchar"},
    "DK": {"dbu pokalen", "danish cup", "puchar", "superpuchar"},
    "NO": {"nm cup", "nm cupen", "norwegian cup", "puchar", "superpuchar"},
    "SE": {"svenska cupen", "puchar", "superpuchar"},
    "GR": {"greek cup", "puchar", "superpuchar"},
}
EUROPEAN = {
    "champions league", "liga mistrzow", "europa league", "liga europy",
    "conference league", "europa conference league", "liga konferencji",
    "super cup", "superpuchar europy",
}
INTERNATIONAL = {
    "world cup", "fifa world cup", "ms", "mistrzostwa swiata", "euro",
    "european championship", "mistrzostwa europy", "nations league", "liga narodow",
    "copa america", "africa cup of nations", "puchar narodow afryki", "afcon",
    "concacaf gold cup", "gold cup", "zloty puchar concacaf",
    "afc asian cup", "asian cup", "puchar azji", "fifa confederations cup", "puchar konfederacji fifa",
    "concacaf nations league",
}
EXCLUDED = re.compile(
    r"\b(?:women\w*|ladies|female|girls|kobiet\w*|kobie\w*|feminin\w*|femenin\w*|femminil\w*|frauen|damen|k|w|nwsl|wsl|"
    r"u\s*\d{1,2}|under\s*\d{1,2}|youth|junior\w*|mlodzie\w*|academy|akadem\w*|primavera|development|"
    r"reserve\w*|rezerw\w*|b\s+teams?|second\s+teams?|amateur\w*|amator\w*|regional\w*|"
    r"exhibition\w*|pokazow\w*|friendly|friendlies|towarzyski\w*|futsal|beach|esoccer|virtual)\b"
)


def _without_suffixes(name):
    name = re.sub(r"\b20\d{2}(?:\s+\d{2,4})?\b", "", name)
    name = re.sub(r"\b(?:kwalifikacje|eliminacje|qualifications?|qualifiers?|qualifying|baraze|play offs?|playoffs)\b", "", name)
    return " ".join(name.split())


def is_allowed_football_competition(competition) -> bool:
    """Positive competition selection; never inspect the clubs' league levels."""
    name = normalize(competition.name)
    category = normalize(competition.category)
    if EXCLUDED.search(f"{name} {category} {normalize(competition.country_name)}") or category in {"zwyciezca", "winner", "outright", "b teams", "second teams"}:
        return False
    name = _without_suffixes(name)
    # The unqualified Polish label in SportMenu denotes CAF's club cup.
    # Only an explicit FIFA label identifies the historical national-team cup.
    if "confederation" in name or "konfederacji" in name:
        if name not in {"fifa confederations cup", "puchar konfederacji fifa", "fifa puchar konfederacji"}:
            return False
        if re.search(r"\b(?:caf|afryka|africa|afrykanski)\b", f"{category} {normalize(competition.country_name)}"):
            return False
        name = "fifa confederations cup"
    code = (competition.country_code or "").upper()
    code = CODE_ALIASES.get(code, code)
    country_name = normalize(competition.country_name)
    if not code:
        code = next((key for key, aliases in COUNTRIES.items() if country_name in aliases), "")

    # Official international tournaments are not domestic Asian/Oceanian leagues.
    international_name = re.sub(r"^uefa\s+", "", name)
    international_name = re.sub(r"\s+(?:uefa|europa|europe|afc|caf|concacaf|conmebol|ofc|afryka|africa|azja|asia|oceania|south america|north america|ameryka poludniowa|ameryka polnocna)$", "", international_name)
    if code in {"", "EU", "ZZ", "INT", "WORLD", "UEFA"}:
        if international_name in EUROPEAN or international_name in INTERNATIONAL:
            return True

    # Country metadata is authoritative; absent metadata can only use an
    # unambiguous exact tournament name, never a broad 'Premier League' substring.
    if code and code not in COUNTRIES:
        return False
    candidates = [code] if code else list(COUNTRIES)
    allowed = []
    for country in candidates:
        local_name = name
        for prefix in COUNTRIES[country]:
            if local_name.startswith(prefix + " "):
                local_name = local_name[len(prefix) + 1:]
                break
        if name in LEAGUES[country] | CUPS[country] or local_name in LEAGUES[country] | CUPS[country]:
            allowed.append(country)
    return len(allowed) == 1


def _domestic_kind(competition, names) -> bool:
    name = _without_suffixes(normalize(competition.name))
    return any(
        name in aliases or any(name == prefix + " " + alias for prefix in COUNTRIES[country] for alias in aliases)
        for country, aliases in names.items()
    )


def is_domestic_cup(competition) -> bool:
    """Called only for competitions which passed the curated allowlist."""
    return _domestic_kind(competition, CUPS)


def is_domestic_league(competition) -> bool:
    return _domestic_kind(competition, LEAGUES)


def football_team_names(match) -> list[str]:
    teams = [team.name for team in match.teams if team.name]
    if len(teams) == 2:
        return teams
    # Require a spaced separator; never split Paris-Saint-Germain, etc.
    parts = re.split(r"\s+(?:[-–—]|vs\.?|v\.)\s+", match.name or "", flags=re.IGNORECASE)
    return parts if len(parts) == 2 else teams


def is_reserve_team(name: str) -> bool:
    name = club_key(name)
    return bool(
        re.search(r"\b(?:reserves?|rezerwy|primavera|u\s*(?:21|23))\b", name)
        or re.match(r"jong\s+", name)
        # B and II must be suffixes, not substrings (B 1903, B.93, Willem II).
        or (re.search(r"\S+\s+(?:b|ii)$", name) and name != "willem ii")
    )


# Explicit conservative fallback requested for the early English cup fixtures.
# This is not a general inference of league level from a club's name. Unknown
# clubs stay eligible; evidence from fetched allowed leagues takes precedence.
EARLY_CUP_CLUBS = {
    "ossett united", "pontefract collieries", "quorn", "shepshed dynamo",
}


def club_key(name: str) -> str:
    return re.sub(r"^(?:fc|afc)\s+|\s+(?:fc|afc)$", "", normalize(name))


def is_allowed_football_match(match, competition, league_teams=frozenset()) -> bool:
    teams = football_team_names(match)
    if any(is_reserve_team(team) for team in teams):
        return False
    if not is_domestic_cup(competition) or len(teams) != 2:
        return True
    keys = {club_key(team) for team in teams}
    if keys & league_teams:
        return True
    # Only explicit late-round category labels establish a main cup phase.
    # Do not infer it from an ambiguous 'round 1' or a calendar date.
    if normalize(competition.category) in {
        "final", "finals", "final pucharu",
        "semi final", "semi finals", "semifinal", "semifinals", "polfinal", "polfinaly",
        "quarter final", "quarter finals", "quarterfinal", "quarterfinals", "cwiercfinal", "cwiercfinaly",
        "round of 16", "1 8 finalu", "1 4 finalu", "1 2 finalu",
    }:
        return True
    # No reliable round/club-tier metadata is exposed by the list API. Keep
    # uncertain pairs, especially a large club against a lower-league opponent.
    return not keys.issubset(EARLY_CUP_CLUBS)
