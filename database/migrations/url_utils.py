def escape_configparser_percent(value: str) -> str:
    return value.replace("%", "%%")
