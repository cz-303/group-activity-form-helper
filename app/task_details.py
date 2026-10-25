"""Pure task-detail formatting shared by the background reader and UI."""

import json
import time
import difflib
from field_report import report_summary
from task_store import state_label
from reminders import countdown
from activity_time import time_references
from task_links import kind_label
from core import canonical_url
from providers.tencent_sheet import is_tencent_sheet


def format_notice(notices, unread):
    raw = '\n\n────────\n\n'.join(
        f"捕获于 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(n['captured']))} · {n['source']}\n\n{n['body']}"
        for n in notices
    )
    changes = ''
    if unread:
        bodies = list(dict.fromkeys(n['body'] for n in notices))
        if len(bodies) > 1:
            if sum(len(x) for x in bodies[:2]) > 20000 or sum(x.count('\n') for x in bodies[:2]) > 400:
                changes = '原通知有更新；内容较长，请直接对照下方各版本完整原文。\n\n'
            else:
                diff = [
                    line
                    for line in difflib.ndiff(bodies[1].splitlines(), bodies[0].splitlines())
                    if line.startswith(('+ ', '- '))
                ]
                changes = (
                    '原通知有更新（按捕获顺序比较，可能是旧通知重发，请核对）：\n＋新增 / －移除\n'
                    + '\n'.join(diff)
                    + '\n\n────────\n\n'
                )
    if not raw:
        raw = (
            '未保存通知原文。\n\n旧版任务和手动添加的链接可能没有原通知。如果监测时再次读到对应通知，会补充可读取文本。'
        )
    return changes + '以下为含链接的单条群聊消息可读取文本，不合并相邻消息；图片、语音等不转写。\n\n' + raw


def build_detail(row, notices, selected_count, notice_text=None):
    result = {'title': row['title'] or kind_label(row['url']), 'tabs': {}}
    meta = f"{state_label(row)}  ·  来源：{row['source']}"
    checklist = json.loads(row['checklist'])
    meta += f"\n意向：{row['intent']}  ·  准备进度：{sum(i['done'] for i in checklist)}/{len(checklist)}"
    if selected_count > 1:
        meta += f'\n已选中 {selected_count} 项，当前显示第一项详情。'
    result['meta'] = meta
    result['tabs']['通知原文'] = (
        notice_text if notice_text is not None else format_notice(notices, row['notice_unread'])
    )
    report = json.loads(row['field_report']) if row['field_report'] else None
    if is_tencent_sheet(row['url']):
        fields = '选择“表头读取预览”，核对表头和空行候选后打开填写预览。确认布局及本次资料后才执行写入，并重新读取和回读核验。在线表格可能即时保存，失败请在网页核对，不自动重试。仅适用于你已核对过布局的简单表格。'
    elif not canonical_url(row['url']):
        fields = '已支持文档链接捕获和打开；此版本尚未启用该平台的表格字段读取及填写。请在网页操作，在线表格输入后可能立即保存。'
    elif not report:
        fields = '尚未检查表单字段。网页打开、登录完成后会更新。'
    else:
        lines = [report_summary(report), '']
        if report['missing']:
            lines += ['待补填（空白文本题，包含可能的选填题）：'] + ['• ' + x for x in report['missing']] + ['']
        if report['review']:
            lines += ['需要人工核对：'] + ['• ' + x for x in report['review']] + ['']
        if report['blocked']:
            lines += ['暂时无法判断：'] + ['• ' + x for x in report['blocked']] + ['']
        lines += [
            f"已填写文本题：{report['filled']} / 已识别题目：{report['questions']}",
            '不显示或保存你的实际答案；提示不代替网页校验。最终请核对并自行提交。',
        ]
        fields = '\n'.join(lines)
    result['tabs']['待补填'] = fields
    timing = f"活动开始：{row['event_start'] or '未设置'}\n活动结束：{row['event_end'] or '未设置'}\n报名截止：{row['deadline'] or '未设置'}\n距离截止：{countdown(row['deadline'])}\n电脑当前时间：{time.strftime('%Y-%m-%d %H:%M')}"
    timing += '\n\n以上时间由你确认保存，按本机当地时间解释。仅填日期的截止时间按当天结束计算。\n到期只更新标记和筛选，不会自动提交、忽略或关闭任务。'
    timing += '\n\n通知中的时间参考（请核对原文）：\n' + (
        time_references(notices) or '未找到时间相关文字，可手动补充。'
    )
    result['tabs']['活动时间'] = timing
    mode = {
        'inherit': '沿用全局',
        'custom': '自定义 ' + ','.join(map(str, json.loads(row['task_minutes']))) + ' 分钟',
        'off': '关闭本任务截止提醒',
    }[row['reminder_mode']]
    info = (
        f"捕获：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['created']))} · 合并 {len(notices)} 份原文\n\n参加意向：{row['intent']}\n\n活动备注\n{row['activity_notes'] or '尚未填写'}\n\n准备清单\n"
        + '\n'.join(('✓ ' if i['done'] else '○ ') + i['text'] for i in checklist)
    )
    info += f"\n\n专属提醒：{mode}\n\n链接\n{row['url']}\n\n尝试次数：{row['attempts']}\n当前说明：{row['note'] or '等待处理'}"
    if row['remind_again_at']:
        info += '\n再次提醒：' + time.strftime('%Y-%m-%d %H:%M', time.localtime(row['remind_again_at']))
    if row['snooze_until']:
        info += '\n暂缓至：' + time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['snooze_until']))
    if row['retry_at']:
        info += '\n下次重试：' + time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['retry_at']))
    info += '\n\n忽略或暂缓不会关闭已经打开的网页；打开中的任务请等导航结束后操作。已打开不等于已经报名。'
    result['tabs']['处理信息'] = info
    return result
