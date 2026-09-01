"""Побудова targeting spec для ad set та пошук інтересів/поведінок для таргетингу."""
from facebook_business.adobjects.targetingsearch import TargetingSearch

GENDER_MAP = {"all": [], "male": [1], "female": [2]}


def build_targeting(
    countries: list[str],
    age_min: int = 18,
    age_max: int = 65,
    gender: str = "all",
    interest_ids: list[str] | None = None,
    platforms: list[str] | None = None,
) -> dict:
    """Складає targeting spec у форматі Marketing API.

    platforms: підмножина ["facebook", "instagram"]; None = обидві.
    """
    if gender not in GENDER_MAP:
        raise ValueError(f"gender має бути одним з {list(GENDER_MAP)}, отримано {gender!r}")

    spec: dict = {
        "geo_locations": {"countries": countries},
        "age_min": age_min,
        "age_max": age_max,
        "publisher_platforms": platforms or ["facebook", "instagram"],
    }
    if GENDER_MAP[gender]:
        spec["genders"] = GENDER_MAP[gender]
    if interest_ids:
        spec["flexible_spec"] = [{"interests": [{"id": i} for i in interest_ids]}]
    return spec


def search_interests(query: str, limit: int = 10) -> list[dict]:
    """Шукає ID інтересів за назвою (напр. «фітнес») для використання в build_targeting()."""
    results = TargetingSearch.search(
        params={"q": query, "type": "adinterest", "limit": limit}
    )
    return [{"id": r["id"], "name": r["name"], "audience_size": r.get("audience_size_lower_bound")} for r in results]
