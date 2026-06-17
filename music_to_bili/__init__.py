# -*- coding: utf-8 -*-
from . import bili_search
from . import web_search
from . import downloader
from . import fingerprint_matcher

from .bili_search import search, search_candidates
from .web_search import search_bvids, search_song
from .downloader import new_session, download_bv_audio, DownloadError
from .fingerprint_matcher import (
    load_audio, build_pack, save_pack, load_pack,
    match_packs, match_pair,
)

__all__ = [
    "bili_search", "web_search", "downloader", "fingerprint_matcher",
    "search", "search_candidates", "search_bvids", "search_song",
    "new_session", "download_bv_audio", "DownloadError",
    "load_audio", "build_pack", "save_pack", "load_pack",
    "match_packs", "match_pair",
]

__version__ = "0.1.0"
