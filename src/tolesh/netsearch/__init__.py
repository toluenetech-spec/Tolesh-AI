"""Web access: page reading and search.

Both return *untrusted* text and provide a wrapper that marks it as data, so the
chat layer never has to remember to do that itself.
"""

from .fetch import FetchError, fetch_page, normalise, page_block
from .search import SearchUnavailable, configured_backends, results_block, search

__all__ = ["FetchError", "SearchUnavailable", "configured_backends", "fetch_page",
           "normalise", "page_block", "results_block", "search"]
