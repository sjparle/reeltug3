import re


SPLIT_PATTERN = re.compile(r"SP\d+")
VERSION_PATTERN = re.compile(r"V\d+")


def replace_split_token(path_value: str, split_no: int) -> str:
    return SPLIT_PATTERN.sub(f"SP{split_no}", path_value)


def strip_split_token(name: str) -> str:
    return re.sub(r"-SP\d+", "", name)


def replace_split_suffix(name: str, replacement: str) -> str:
    return re.sub(r"-SP\d+", replacement, name)
