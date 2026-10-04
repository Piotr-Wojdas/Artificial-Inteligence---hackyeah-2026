"""Solari - panel layout on a real roof found by address, with a production estimate for Poland.

    from solary import analyze
    result = analyze(address="Mariacka 1, Katowice", kwp=6)

See README.md for the data sources, assumptions and limits.
"""

from .config import CONFIG, Config
from .service import analyze

__all__ = ["CONFIG", "Config", "analyze"]
__version__ = "0.1.0"
