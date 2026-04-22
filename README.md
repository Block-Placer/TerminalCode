TUI Code Editor (prompt_toolkit)
================================

A terminal-based full-stack code editor built for macOS (and other UNIX-like terminals) using prompt_toolkit and Pygments.

Features
- File explorer (left pane)
- Multi-tab editor (center pane) with syntax highlighting using Pygments
- Tabs bar showing open files and dirty markers
- Search in file (Ctrl-F) via in-TUI search toolbar
- Run file or shell commands and view output in bottom terminal pane (F5 to run current file)
- Start a demo LSP (pylsp) for Python with Ctrl-L (demo skeleton) and view basic messages
- Colors, themes and improved layout

Keybindings
- Ctrl-O: Open file under cursor in explorer
- Ctrl-S: Save current file
- Ctrl-F: Focus search toolbar
- F5: Run current file (python/node/cat fallback)
- Ctrl-L: Start demo LSP for Python (if pylsp available)
- Ctrl-Q: Quit

Requirements
- Python 3.10+
- macOS Terminal or iTerm2 (works on Linux too)

Install
1. Create and activate a virtualenv (recommended):

   python -m venv .venv
   source .venv/bin/activate

2. Install dependencies:

   pip install -r requirements.txt

Run

   python -m tui_code_editor.main

Notes on LSP
This project contains a minimal LSP client skeleton in tui_code_editor/lsp_client.py. It demonstrates launching a language server as a subprocess and exchanging JSON-RPC messages. It is intentionally small — a production-ready LSP client needs full message handling, request/response ID management, capabilities, and robust error handling.

Contributors
Block-Placer

Contributions
This scaffold aims to be a starting point. If you want richer editing features (Undo/Redo history, code-completion, diagnostics, real PTY terminal, language integrations), we can add them incrementally.
