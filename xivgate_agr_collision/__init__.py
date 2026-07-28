# SPDX-FileCopyrightText: 2026 XIVgate
# SPDX-License-Identifier: GPL-3.0-or-later
# AGR Collision
# Author: XIVgate
#
# Generates closed, convex UCX collision sets for Moscow AGR models and Unreal
# Engine. Selected source objects are combined through a hidden repair proxy;
# the visual model itself is never modified.

import bpy

from . import props
from . import operators
from . import panel
from . import translations

_MODULES = (props, operators, panel)


def register():
    for mod in _MODULES:
        mod.register()
    translations.unregister(__name__)
    translations.register(__name__)


def unregister():
    translations.unregister(__name__)
    for mod in reversed(_MODULES):
        mod.unregister()
