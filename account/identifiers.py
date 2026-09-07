import secrets


CVU_LENGTH = 22
ALIAS_TOKEN_BYTES = 8


def generate_account_identifiers():
    cvu = f"{secrets.randbelow(10 ** CVU_LENGTH):0{CVU_LENGTH}d}"
    token = secrets.token_hex(ALIAS_TOKEN_BYTES)
    alias = f"brokerdex.{token[:8]}.{token[8:]}"
    return cvu, alias
