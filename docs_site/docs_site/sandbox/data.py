"""The sample rows every tab starts from. Invented names; amounts in the fund's currency."""

_FUNDS = [
    (1, "Northbridge Growth I", 2019, "closed", 250_000_000, "EUR"),
    (2, "Northbridge Growth II", 2022, "investing", 400_000_000, "EUR"),
    (3, "Harbour Infrastructure", 2020, "investing", 600_000_000, "USD"),
    (4, "Alder Credit Opportunities", 2021, "investing", 150_000_000, "EUR"),
    (5, "Cedar Venture III", 2023, "fundraising", 120_000_000, "USD"),
    (6, "Birch Secondaries", 2018, "harvesting", 300_000_000, "EUR"),
]
_INVESTORS = [
    (1, "Rhine Pension Fund", "DE", "pension"),
    (2, "Alpine Insurance", "CH", "insurer"),
    (3, "Lakeside Family Office", "AT", "family office"),
    (4, "Meridian Endowment", "US", "endowment"),
    (5, "Fjord Sovereign Fund", "NO", "sovereign"),
]
_COMMITMENTS = [
    (1, 1, 1, 40_000_000, "2019-03-14"),
    (2, 1, 2, 25_000_000, "2019-04-02"),
    (3, 2, 1, 60_000_000, "2022-01-20"),
    (4, 2, 3, 10_000_000, "2022-02-11"),
    (5, 3, 4, 75_000_000, "2020-06-30"),
    (6, 3, 5, 120_000_000, "2020-07-15"),
    (7, 4, 2, 20_000_000, "2021-09-01"),
    (8, 5, 3, 5_000_000, "2023-05-05"),
    (9, 6, 5, 50_000_000, "2018-11-23"),
    (10, 6, 4, 30_000_000, "2018-12-07"),
]


def seed() -> dict[str, list[dict]]:
    """A fresh copy of the sample rows, keyed by model name."""
    return {
        "Fund": [dict(zip(("id", "name", "vintage", "status", "size", "currency"), row)) for row in _FUNDS],
        "Investor": [dict(zip(("id", "name", "country", "kind"), row)) for row in _INVESTORS],
        "Commitment": [dict(zip(("id", "fund", "investor", "amount", "signed_on"), row)) for row in _COMMITMENTS],
    }
