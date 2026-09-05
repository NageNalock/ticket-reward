#!/usr/bin/env python3
"""Write a reproducible Finder layout without reading local Finder preferences."""
from __future__ import annotations

import sys
from pathlib import Path

from ds_store import DSStore


def write_layout(folder: Path) -> None:
    with DSStore.open(str(folder / ".DS_Store"), "w+") as store:
        store["."]["vSrn"] = ("long", 1)
        store["."]["vstl"] = ("type", b"icnv")
        store["."]["bwsp"] = {
            "WindowBounds": "{{200, 180}, {620, 360}}",
            "ShowToolbar": False,
            "ShowSidebar": False,
            "ShowStatusBar": False,
            "ShowPathbar": False,
            "ShowTabView": False,
            "ContainerShowSidebar": False,
        }
        store["."]["icvp"] = {
            "viewOptionsVersion": 1,
            "backgroundType": 1,
            "backgroundColorRed": 0.957,
            "backgroundColorGreen": 0.976,
            "backgroundColorBlue": 0.969,
            "iconSize": 128.0,
            "textSize": 14.0,
            "gridSpacing": 100.0,
            "gridOffsetX": 0.0,
            "gridOffsetY": 0.0,
            "arrangeBy": "none",
            "labelOnBottom": True,
            "showItemInfo": False,
            "showIconPreview": False,
        }
        store["Bing Rewards.app"]["Iloc"] = (155, 170)
        store["Applications"]["Iloc"] = (465, 170)


if __name__ == "__main__":
    write_layout(Path(sys.argv[1]))
