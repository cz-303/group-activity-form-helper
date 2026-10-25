"""Optional visible-image capture; never clicks, scrolls, or reads client caches."""

import ctypes
import time
from ctypes import wintypes
from PIL import ImageGrab
from chat_reader import rect
from attachment_store import image_bytes, has_qr


def inside(box, outer):
    l, t, r, b = box
    ol, ot, orr, ob = outer
    return ol <= l < r <= orr and ot <= t < b <= ob


def visible_image_nodes(messages):
    """Only images nested in numbered message rows under the known container."""
    stack = [(messages, False, '')]
    count = 0
    deadline = time.monotonic() + 2
    while stack:
        node, in_root, message_id = stack.pop()
        count += 1
        if count > 2200 or time.monotonic() > deadline:
            return
        if node.ControlTypeName == 'EditControl':
            continue
        in_root = in_root or node.AutomationId == 'ml-root'
        if in_root and (node.AutomationId or '').isdigit():
            message_id = node.AutomationId
        if in_root and message_id and node.ControlTypeName == 'ImageControl':
            box = rect(node)
            if (
                not getattr(node, 'IsOffscreen', True)
                and inside(box, rect(messages))
                and box[2] - box[0] >= 100
                and box[3] - box[1] >= 100
                and (box[2] - box[0]) * (box[3] - box[1]) <= 4_000_000
            ):
                yield node, message_id, box
        stack.extend((child, in_root, message_id) for child in node.GetChildren())


def unobscured(hwnd, box):
    """Reject background/minimized/occluded targets, including overlapping windows."""
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.GetForegroundWindow.restype = wintypes.HWND
    api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    api.GetAncestor.restype = wintypes.HWND
    api.GetTopWindow.argtypes = [wintypes.HWND]
    api.GetTopWindow.restype = wintypes.HWND
    api.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    api.GetWindow.restype = wintypes.HWND
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.IsIconic.argtypes = [wintypes.HWND]
    api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    if not hwnd or api.IsIconic(hwnd) or api.GetAncestor(api.GetForegroundWindow(), 2) != hwnd:
        return False
    current = api.GetTopWindow(None)
    l, t, r, b = box
    for _ in range(512):
        if current == hwnd:
            return True
        if not current:
            return False
        if api.IsWindowVisible(current) and not api.IsIconic(current):
            area = wintypes.RECT()
            if not api.GetWindowRect(current, ctypes.byref(area)):
                return False
            if max(l, area.left) < min(r, area.right) and max(t, area.top) < min(b, area.bottom):
                return False
        current = api.GetWindow(current, 2)
    return False


def capture_visible_qr(window, messages, visible=unobscured, grab=None, offset=0):
    """Return transient PNGs; caller persists only after rechecking the group."""
    grab = grab or (lambda box: ImageGrab.grab(bbox=box, all_screens=True))
    result = []
    examined = 0
    deadline = time.monotonic() + 3
    try:
        if not visible(window.NativeWindowHandle, rect(messages)):
            return [], '图片捕获等待聊天窗口位于前台且聊天区域无遮挡'
        nodes = list(visible_image_nodes(messages))
        if nodes:
            offset %= len(nodes)
            nodes = nodes[offset:] + nodes[:offset]
        for node, message_id, box in nodes:
            if examined >= 6 or time.monotonic() > deadline:
                break
            examined += 1
            if not visible(window.NativeWindowHandle, box):
                return [], '图片区域被遮挡，本轮图片已丢弃'
            raw = image_bytes(grab(box))
            if rect(node) != box or not visible(window.NativeWindowHandle, box):
                return [], '图片位置变化，本轮图片已丢弃'
            if has_qr(raw):
                result.append((raw, message_id))
        return result, (
            '本轮识别到 %d 张二维码图片' % len(result) if examined else '未读取到可见图片控件；可从“待关联图片”手动导入'
        )
    except Exception:
        # Text capture may continue. No raw image, payload or OS path goes into logs.
        return [], '图片捕获失败；可手动导入图片，文字监测继续'
