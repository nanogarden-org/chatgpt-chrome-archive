"""Windows desktop interface for ChatGPT Chrome Archive."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent


def validate_url(value):
    value = value.strip()
    p = urlparse(value)
    if p.scheme != 'https' or p.netloc not in ('chatgpt.com', 'chat.openai.com') or not re.fullmatch(r'/c/[A-Za-z0-9_-]+/?', p.path):
        raise ValueError('Enter a conversation URL: https://chatgpt.com/c/conversation-id')
    return value


def worker():
    import archive
    p = argparse.ArgumentParser()
    p.add_argument('command', choices=['login', 'index', 'capture', 'capture-url', 'add', 'verify'])
    p.add_argument('url', nargs='?')
    p.add_argument('--max-passes', type=int, default=400)
    p.add_argument('--stable-passes', type=int, default=10)
    p.add_argument('--retry-verified', action='store_true')
    p.add_argument('--stop-on-error', action='store_true')
    args = p.parse_args(sys.argv[2:])
    args.stop_event = threading.Event()
    if args.command != 'login':
        def control():
            for line in sys.stdin:
                if line.strip() == 'stop':
                    args.stop_event.set()
        threading.Thread(target=control, daemon=True).start()
    archive.setup_logging()
    getattr(archive, 'cmd_' + args.command.replace('-', '_'))(args)


class ArchiveGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('ChatGPT Archive — Local Chrome Archiver')
        self.geometry('1000x840')
        self.minsize(920, 760)
        self.process = None
        self.events = queue.Queue()
        self.rows = []
        self.controls = []
        self.stopping = False
        self.url = tk.StringVar()
        self.mode = tk.StringVar(value='all')
        self.retry = tk.BooleanVar(value=False)
        self.stop_error = tk.BooleanVar(value=False)
        self.maximum = tk.StringVar(value='400')
        self.stable = tk.StringVar(value='10')
        self.search = tk.StringVar()
        self.status = tk.StringVar(value='Ready')
        self.summary = tk.StringVar()
        self.detail = tk.StringVar(value='Select a conversation to see capture details.')
        self.build_ui()
        self.search.trace_add('write', lambda *_: self.populate())
        self.refresh()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    def button(self, parent, text, callback, lock=True):
        b = ttk.Button(parent, text=text, command=callback)
        b.pack(side='left', padx=(0, 8))
        if lock:
            self.controls.append(b)
        return b

    def build_ui(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill='both', expand=True)
        ttk.Label(root, text='ChatGPT Archive', font=('Segoe UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(root, text='Local Chrome conversation archive — no terminal required once launched').pack(anchor='w', pady=(0, 12))
        setup = ttk.LabelFrame(root, text='Setup & discovery', padding=10)
        setup.pack(fill='x', pady=(0, 10))
        row = ttk.Frame(setup)
        row.pack(fill='x')
        self.button(row, '1. Log In', lambda: self.start('login'))
        self.login_done = self.button(row, 'Finish Login', self.finish_login, False)
        self.login_done.configure(state='disabled')
        self.button(row, '2. Refresh Chat Index', lambda: self.start('index'))
        for label, var in [('Max passes:', self.maximum), ('Stable passes:', self.stable)]:
            ttk.Label(row, text=label).pack(side='left', padx=(8, 4))
            ttk.Spinbox(row, from_=1, to=5000, textvariable=var, width=6).pack(side='left')
        ttk.Label(setup, text='Log in in Chrome, then click Finish Login here. Your dedicated browser profile is reused.').pack(anchor='w', pady=(8, 0))
        source = ttk.LabelFrame(root, text='Source & capture options', padding=10)
        source.pack(fill='x', pady=(0, 10))
        row = ttk.Frame(source)
        row.pack(fill='x', pady=(0, 8))
        for label, value in [('All indexed chats', 'all'), ('Single conversation URL', 'url')]:
            ttk.Radiobutton(row, text=label, value=value, variable=self.mode).pack(side='left', padx=(0, 16))
        row = ttk.Frame(source)
        row.pack(fill='x')
        ttk.Label(row, text='ChatGPT URL:', width=14).pack(side='left')
        ttk.Entry(row, textvariable=self.url).pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.button(row, 'Add to Index', lambda: self.start('add'))
        row = ttk.Frame(source)
        row.pack(fill='x', pady=(8, 0))
        ttk.Checkbutton(row, text='Recapture verified chats (batch)', variable=self.retry).pack(side='left')
        ttk.Checkbutton(row, text='Stop batch on capture error', variable=self.stop_error).pack(side='left', padx=14)
        ttk.Label(source, text=f'Archive folder: {ROOT / "archive"}').pack(anchor='w', pady=(8, 0))
        row = ttk.Frame(root)
        row.pack(fill='x', pady=(0, 8))
        self.button(row, '3. Start Capture', lambda: self.start('capture' if self.mode.get() == 'all' else 'capture-url'))
        self.stop_button = self.button(row, 'Stop After Current Chat', self.stop, False)
        self.stop_button.configure(state='disabled')
        self.button(row, '4. Verify Archive', lambda: self.start('verify'))
        self.button(row, 'Open Archive Folder', lambda: self.open_path(ROOT / 'archive'), False)
        ttk.Label(row, textvariable=self.status).pack(side='right')
        self.progress = ttk.Progressbar(root, mode='indeterminate')
        self.progress.pack(fill='x', pady=(0, 8))
        box = ttk.LabelFrame(root, text='Conversations', padding=8)
        box.pack(fill='both', expand=True, pady=(0, 10))
        row = ttk.Frame(box)
        row.pack(fill='x', pady=(0, 6))
        ttk.Label(row, text='Search:').pack(side='left', padx=(0, 6))
        ttk.Entry(row, textvariable=self.search, width=25).pack(side='left')
        self.button(row, 'Reload List', self.refresh)
        self.button(row, 'Open Markdown', self.open_selected, False)
        ttk.Label(row, textvariable=self.summary).pack(side='right')
        table = ttk.Frame(box)
        table.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(table, columns=('title', 'status', 'messages', 'date'), show='headings', height=7, selectmode='browse')
        for key, title, width in [('title', 'Conversation', 440), ('status', 'Status', 90), ('messages', 'Messages', 75), ('date', 'Captured (UTC)', 155)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=60, stretch=key == 'title')
        self.tree.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scroll.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.tag_configure('verified', foreground='#18713a')
        self.tree.tag_configure('failed', foreground='#b3261e')
        self.tree.bind('<<TreeviewSelect>>', self.select)
        self.tree.bind('<Double-1>', lambda _: self.open_selected())
        ttk.Label(box, textvariable=self.detail, wraplength=880).pack(anchor='w', pady=(6, 0))
        box = ttk.LabelFrame(root, text='Live log', padding=6)
        box.pack(fill='both', expand=True)
        self.log = tk.Text(box, wrap='word', state='disabled', font=('Consolas', 10), height=9)
        self.log.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(box, orient='vertical', command=self.log.yview)
        scroll.pack(side='right', fill='y')
        self.log.configure(yscrollcommand=scroll.set)

    def refresh(self):
        from utils import read_index
        try:
            self.rows = read_index()
            self.populate()
        except (OSError, ValueError) as exc:
            self.append(f'Could not read index: {exc}\n')

    def populate(self):
        self.tree.delete(*self.tree.get_children())
        query = self.search.get().lower().strip()
        for i, row in enumerate(self.rows):
            if query and query not in ' '.join(str(v) for v in row.values()).lower():
                continue
            status = row.get('status') or 'pending'
            self.tree.insert('', 'end', iid=str(i), values=(row.get('title') or row.get('conversation_id'), status, row.get('message_count', ''), row.get('captured_at', '')[:19].replace('T', ' ')), tags=(status,))
        verified = sum(r.get('status') == 'verified' for r in self.rows)
        failed = sum(r.get('status') == 'failed' for r in self.rows)
        self.summary.set(f'{len(self.rows)} total · {verified} verified · {failed} failed')

    def selected(self):
        selected = self.tree.selection()
        return self.rows[int(selected[0])] if selected else None

    def select(self, _=None):
        row = self.selected()
        if row:
            self.url.set(row.get('url', ''))
            self.detail.set((row.get('error') or row.get('url') or '')[:450])

    def open_selected(self):
        row = self.selected()
        if not row:
            messagebox.showinfo('Choose a conversation', 'Select a conversation in the list first.')
            return
        folder = (ROOT / 'archive' / row['conversation_id']).resolve()
        if folder.parent != (ROOT / 'archive').resolve():
            messagebox.showerror('Invalid archive path', 'The conversation ID is not a valid folder name.')
            return
        self.open_path(folder / 'conversation.md')

    def open_path(self, path):
        if not path.exists():
            messagebox.showinfo('Not available yet', f'No saved output at:\n{path}')
            return
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror('Could not open', str(exc))

    def build_command(self, command):
        python = ROOT / '.venv' / 'Scripts' / 'python.exe'
        if not python.exists():
            python = Path(sys.executable).with_name('python.exe')
        args = [str(python), '-u', str(Path(__file__).resolve()), '--worker', command]
        if command in ('capture-url', 'add'):
            args.append(validate_url(self.url.get()))
        if command == 'index':
            try:
                maximum, stable = int(self.maximum.get()), int(self.stable.get())
            except ValueError:
                raise ValueError('Index passes must be whole numbers.') from None
            if not 1 <= stable <= maximum <= 5000:
                raise ValueError('Use 1 ≤ stable passes ≤ max passes ≤ 5000.')
            args.extend(['--max-passes', str(maximum), '--stable-passes', str(stable)])
        if command == 'capture':
            if self.retry.get():
                args.append('--retry-verified')
            if self.stop_error.get():
                args.append('--stop-on-error')
        return args

    def start(self, command):
        if self.process:
            return
        try:
            args = self.build_command(command)
            replacing = command == 'capture' and self.retry.get()
            if command == 'capture-url':
                cid = urlparse(args[-1]).path.rstrip('/').split('/')[-1]
                replacing = (ROOT / 'archive' / cid / 'conversation.md').exists()
            if replacing and not messagebox.askyesno('Replace existing captures', 'This will replace existing captures. Continue?'):
                return
            env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
            self.process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', bufsize=1, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as exc:
            messagebox.showerror('Cannot start', str(exc))
            return
        self.stopping = False
        self.append(f'\n── {command.upper()} ──\n')
        self.status.set('Running: ' + command)
        for control in self.controls:
            control.configure(state='disabled')
        self.stop_button.configure(state='normal' if command == 'capture' else 'disabled')
        self.login_done.configure(state='normal' if command == 'login' else 'disabled')
        if command == 'login':
            self.append('Log in in Chrome, then click Finish Login here.\n')
        self.progress.start(10)
        threading.Thread(target=self.reader, args=(self.process,), daemon=True).start()

    def reader(self, process):
        try:
            for line in process.stdout:
                self.events.put(('line', line))
        finally:
            process.stdout.close()
            self.events.put(('done', process.wait()))

    def send(self, text):
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.write(text + '\n')
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass

    def finish_login(self):
        self.send('')
        self.login_done.configure(state='disabled')
        self.status.set('Closing login browser…')

    def stop(self):
        self.send('stop')
        self.stopping = True
        self.stop_button.configure(state='disabled')
        self.status.set('Stopping after current chat…')
        self.append('Stop requested. The current chat will finish and save before the batch stops.\n')

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text)
        if int(self.log.index('end-1c').split('.')[0]) > 4000:
            self.log.delete('1.0', '1000.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def poll(self):
        for _ in range(250):
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'line':
                self.append(data)
            else:
                self.process.stdin.close()
                self.process = None
                self.progress.stop()
                for control in self.controls:
                    control.configure(state='normal')
                self.stop_button.configure(state='disabled')
                self.login_done.configure(state='disabled')
                self.status.set('Error — see log' if data else ('Stopped' if self.stopping else 'Finished — review results'))
                self.append(f'\nProcess finished (exit code {data}). Review log and conversation statuses for verification results.\n')
                self.refresh()
        self.after(100, self.poll)

    def close(self):
        if self.process:
            messagebox.showinfo('Operation in progress', 'Finish Login or wait for the operation to finish before closing. For a batch, Stop After Current Chat saves progress before stopping.')
            return
        self.destroy()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        worker()
    else:
        ArchiveGUI().mainloop()
