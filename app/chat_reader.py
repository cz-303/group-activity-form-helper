"""desktop chat client main-window adapter based on observed accessible header and message list."""

from diagnostics import UserFacingError
import re
import time
from notice_identity import ChatMessage, is_timestamp


def message_content(row, children):
    """Recognize only the observed timestamp / sender / content row structure.

    Ambiguous or changed UI layouts keep the complete original text, rather
    than guessing which user-authored lines might be metadata.
    """
    containers = ('GroupControl', 'PaneControl', 'CustomControl')
    anonymous_cache = {}

    def anonymous(node):
        key = id(node)
        if key not in anonymous_cache:
            anonymous_cache[key] = (
                node.ControlTypeName in containers
                and not node.Name
                and all(anonymous(child) for child in children[key])
            )
        return anonymous_cache[key]

    def content(node):
        if node.ControlTypeName == 'EditControl':
            return []
        nested = [text for child in children[id(node)] for text in content(child)]
        return nested or ([node.Name] if node.Name else [])

    parts = list(children[id(row)])
    # Chromium-based client accessibility bridges expose generic groups as Group, Pane
    # or Custom controls, sometimes inside a transparent unnamed wrapper.
    for _ in range(4):
        current = parts
        if len(current) == 2:
            stamp = children[id(current[0])]
            if (
                current[0].ControlTypeName in containers
                and len(stamp) == 1
                and stamp[0].ControlTypeName == 'TextControl'
                and not children[id(stamp[0])]
                and is_timestamp(stamp[0].Name or '')
            ):
                current = current[1:]
        if len(current) == 1 and current[0].ControlTypeName in containers:
            items = [item for item in children[id(current[0])] if not anonymous(item)]
            if len(items) >= 2:
                sender, body = items[0], items[-1]
                name = sender.Name or ''
                if (
                    sender.ControlTypeName in containers
                    and all(anonymous(child) for child in children[id(sender)])
                    and body.ControlTypeName in containers
                    and children[id(body)]
                    and all(
                        item.ControlTypeName == 'TextControl'
                        and not children[id(item)]
                        and (item.Name or '') in (name, '群主', '管理员')
                        for item in items[1:-1]
                    )
                ):
                    return '\n'.join(content(body))
        if len(parts) != 1 or parts[0].Name or parts[0].ControlTypeName not in containers:
            break
        parts = list(children[id(parts[0])])
    return None


def group_header(name, group):
    return bool(re.fullmatch(re.escape(group) + r'(?:\s*[（(]\d+[）)])?', name.strip()))


def rect(control):
    r = control.BoundingRectangle
    return r.left, r.top, r.right, r.bottom


def find_regions(window, group):
    headers, lists = [], []
    stack, count, deadline = [window], 0, time.monotonic() + 6
    while stack:
        n = stack.pop()
        count += 1
        if count > 1800 or time.monotonic() > deadline:
            raise UserFacingError('聊天界面结构读取超时，请稍候重试。')
        name, kind = n.Name or '', n.ControlTypeName
        # Never interpret a sidebar entry, member, message quote or draft as the active header.
        if name in ('会话列表', '群成员列表') or kind == 'EditControl':
            continue
        if name == '消息列表':
            lists.append(n)
            continue
        if kind == 'ButtonControl' and group_header(name, group):
            headers.append(n)
        stack.extend(n.GetChildren())
    wl, wt, wr, wb = rect(window)
    pairs = []
    for h in headers:
        hl, ht, hr, hb = rect(h)
        for m in lists:
            ml, mt, mr, mb = rect(m)
            if (
                mr > ml
                and mb > mt
                and hr > hl
                and hb > ht
                and wt <= ht < hb <= mt
                and ml <= hl < hr <= mr
                and mt - hb < (wb - wt) * 0.2
            ):
                pairs.append((h, m))
    if len(pairs) > 1:
        raise UserFacingError('同一聊天窗口找到多个聊天区域，暂停读取。')
    return pairs[0] if pairs else None


def read_messages(message_list):
    # desktop chat client exposes each message under ml-root with a numeric AutomationId.
    stack, roots, count = [message_list], [], 0
    while stack:
        n = stack.pop()
        count += 1
        if count > 100:
            raise UserFacingError('消息容器结构发生变化，请停止监测并检查。')
        if n.AutomationId == 'ml-root':
            roots.append(n)
            continue
        stack.extend(n.GetChildren())
    if len(roots) != 1:
        raise UserFacingError('已识别目标群，但无法唯一定位消息容器。')
    texts, deadline = [], time.monotonic() + 6
    count = 0
    rows, wrappers = [], [(roots[0], 0)]
    while wrappers:
        container, depth = wrappers.pop()
        for child in container.GetChildren():
            if (child.AutomationId or '').isdigit():
                rows.append(child)
            elif depth < 3 and child.ControlTypeName != 'EditControl':
                wrappers.append((child, depth + 1))
    for row in rows:
        children_by_node = {}

        def collect(n):
            nonlocal count
            count += 1
            if count > 2200 or time.monotonic() > deadline:
                raise UserFacingError('消息读取超时；本轮结果已丢弃。')
            if n.ControlTypeName == 'EditControl':
                children_by_node[id(n)] = []
                return []
            children_by_node[id(n)] = n.GetChildren()
            children = []
            for child in children_by_node[id(n)]:
                children.extend(collect(child))
            if children:
                return children
            name = n.Name or ''
            return [name] if name else []

        parts = collect(row)
        if parts:
            raw = '\n'.join(parts)
            separated = message_content(row, children_by_node)
            texts.append(ChatMessage(raw, separated) if separated is not None else raw)
    return texts


def read_group_windows(windows, group, is_minimized, capture_images=None):
    if not windows:
        raise UserFacingError('未找到聊天窗口，请先打开聊天客户端。')
    candidates, expanded = [], 0
    for w in windows:
        if is_minimized(w.NativeWindowHandle):
            continue
        expanded += 1
        regions = find_regions(w, group)
        if regions:
            candidates.append((w, regions))
    if not expanded:
        raise UserFacingError('聊天窗口已最小化，请恢复窗口。')
    if not candidates:
        raise UserFacingError(
            '未识别到目标群顶部标题和消息列表。请在聊天客户端中打开目标群；若已打开，请检查辅助模式是否生效。'
        )
    if len(candidates) > 1:
        raise UserFacingError('多个聊天窗口同时显示目标群，请仅保留一个目标聊天窗口。')
    window, (header, messages) = candidates[0]
    texts = read_messages(messages)
    if capture_images is not None:
        capture_images(window, messages)
    # Re-resolve the current UI after reading so changing chats cannot pass the initial guard alone.
    if not find_regions(window, group):
        raise UserFacingError('当前聊天已切换，本轮读取结果已丢弃。')
    return texts
