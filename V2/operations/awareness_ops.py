from pycrdt import Decoder


def awareness_client_ids(update: bytes) -> list[int]:
    """Client ids carried by a y-protocols awareness update, read without applying it."""
    decoder = Decoder(update)
    ids = []
    for _ in range(decoder.read_var_uint()):
        ids.append(decoder.read_var_uint())
        decoder.read_var_uint()
        decoder.read_var_string()
    return ids


def offset_to_line_col(text: str, offset: int) -> tuple[int, int]:
    before = text[:offset]
    return before.count("\n"), offset - (before.rfind("\n") + 1)
