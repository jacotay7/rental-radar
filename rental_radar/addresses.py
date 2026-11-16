"""US street-address patterns, parameterized by state."""
import functools
import re

STREET_TYPES = (r"St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Way|Ct|Court|Ln|Lane|Pl|Place|"
                r"Ter|Terrace|Cir|Circle|Pkwy|Parkway|Hwy|Loop|Sq|Walk|Path|Row|Alley|Trl|Trail|Pike")


@functools.cache
def address_re(state: str) -> re.Pattern:
    """'123 Main St Apt 4, Springfield, IL 62701' -> groups (street, city, zip). Zip is optional."""
    return re.compile(
        rf"(\d{{1,6}}[ \t]+[A-Za-z0-9 .'#-]{{2,40}}?\b(?:{STREET_TYPES})\.?"
        rf"(?:\s*(?:#|Apt\.?|Unit|Ste\.?)\s*[A-Za-z0-9-]+)?),?\s+([A-Za-z .]{{3,30}}),\s*{re.escape(state)}\s*(\d{{5}})?",
        re.I)


@functools.cache
def card_address_re(state: str) -> re.Pattern:
    """A whole line that is an address: '369 S 20th St, Springfield, IL' / '958 43rd St, Springfield'."""
    return re.compile(rf"^(\d{{1,6}}[A-Za-z]?\s+[^,\n]{{2,50}}?),\s*([A-Za-z .'-]{{3,30}}?)"
                      rf"(?:,\s*{re.escape(state)})?(?:\s+(\d{{5}}))?$")
