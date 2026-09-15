"""Administrative-name scoping only; never invent coordinates or rewrite radio text."""

import unicodedata

PREFECTURES = (
    "北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 "
    "茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 "
    "新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 "
    "三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 "
    "鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 "
    "福岡県 佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県"
).split()


def normalize(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split())


def split_area(value: str) -> tuple[str, str]:
    value = normalize(value)
    prefecture = next((p for p in PREFECTURES if value.startswith(p)), "")
    return prefecture, value.removeprefix(prefecture)


def municipality_contains(scope: str, value: str) -> bool:
    if not scope or scope == value:
        return True
    # A city can contain a named ward, but 東区 must never match 台東区 or 江東区.
    if scope.endswith("市") and value.startswith(scope) and value.endswith("区"):
        return True
    if value.endswith(scope):
        return value[: -len(scope)].endswith(("市", "郡"))
    return False


def in_region(region, municipality: str) -> bool:
    if region is None:
        return True
    inherited_prefecture = ""
    for part in municipality.split("・"):
        prefecture, local = split_area(part)
        inherited_prefecture = prefecture or inherited_prefecture
        if inherited_prefecture == region.prefecture and municipality_contains(
            region.municipality, local
        ):
            return True
    return False


def region_conflicts(region, municipality: str | None) -> bool:
    if region is None or not municipality:
        return False
    inherited_prefecture = ""
    for part in municipality.split("・"):
        prefecture, local = split_area(part)
        inherited_prefecture = prefecture or inherited_prefecture
        if inherited_prefecture and inherited_prefecture != region.prefecture:
            continue
        if not local or not region.municipality:
            return False
        if municipality_contains(region.municipality, local) or municipality_contains(
            local, region.municipality
        ):
            return False
    return True


def geocoder_municipality(result: dict) -> str:
    """Use returned administrative fields, not the requested area as proof of a match."""
    address = result.get("address", {})
    if not isinstance(address, dict):
        return ""
    prefecture = address.get("province") or address.get("state") or ""
    if prefecture not in PREFECTURES or address.get("country_code") != "jp":
        return ""
    parts = []
    for key in ["county", "city", "municipality", "town", "village", "city_district", "suburb"]:
        value = address.get(key)
        if isinstance(value, str) and value.endswith(("郡", "市", "区", "町", "村")):
            value = normalize(value)
            if value not in parts and not any(p.endswith(value) for p in parts):
                parts.append(value)
    return prefecture + "".join(parts)
