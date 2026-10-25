"""Explicit UI feedback; severity is never guessed from notification text."""

import tkinter as tk
from tkinter import ttk
from design_tokens import STATES


class StatusVar(tk.StringVar):
    """Retain raw get() compatibility; keep severity separate from user content."""

    def __init__(self, master=None, value='', state='info'):
        self.state = state if state in STATES else 'info'
        super().__init__(master, value)

    def set(self, value, state='info'):
        self.state = state if state in STATES else 'info'
        super().set(value)


class StatusLabel(ttk.Label):
    def __init__(self, master, variable, **kwargs):
        self.variable = variable
        super().__init__(master, **kwargs)
        self._trace = variable.trace_add('write', self._refresh)
        self.bind('<Destroy>', self._release, add='+')
        self._refresh()

    def _refresh(self, *_):
        value = self.variable.get()
        state = self.variable.state
        self.configure(text=f'{STATES[state][0]} · {value}' if value else '', style=f'Status.{state}.TLabel')

    def _release(self, event):
        if event.widget is self and self._trace:
            self.variable.trace_remove('write', self._trace)
            self._trace = None
