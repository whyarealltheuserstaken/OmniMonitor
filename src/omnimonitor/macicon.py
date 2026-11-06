"""Crisp template images in the macOS menu bar.

pystray 0.19's darwin backend resizes whatever PIL image it gets to the status-bar thickness
in *pixels* (22 px), so on Retina screens a 44 px icon is downsampled to 22 px and then
upscaled by AppKit: blurry. It also never marks the image as a template, so a colored image
on a dark disc ignores light/dark menu bars. We replace that one method on the icon
instance: the @2x PNG becomes an NSImage whose logical size is 22 pt tall (AppKit uses all
44 px on Retina) Colored green/amber/red gauges use ``setTemplate_(False)``; error / no-data stay templates.
"""

from __future__ import annotations

import io
import logging
import types

log = logging.getLogger(__name__)


def nsimage_from(img, template: bool, scale: int = 2):
    import AppKit  # noqa: PLC0415
    import Foundation  # noqa: PLC0415
    b = io.BytesIO()
    img.save(b, "png")
    ns = AppKit.NSImage.alloc().initWithData_(Foundation.NSData(b.getvalue()))
    ns.setSize_((img.width / float(scale), img.height / float(scale)))
    ns.setTemplate_(bool(template))
    return ns


def install(icon) -> bool:
    """Patch a pystray darwin Icon in place. Returns False (no-op) on other backends."""
    if "_darwin" not in type(icon).__module__ or not hasattr(icon, "_status_item"):
        return False
    original = icon._assert_image

    def _assert_image(self):
        spec = getattr(self, "_omni_spec", None)
        if spec is None:
            return original()
        if self._icon_image is not None and getattr(self, "_omni_applied", None) is spec:
            return None
        img, template = spec
        try:
            self._icon_image = nsimage_from(img, template)
        except Exception:  # noqa: BLE001
            log.exception("menu bar image")
            return original()
        self._status_item.button().setImage_(self._icon_image)
        self._omni_applied = spec
        return None
    icon._assert_image = types.MethodType(_assert_image, icon)
    return True
