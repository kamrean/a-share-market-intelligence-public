"""Pinned PR14 and PR15 inputs for historical assertions, never executable archives."""

from functools import lru_cache
from types import MappingProxyType
from unittest.mock import patch


MERGED_P4T = "652d5c6effcf22dc1f38b2d2a3c5ea86c20291ec"
MERGED_P4MVP = "37a73f7ff3c9d3f7fd4600cc460430c035c8213a"


@lru_cache(maxsize=2)
def merged_contents(root, g, commit=MERGED_P4T):
    entries = {}
    for record in g.git_paths(g.git(root, "ls-tree", "-r", "-z", commit)):
        metadata, separator, name = record.partition("\t")
        fields = metadata.split()
        g.require(separator and len(fields) == 3 and fields[1] == "blob"
                  and name not in entries, "invalid historical merged tree entry")
        entries[name] = fields[2]
    blobs = g.read_blobs(root, entries.values())
    return MappingProxyType({name: blobs[oid] for name, oid in entries.items()})


def merged_reads(governance, contents):
    return patch.object(governance, "read_text", side_effect=lambda _root, name: contents[name])
