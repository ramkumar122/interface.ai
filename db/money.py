"""Money handling for CoreDesk.

All money is stored as INTEGER CENTS. Never float, never REAL. These two
helpers are the only sanctioned way to move between stored cents and the
display strings templates render. Everything here uses integer arithmetic,
so no rounding artifact can ever creep in.
"""


def cents_to_display(cents):
    """Format integer cents as a comma-grouped string with no currency symbol.

    Examples:
        cents_to_display(0)        -> "0.00"
        cents_to_display(1284550)  -> "12,845.50"
        cents_to_display(7420999)  -> "74,209.99"
        cents_to_display(-5420)    -> "-54.20"
    """
    # Reject anything that is not a plain int. bool is a subclass of int, so
    # it is excluded explicitly. This is the guard that keeps floats out.
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise TypeError(
            "cents_to_display expects int cents, got %s" % type(cents).__name__
        )

    negative = cents < 0
    magnitude = -cents if negative else cents

    dollars, remainder = divmod(magnitude, 100)  # integer division only
    body = "{:,}.{:02d}".format(dollars, remainder)
    return "-" + body if negative else body


def display_to_cents(display):
    """Parse a display string (optionally comma-grouped) back into int cents.

    Inverse of cents_to_display for any value it can produce:
        display_to_cents("12,845.50") -> 1284550
        display_to_cents("0.00")      -> 0
        display_to_cents("-54.20")    -> -5420
    """
    if not isinstance(display, str):
        raise TypeError(
            "display_to_cents expects a str, got %s" % type(display).__name__
        )

    s = display.strip()
    if not s:
        raise ValueError("display_to_cents received an empty string")

    negative = s.startswith("-")
    if negative:
        s = s[1:].strip()

    s = s.replace(",", "")

    if "." in s:
        whole, frac = s.split(".", 1)
    else:
        whole, frac = s, ""

    whole = whole or "0"
    # Pad/trim the fractional part to exactly two digits (cents).
    frac = (frac + "00")[:2]

    if not whole.isdigit() or not frac.isdigit():
        raise ValueError("display_to_cents could not parse %r" % display)

    total = int(whole) * 100 + int(frac)
    return -total if negative else total
