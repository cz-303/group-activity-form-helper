"""One compact image manager, available from activity details or the inbox."""

import io, time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk, ImageGrab
from attachment_store import add_image, images, get_image, associate, detach, delete_image, export_image
from appearance import themed
from ui_layout import fit_window, ResizeUpdate, bind_wrap, set_wrap
from ui_status import StatusVar, StatusLabel
from diagnostics import describe_error, record_error
from task_links import canonical_task_url


def show_images(app, url=None):
    if getattr(app, 'image_window', None) and app.image_window.winfo_exists():
        app.image_window.lift()
        return
    win = tk.Toplevel(app.root)
    win.title('活动图片与群二维码')
    win.transient(app.root)
    app.image_window = win
    fit_window(win, 900, 680, (700, 500))
    frame = ttk.Frame(win, padding=16)
    frame.pack(fill='both', expand=True)
    feedback = StatusVar(win, '图片先进入待关联列表；选中后确认关联到活动。', state='info')
    status = StatusLabel(frame, feedback, wraplength=800)
    status.pack(fill='x')
    target = tk.StringVar(value=url or '')
    scope = tk.StringVar(value='本活动' if url else '待关联')
    row = ttk.Frame(frame)
    row.pack(fill='x', pady=8)
    ttk.Label(row, text='查看').pack(side='left')
    chooser = ttk.Combobox(row, textvariable=scope, values=['本活动', '待关联', '全部'], state='readonly', width=10)
    chooser.pack(side='left', padx=8)
    ttk.Label(row, text='关联活动链接').pack(side='left')
    ttk.Entry(row, textvariable=target).pack(side='left', fill='x', expand=True, padx=8)
    hint = ttk.Label(
        frame, text='可粘贴已保存到任务清单的链接。二维码只在本地展示；关联前请核对活动和群名。', wraplength=820
    )
    hint.pack(anchor='w')
    frame.bind(
        '<Configure>',
        ResizeUpdate(
            frame, lambda e: (set_wrap(hint, max(160, e.width - 32)), set_wrap(status, max(160, e.width - 32)))
        ).request,
    )
    panes = ttk.Panedwindow(frame, orient='horizontal')
    panes.pack(fill='both', expand=True, pady=8)
    left = ttk.Frame(panes)
    right = ttk.Frame(panes)
    panes.add(left, weight=1)
    panes.add(right, weight=2)
    table = ttk.Treeview(left, columns=('kind', 'source'), show='headings', selectmode='browse', height=10)
    table.heading('kind', text='图片', anchor='w')
    table.heading('source', text='来源', anchor='w')
    table.column('kind', width=120)
    table.column('source', width=150)
    scroll = ttk.Scrollbar(left, command=table.yview)
    table.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right', fill='y')
    table.pack(fill='both', expand=True)
    picture = ttk.Label(right, text='选择图片查看预览', anchor='center')
    picture.pack(fill='both', expand=True)
    caption = ttk.Label(right, wraplength=460)
    caption.pack(fill='x', pady=8)
    bind_wrap(right, caption, lambda e: max(160, e.width - 16))
    photo = [None]
    records = {}

    def failed(error):
        record_error(app.logger, 'activity-image', error)
        if win.winfo_exists():
            feedback.set(describe_error(error), state='error')

    def selected():
        ids = table.selection()
        return ids[0] if ids else None

    def refresh():
        if not win.winfo_exists():
            return
        keep = selected()
        rows = images(
            app.store, url=target.get().strip() if scope.get() == '本活动' else None, pending=scope.get() == '待关联'
        )
        records.clear()
        records.update({r['id']: r for r in rows})
        table.delete(*table.get_children())
        for r in rows:
            table.insert(
                '', 'end', iid=r['id'], values=('二维码图片' if r['is_qr'] else '图片（未识别码）', r['source'])
            )
        if keep in records:
            table.selection_set(keep)
        elif rows:
            table.selection_set(rows[0]['id'])
        else:
            photo[0] = None
            picture.configure(image='', text='当前范围没有图片')
            caption.configure(text='')
            feedback.set('当前范围没有图片，可导入文件或粘贴图片。', state='empty')

    def preview(*_):
        key = selected()
        if not key:
            return
        try:
            with Image.open(io.BytesIO(get_image(app.store, key))) as original:
                width = max(240, right.winfo_width() - 16)
                height = max(180, picture.winfo_height() - 16)
                scale = min(width / original.width, height / original.height, 4)
                resized = original.resize(
                    (max(1, int(original.width * scale)), max(1, int(original.height * scale))),
                    Image.Resampling.NEAREST if records[key]['is_qr'] else Image.Resampling.LANCZOS,
                )
                photo[0] = ImageTk.PhotoImage(resized, master=win)
            picture.configure(image=photo[0], text='')
            r = records[key]
            caption.configure(
                text=f"来源：{r['source']}\n保存时间：{time.strftime('%Y-%m-%d %H:%M', time.localtime(r['created']))}\n已关联 {r['linked']} 个活动；识别二维码不代表已验证群身份。"
            )
        except Exception as e:
            failed(e)

    def reload(*_):
        try:
            refresh()
        except Exception as e:
            failed(e)

    table.bind('<<TreeviewSelect>>', preview)
    chooser.bind('<<ComboboxSelected>>', reload)

    def imported(result):
        if not win.winfo_exists():
            return
        key, new = result
        scope.set('待关联')
        refresh()
        feedback.set(
            '图片已保存，请选中并确认关联。' if new else '相同图片已保存，可切换“全部”查看。',
            state='success' if new else 'info',
        )
        if table.exists(key):
            table.selection_set(key)

    def import_file():
        path = filedialog.askopenfilename(
            parent=win, title='导入二维码或活动图片', filetypes=[('图片', '*.png *.jpg *.jpeg *.webp *.bmp')]
        )
        if path:
            app.run_file_job('导入图片', lambda: add_image(app.store, path), imported, feedback=feedback)

    def paste():
        try:
            image = ImageGrab.grabclipboard()
            if not isinstance(image, Image.Image):
                raise ValueError('剪贴板中没有图片，请先在聊天客户端中复制图片。')
            app.run_file_job(
                '保存粘贴图片', lambda: add_image(app.store, image, '剪贴板图片'), imported, feedback=feedback
            )
        except Exception as e:
            failed(e)

    def link():
        key = selected()
        url = canonical_task_url(target.get().strip())
        if not key:
            return
        if not url:
            feedback.set('请填写支持的完整活动链接。', state='warning')
            return
        try:
            task = app.store.task(url)
        except Exception as e:
            failed(e)
            return
        if not task:
            feedback.set('请填写已存在于任务清单中的完整链接。', state='warning')
            return
        if not messagebox.askyesno(
            '确认关联', f"将选中图片关联到：\n{task['title'] or url}\n\n请核对预览中的活动群。", parent=win
        ):
            return
        try:
            associate(app.store, key, url)
            target.set(url)
            scope.set('本活动')
            refresh()
            feedback.set('图片已关联到本活动。', state='success')
        except Exception as e:
            failed(e)

    def unlink():
        if not selected():
            return
        try:
            url = canonical_task_url(target.get().strip())
            if not url or not any(r['id'] == selected() for r in images(app.store, url=url)):
                feedback.set('所选图片没有关联到当前链接，请先核对活动。', state='warning')
                return
            detach(app.store, selected(), url)
            refresh()
            feedback.set('已解除当前活动的关联；图片仍保留。', state='success')
        except Exception as e:
            failed(e)

    def remove():
        key = selected()
        if not key:
            return
        if not messagebox.askyesno(
            '删除本地图片',
            '删除选中图片及其所有活动关联？\n已有备份或外部导出的图片仍会保留。',
            parent=win,
            default='no',
        ):
            return
        try:
            delete_image(app.store, key)
            refresh()
            feedback.set('本地图片记录已删除；已有备份、外部导出及磁盘残留不受影响。', state='success')
        except Exception as e:
            failed(e)

    def export():
        key = selected()
        if not key:
            return
        path = filedialog.asksaveasfilename(
            parent=win,
            title='保存图片',
            defaultextension='.png',
            initialfile='活动群二维码.png',
            filetypes=[('PNG 图片', '*.png')],
        )

        def done(_):
            if win.winfo_exists():
                feedback.set('图片已导出。', state='success')

        if path:
            app.run_file_job('导出图片', lambda: export_image(app.store, key, path, app.data), done, feedback=feedback)

    buttons = ttk.Frame(frame)
    buttons.pack(fill='x')
    for label, command in [('导入图片', import_file), ('粘贴图片', paste), ('保存图片', export), ('刷新', reload)]:
        ttk.Button(buttons, text=label, command=command).pack(side='left', padx=(0, 8))
    actions = ttk.Frame(frame)
    actions.pack(fill='x', pady=(8, 0))
    ttk.Button(actions, text='解除关联', command=unlink).pack(side='left', padx=(0, 8))
    ttk.Button(actions, text='删除图片', style='Danger.TButton', command=remove).pack(side='left')
    ttk.Button(actions, text='关联到活动', style='Primary.TButton', command=link).pack(side='right')
    reload()
    themed(app, win)
