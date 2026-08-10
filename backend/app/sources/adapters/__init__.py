"""Per-site adapters. Each exposes list_url, list_ids and parse_detail, all PURE.

Fetching belongs to poller.py so that every parser is testable against saved
HTML with no network and no politeness concerns.
"""
from . import rinka

ADAPTERS = {rinka.KEY: rinka}


def get(key: str):
    return ADAPTERS.get(key)
