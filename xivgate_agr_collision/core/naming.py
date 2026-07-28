# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# UCX naming helpers.
#
# AGR naming scheme for collision objects (spec item 10/4.2):
#   UCX_<SourceGeometryName>_<NNN>
# where NNN is a zero-padded ordinal 001..999 without gaps.
# The same scheme is what Unreal Engine expects for FBX collision import.

import re

# Blender object names are limited to 63 bytes; warn before we hit it.
BLENDER_NAME_LIMIT = 63

# Matches our colliders, including possible ".001" duplicates Blender may add.
_UCX_RE = re.compile(r"^UCX_(?P<base>.+)_(?P<num>\d{3})(?:\.\d+)?$")

SOURCE_PROP = "agr_ucx_source"


def collider_name(base, index):
    # index is 1-based per the spec (001..999).
    return "UCX_{:s}_{:03d}".format(base, index)


def parse(name):
    # Returns (base, number) or None when the name is not a UCX collider name.
    m = _UCX_RE.match(name)
    if m is None:
        return None
    return m.group("base"), int(m.group("num"))


def is_collider_of(ob, base):
    # Custom property is the robust link; the name pattern is the fallback
    # so that manually renamed sources still get their old colliders replaced.
    if ob.get(SOURCE_PROP) == base:
        return True
    parsed = parse(ob.name)
    return parsed is not None and parsed[0] == base


def colliders_of(objects, base):
    return [ob for ob in objects if ob.type == "MESH" and is_collider_of(ob, base)]


def is_any_collider(ob):
    return ob.type == "MESH" and (SOURCE_PROP in ob.keys() or parse(ob.name) is not None)
