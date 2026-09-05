"""Small native AppKit palette and controls shared by the two windows."""
from __future__ import annotations

from typing import Any

import objc
from AppKit import (
    NSBezelStyleRounded,
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSFontDescriptorSystemDesignRounded,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSImage,
    NSImageLeft,
    NSImageView,
    NSMakeRect,
    NSTextField,
    NSView,
)

from src.utils.storage import bundled_path

BACKGROUND = "F4F7F6"
SURFACE = "FFFFFF"
INK = "203D35"
MUTED = "63786F"
ACCENT = "23775B"
MINT = "E4F2EB"
BORDER = "DDE7E1"
WARNING = "946224"
ERROR = "B24E4E"


class Surface(NSView):
    def initWithFrame_(self, frame: Any):
        self = objc.super(Surface, self).initWithFrame_(frame)
        if self is not None:
            self.fill_color = NSColor.whiteColor()
            self.radius = 18
        return self

    def drawRect_(self, _rect: Any) -> None:
        self.fill_color.setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            self.bounds(), self.radius, self.radius
        ).fill()


def color(value: str) -> Any:
    channels = [int(value[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    return NSColor.colorWithSRGBRed_green_blue_alpha_(*channels, 1.0)


def font(size: float, bold: bool = False, rounded: bool = False) -> Any:
    result = NSFont.systemFontOfSize_weight_(
        size, NSFontWeightSemibold if bold else NSFontWeightRegular
    )
    if rounded:
        descriptor = result.fontDescriptor().fontDescriptorWithDesign_(NSFontDescriptorSystemDesignRounded)
        if descriptor is not None:
            result = NSFont.fontWithDescriptor_size_(descriptor, size)
    return result


def label(text: str, frame: Any, size: float = 13, bold: bool = False,
          ink: str = INK, rounded: bool = False) -> Any:
    view = NSTextField.labelWithString_(text)
    view.setFrame_(frame)
    view.setFont_(font(size, bold, rounded))
    view.setTextColor_(color(ink))
    view.setSelectable_(False)
    view.setMaximumNumberOfLines_(0)
    view.cell().setWraps_(True)
    return view


def panel(frame: Any, background: str = SURFACE, radius: int = 18) -> Any:
    view = Surface.alloc().initWithFrame_(frame)
    view.fill_color = color(background)
    view.radius = radius
    return view


def button(title: str, frame: Any, target: Any, action: str,
           primary: bool = False, symbol: str = "") -> Any:
    view = NSButton.alloc().initWithFrame_(frame)
    view.setTitle_(title)
    view.setBezelStyle_(NSBezelStyleRounded)
    view.setFont_(font(13, primary))
    view.setTarget_(target)
    view.setAction_(action)
    if primary:
        view.setBezelColor_(color(ACCENT))
        view.setContentTintColor_(NSColor.whiteColor())
    if symbol:
        icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title)
        if icon is not None:
            view.setImage_(icon)
            view.setImagePosition_(NSImageLeft)
    return view


def mascot(frame: Any) -> Any:
    view = NSImageView.alloc().initWithFrame_(frame)
    view.setImage_(NSImage.alloc().initWithContentsOfFile_(str(bundled_path("assets/mascot.png"))))
    view.setAccessibilityLabel_("手持星星票券的薄荷发色动漫少女")
    return view


def divider(parent: Any, x: float, y: float, width: float, height: float = 1) -> None:
    parent.addSubview_(panel(NSMakeRect(x, y, width, height), BORDER, 0))
