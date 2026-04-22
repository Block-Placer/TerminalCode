from __future__ import annotations
import ast
import sys
from pathlib import Path

class DocstringAndCommentRemover(ast.NodeTransformer):

    def _remove_docstring(self, node):
        if not hasattr(node, 'body') or not node.body:
            return node
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            node.body.pop(0)
        return node

    def visit_Module(self, node: ast.Module):
        node = self.generic_visit(node)
        return self._remove_docstring(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        node = self.generic_visit(node)
        return self._remove_docstring(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        node = self.generic_visit(node)
        return self._remove_docstring(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        node = self.generic_visit(node)
        return self._remove_docstring(node)

def rewrite_file(path: Path) -> bool:
    try:
        src = path.read_text(encoding='utf-8')
    except Exception:
        return False
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    transformer = DocstringAndCommentRemover()
    tree = transformer.visit(tree)
    ast.fix_missing_locations(tree)
    try:
        new_src = ast.unparse(tree)
    except Exception:
        return False
    try:
        path.write_text(new_src, encoding='utf-8')
        return True
    except Exception:
        return False

def main(root: Path):
    py_files = list(root.rglob('*.py'))
    skipped = {'venv', '.venv', '.git'}
    changed = []
    for p in py_files:
        parts = set(p.parts)
        if parts & skipped:
            continue
        ok = rewrite_file(p)
        if ok:
            changed.append(p)
    for p in changed:
        print(f'Rewrote: {p}')
    print(f'Processed {len(py_files)} python files, rewritten {len(changed)} files.')
if __name__ == '__main__':
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    main(root)