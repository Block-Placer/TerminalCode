from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional, List
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window
from prompt_toolkit.widgets import TextArea, Frame, Label, SearchToolbar
from prompt_toolkit.shortcuts import input_dialog, message_dialog, radiolist_dialog
from prompt_toolkit.styles import Style
from prompt_toolkit.buffer import Buffer
from pygments.lexers import get_lexer_for_filename, guess_lexer
from pygments.lexers.special import TextLexer
from pygments.lexers import PythonLexer
from prompt_toolkit.lexers import PygmentsLexer
from .terminal_panel import TerminalPanel
from . import lsp_client
import shutil
import asyncio
import subprocess
import json
from prompt_toolkit.completion import Completer, Completion

class Tab:

    def __init__(self, path: Optional[Path], text: str='', search_toolbar: Optional[SearchToolbar]=None, on_change=None):
        self.path = path
        self.buffer = Buffer()
        self.dirty = False

        def _on_change(_):
            self.dirty = True
        try:
            self.buffer.on_text_changed += _on_change
            if on_change is not None:

                def _external(_):
                    try:
                        on_change(self)
                    except Exception:
                        pass
                self.buffer.on_text_changed += _external
        except Exception:
            pass
        kwargs = {'buffer': self.buffer, 'lexer': None, 'scrollbar': True, 'line_numbers': True, 'wrap_lines': False}
        if search_toolbar is not None:
            kwargs['search_field'] = search_toolbar
        try:
            from prompt_toolkit.completion import Completer

            class BufferCompleter(Completer):

                def get_completions(self, document, complete_event):
                    text = document.text_before_cursor
                    words = set(document.text.split())
                    for w in words:
                        if w.startswith(document.get_word_before_cursor() or '') and w:
                            yield Completion(w, start_position=-len(document.get_word_before_cursor() or ''))
            kwargs['completer'] = BufferCompleter()
        except Exception:
            pass
        self.text_area = TextArea(**kwargs)
        self.buffer.text = text

    def set_lexer_for_path(self):
        if not self.path:
            self.text_area.lexer = None
            return
        try:
            lexer = PygmentsLexer(get_lexer_for_filename(str(self.path)))
        except Exception:
            lexer = PygmentsLexer(TextLexer)
        self.text_area.lexer = lexer

class EditorApp:

    def __init__(self, root: Path):
        self.root = Path(root).expanduser()
        self.open_tabs: List[Tab] = []
        self.current = 0
        self.lsp: Optional[lsp_client.SimpleLSPClient] = None
        self.terminal = TerminalPanel()
        self.search_toolbar = SearchToolbar()
        self._watch_task = None
        self.file_explorer = TextArea(text=self._build_file_list(), width=30, scrollbar=True)
        self.status = Label(text=self._status_text())
        self.editor_frame = Frame(Label(text='No file opened'))
        self.terminal_output = TextArea(style='class:terminal', text='', scrollbar=True, height=10, read_only=True)
        self.terminal_input = TextArea(height=1, prompt='> ', multiline=False, wrap_lines=False)
        self.terminal_session_started = False
        kb = KeyBindings()

        @kb.add('c-o')
        def _(event):
            cursor = self.file_explorer.document.current_line
            path = (self.root / cursor.strip()).resolve()
            if path.exists() and path.is_file():
                self.open_file(path)

        @kb.add('c-s')
        def _(event):
            self.save_current()

        @kb.add('c-q')
        def _(event):
            event.app.exit()

        @kb.add('c-f')
        def _(event):
            event.app.layout.focus(self.search_toolbar.control)

        @kb.add('c-n')
        def _(event):
            try:
                value = input_dialog(title='New file', text='Enter new file path (relative to cwd):').run()
                if value:
                    newpath = (self.root / value).resolve()
                    if newpath.exists():
                        message_dialog(title='Error', text='File already exists').run()
                    else:
                        newpath.parent.mkdir(parents=True, exist_ok=True)
                        newpath.write_text('', encoding='utf-8')
                        self.file_explorer.text = self._build_file_list()
            except Exception as e:
                self.status.text = f'New file failed: {e}'

        @kb.add('c-r')
        def _(event):
            try:
                sel = self.file_explorer.document.current_line.strip()
                if not sel:
                    return
                src = (self.root / sel).resolve()
                if not src.exists():
                    self.status.text = 'Selected entry not found'
                    return
                dest = input_dialog(title='Rename', text='Enter new name (relative to cwd):').run()
                if dest:
                    dst = (self.root / dest).resolve()
                    src.rename(dst)
                    self.file_explorer.text = self._build_file_list()
            except Exception as e:
                self.status.text = f'Rename failed: {e}'

        @kb.add('c-d')
        def _(event):
            try:
                sel = self.file_explorer.document.current_line.strip()
                if not sel:
                    return
                fp = (self.root / sel).resolve()
                if not fp.exists():
                    self.status.text = 'Selected entry not found'
                    return
                confirm = message_dialog(title='Delete', text=f'Delete {sel}? Press OK to confirm.')
                ok = input_dialog(title='Confirm delete', text=f'Type YES to delete {sel}:').run()
                if ok == 'YES':
                    if fp.is_dir():
                        import shutil as _sh
                        _sh.rmtree(fp)
                    else:
                        fp.unlink()
                    self.file_explorer.text = self._build_file_list()
            except Exception as e:
                self.status.text = f'Delete failed: {e}'

        @kb.add('f2')
        def _(event):
            self.file_explorer.text = self._build_file_list()

        @kb.add('c-`')
        def _(event):
            if not self.terminal_session_started:
                try:
                    self.terminal.start_session('/bin/bash', on_output=lambda out: self._on_terminal_output(out))
                    self.terminal_session_started = True
                except Exception:
                    self.terminal_output.text += '\nFailed to start terminal session.'
            event.app.layout.focus(self.terminal_input)

        @kb.add('escape')
        def _(event):
            if self.open_tabs:
                event.app.layout.focus(self.open_tabs[self.current].text_area)

        @kb.add('c-space')
        def _(event):
            asyncio.get_event_loop().create_task(self._lsp_completion())

        @kb.add('c-k')
        def _(event):
            asyncio.get_event_loop().create_task(self._lsp_hover())

        @kb.add('c-z')
        def _(event):
            if self.open_tabs:
                try:
                    self.open_tabs[self.current].buffer.undo()
                except Exception:
                    pass

        @kb.add('c-y')
        def _(event):
            if self.open_tabs:
                try:
                    self.open_tabs[self.current].buffer.redo()
                except Exception:
                    pass

        @kb.add('c-t')
        def _(event):
            self.theme = 'light' if self.theme == 'dark' else 'dark'
            self._save_config()
            if self.theme == 'dark':
                self.style = Style.from_dict({'frame.border': 'ansiblue', 'frame.label': 'bold', 'status': 'reverse', 'editor.line-number': 'ansidarkgray', 'editor.current-line': 'bg:#1e1e1e', 'terminal': 'bg:#0b0b0b #ffffff'})
            else:
                self.style = Style.from_dict({'frame.border': 'ansiblue', 'frame.label': 'bold', 'status': 'reverse', 'editor.line-number': 'ansiblack', 'editor.current-line': 'bg:#ffffff', 'terminal': 'bg:#f8f8f8 #000000'})
            self.application.style = self.style
        self.kb = kb
        root_container = HSplit([VSplit([Frame(self.file_explorer, title='Explorer'), HSplit([Frame(self.editor_frame, title='Editor', width=100), self.search_toolbar])]), VSplit([self.status]), Frame(self.terminal_output, title='Terminal')])
        self.style = Style.from_dict({'frame.border': 'ansiblue', 'frame.label': 'bold', 'status': 'reverse', 'editor.line-number': 'ansidarkgray', 'editor.current-line': 'bg:#1e1e1e', 'terminal': 'bg:#0b0b0b #ffffff'})
        self.application = Application(layout=Layout(root_container), key_bindings=kb, full_screen=True, style=self.style)
        try:
            loop = asyncio.get_event_loop()
            self._watch_task = loop.create_task(self._watch_files())
        except Exception:
            self._watch_task = None

        def _accept_terminal(buff):
            text = buff.text
            if text:
                try:
                    self.terminal.write_input(text + '\n')
                except Exception:
                    try:
                        self.terminal_output.text += '\n[terminal write failed]'
                    except Exception:
                        pass
            buff.text = ''
        try:
            self.terminal_input.buffer.accept_handler = _accept_terminal
        except Exception:
            pass

    def _build_file_list(self) -> str:
        lines = []
        for p in sorted(self.root.iterdir()):
            lines.append(p.name + ('/' if p.is_dir() else ''))
        return '\n'.join(lines)

    def _on_terminal_output(self, chunk: str) -> None:
        try:
            self.terminal_output.text += chunk
        except Exception:
            try:
                self.terminal_output.text = (self.terminal_output.text or '') + chunk
            except Exception:
                pass

    async def _watch_files(self):
        try:
            prev = set(p.name for p in self.root.iterdir())
        except Exception:
            prev = set()
        while True:
            await asyncio.sleep(1.0)
            try:
                current = set(p.name for p in self.root.iterdir())
            except Exception:
                continue
            if current != prev:
                prev = current
                try:
                    self.file_explorer.text = self._build_file_list()
                except Exception:
                    pass

    def _status_text(self) -> str:
        return f'{len(self.open_tabs)} tabs | cwd: {self.root}'

    def open_file(self, path: Path):
        try:
            text = path.read_text(encoding='utf-8')
        except Exception:
            text = ''
        tab = Tab(path, text, search_toolbar=self.search_toolbar, on_change=lambda t: self._on_buffer_change(t))
        tab.set_lexer_for_path()
        self.open_tabs.append(tab)
        self.current = len(self.open_tabs) - 1
        self._render_current()

    def _render_current(self):
        if not self.open_tabs:
            self.editor_frame.body = Label(text='No file opened')
            return
        tab = self.open_tabs[self.current]
        tabs_labels = []
        for i, t in enumerate(self.open_tabs):
            name = t.path.name if t.path else f'untitled{i}'
            mark = '*' if getattr(t, 'dirty', False) else ''
            if i == self.current:
                tabs_labels.append(f'[ {name}{mark} ]')
            else:
                tabs_labels.append(f'  {name}{mark}  ')
        tabs_bar = Label(text=' '.join(tabs_labels))
        self.editor_frame.body = HSplit([tabs_bar, tab.text_area])
        self.status.text = self._status_text()

    def _on_buffer_change(self, tab: Tab):
        tab.dirty = True
        if self.lsp and tab.path:
            asyncio.get_event_loop().create_task(self.lsp.send_notification('textDocument/didChange', {'textDocument': {'uri': tab.path.as_uri(), 'version': 1}, 'contentChanges': [{'text': tab.buffer.text}]}))

    async def _lsp_completion(self):
        if not self.lsp or not self.open_tabs:
            self.status.text = 'LSP not running or no file'
            return
        tab = self.open_tabs[self.current]
        if not tab.path:
            self.status.text = 'Unsaved file'
            return
        doc = tab.buffer.document
        line = doc.cursor_position_row
        col = doc.cursor_position_col
        uri = tab.path.as_uri()
        params = {'textDocument': {'uri': uri}, 'position': {'line': line, 'character': col}}
        try:
            res = await self.lsp.send_request('textDocument/completion', params, timeout=2.0)
        except Exception as e:
            self.status.text = f'Completion request failed: {e}'
            return
        items = []
        if not res:
            return
        if isinstance(res, dict) and 'items' in res:
            raw_items = res['items']
        elif isinstance(res, list):
            raw_items = res
        else:
            raw_items = []
        choices = []
        for it in raw_items:
            label = it.get('label') if isinstance(it, dict) else str(it)
            insert = None
            edit = None
            if isinstance(it, dict):
                insert = it.get('insertText') or it.get('label')
                te = it.get('textEdit')
                if te and isinstance(te, dict):
                    edit = te
            choices.append((label, insert, edit))
        if not choices:
            self.status.text = 'No completions'
            return
        try:
            result = radiolist_dialog(title='Completion', text='Choose completion', values=[(i, c[0]) for i, c in enumerate(choices)]).run()
        except Exception:
            result = None
        if result is None:
            return
        idx = int(result)
        chosen = choices[idx][1]
        edit = choices[idx][2]
        if chosen is None:
            return
        try:
            doc = tab.buffer.document
            if edit:
                new_text = edit.get('newText')
                rng = edit.get('range')
                if rng and new_text is not None:
                    start = rng.get('start')
                    end = rng.get('end')
                    if start and end:
                        sline = start.get('line', 0)
                        schar = start.get('character', 0)
                        eline = end.get('line', 0)
                        echar = end.get('character', 0)
                        try:
                            start_offset = doc.translate_row_col_to_index(sline, schar)
                            end_offset = doc.translate_row_col_to_index(eline, echar)
                            # perform replacement
                            tab.buffer.delete(start_offset, end_offset - start_offset)
                            tab.buffer.insert_text(new_text, overwrite=False)
                        except Exception:
                            tab.buffer.insert_text(chosen)
                    else:
                        tab.buffer.insert_text(chosen)
                else:
                    tab.buffer.insert_text(chosen)
            else:
                tab.buffer.insert_text(chosen)
        except Exception:
            pass

    async def _lsp_hover(self):
        if not self.lsp or not self.open_tabs:
            self.status.text = 'LSP not running or no file'
            return
        tab = self.open_tabs[self.current]
        if not tab.path:
            self.status.text = 'Unsaved file'
            return
        doc = tab.buffer.document
        line = doc.cursor_position_row
        col = doc.cursor_position_col
        uri = tab.path.as_uri()
        params = {'textDocument': {'uri': uri}, 'position': {'line': line, 'character': col}}
        try:
            res = await self.lsp.send_request('textDocument/hover', params, timeout=2.0)
        except Exception as e:
            self.status.text = f'Hover request failed: {e}'
            return
        contents = None
        if not res:
            contents = ''
        else:
            contents = res.get('contents') if isinstance(res, dict) else res
        if isinstance(contents, dict) and 'value' in contents:
            text = contents['value']
        elif isinstance(contents, list):
            text = '\n'.join([c.get('value') if isinstance(c, dict) else str(c) for c in contents])
        else:
            text = str(contents)
        message_dialog(title='Hover', text=text or 'No info').run()

    def save_current(self):
        if not self.open_tabs:
            return
        tab = self.open_tabs[self.current]
        if not tab.path:
            return
        try:
            if tab.path.suffix.lower() == '.py':
                black_bin = shutil.which('black')
                if black_bin:
                    try:
                        p = subprocess.Popen([black_bin, '-'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        out, err = p.communicate(tab.buffer.text.encode('utf-8'))
                        if p.returncode == 0 and out:
                            tab.buffer.text = out.decode('utf-8')
                    except Exception:
                        pass
            tab.path.write_text(tab.buffer.text, encoding='utf-8')
            tab.dirty = False
            self.status.text = f'Saved {tab.path}'
            if self.lsp:
                asyncio.get_event_loop().create_task(self.lsp.send_notification('textDocument/didSave', {'textDocument': {'uri': tab.path.as_uri()}}))
        except Exception as e:
            self.status.text = f'Error saving: {e}'

    def search_in_current(self):
        if not self.open_tabs:
            return
        pass

    async def _run_current_file(self):
        if not self.open_tabs:
            self.terminal_output.text = 'No file to run'
            return
        tab = self.open_tabs[self.current]
        if not tab.path:
            self.terminal_output.text = 'Unsaved buffer'
            return
        ext = tab.path.suffix.lower()
        if ext in ('.py',):
            cmd = f'python "{tab.path}"'
        elif ext in ('.js',):
            cmd = f'node "{tab.path}"'
        else:
            cmd = f'cat "{tab.path}"'
        out = await self.terminal.run(cmd)
        self.terminal_output.text = out

    async def _start_lsp_for_current(self):
        if not self.open_tabs:
            self.status.text = 'No file to start LSP for'
            return
        tab = self.open_tabs[self.current]
        if not tab.path or tab.path.suffix.lower() != '.py':
            self.status.text = 'LSP currently only demo for Python'
            return
        if shutil.which('pylsp') is None:
            self.status.text = 'pylsp not found in PATH'
            return
        if self.lsp is not None:
            self.status.text = 'LSP already running'
            return
        self.lsp = lsp_client.SimpleLSPClient(['pylsp'])

        def handle_diagnostics(params):
            try:
                uri = params.get('uri')
                diags = params.get('diagnostics', [])
                pretty = json.dumps(diags, indent=2)
                self.terminal_output.text = f'Diagnostics for {uri}:\n{pretty}'
            except Exception:
                pass
        self.lsp.on_diagnostics = handle_diagnostics
        try:
            await self.lsp.start()
            try:
                res = await self.lsp.send_request('initialize', {'capabilities': {}}, timeout=5.0)
            except Exception:
                res = None
            await self.lsp.send_notification('initialized', {})
            self.status.text = 'LSP started (pylsp)'
            tab = self.open_tabs[self.current]
            if tab and tab.path:
                uri = tab.path.as_uri()
                await self.lsp.send_notification('textDocument/didOpen', {'textDocument': {'uri': uri, 'languageId': 'python', 'version': 1, 'text': tab.buffer.text}})
        except Exception as e:
            self.status.text = f'LSP start failed: {e}'

    def run(self):
        self.application.run()

def main():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print('This TUI requires a real terminal (TTY).')
        print()
        print('Run these commands in your terminal:')
        print('  cd /Users/yudi/Documents/coding/tui-code-editor')
        print('  source .venv/bin/activate   # or create/activate your own venv')
        print('  python -m tui_code_editor.main')
        print()
        print('If you want to run non-interactive checks, set TUI_HEADLESS=1 to run a quick smoke test.')
        sys.exit(1)
    root = Path.cwd()
    app = EditorApp(root)
    app.run()
if __name__ == '__main__':
    main()
