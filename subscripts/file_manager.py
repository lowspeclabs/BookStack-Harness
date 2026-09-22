"""
BookStack File Manager (Import & Export Utilities)
Provides clean, modular functionality for:
1. File mention & import support (@filename auto-expansion and tab-completion)
2. Markdown & ZIP export of BookStack Pages, Books (folder of pages in zip), and Shelves (folder of books in zip)
"""

import os
import re
import sys
import zipfile
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import readline
except ImportError:
    readline = None

try:
    from prompt_toolkit.completion import Completer, Completion
except ImportError:
    Completer = object
    Completion = None


# -----------------------------------------------------------------------------
# 1. Local File Import & @-Mention Expansion
# -----------------------------------------------------------------------------

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    ".agent",
    "logs",
}

TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".yml",
    ".yaml",
    ".py",
    ".sh",
    ".bash",
    ".ini",
    ".conf",
    ".cfg",
    ".env.example",
    ".toml",
    ".rst",
    ".csv",
    ".sql",
    ".xml",
    ".html",
    ".js",
    ".ts",
}


def list_workspace_files(base_dir: Optional[Path] = None, max_depth: int = 3) -> List[str]:
    """List text and documentation files in the current working directory."""
    root = (base_dir or Path.cwd()).resolve()
    found: List[str] = []

    for path in root.rglob("*"):
        # Check exclusion in parents
        rel = path.relative_to(root)
        parts = rel.parts
        if any(part in EXCLUDED_DIRS for part in parts):
            continue
        if len(parts) > max_depth:
            continue
        if path.is_file():
            if path.suffix.lower() in TEXT_EXTENSIONS or path.name in ("Dockerfile", "Makefile", "requirements.txt"):
                found.append(str(rel))

    return sorted(found)


def select_file_interactive(base_dir: Optional[Path] = None, initial_filter: str = "") -> Optional[str]:
    """
    Renders an interactive terminal popup menu to select a local file.
    Supports Up/Down arrow navigation, typing to live-filter, Enter to select, Esc to cancel.
    """
    root = (base_dir or Path.cwd()).resolve()
    all_files = list_workspace_files(root)
    if not all_files:
        print("\n\033[33m⚠ No eligible files found in working directory.\033[0m\n")
        return None

    # Check if standard input is a TTY
    if not sys.stdin.isatty():
        return None

    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    query = initial_filter
    selected_idx = 0
    max_visible = 8
    last_rendered_lines = 0

    def get_filtered() -> List[str]:
        if not query:
            return all_files
        q_lower = query.lower()
        return [f for f in all_files if q_lower in f.lower()]

    try:
        tty.setraw(fd)
        sys.stdout.write("\033[?25l")  # Hide cursor

        while True:
            filtered = get_filtered()
            if not filtered:
                selected_idx = 0
            else:
                selected_idx = max(0, min(selected_idx, len(filtered) - 1))

            # Clear previous render
            if last_rendered_lines > 0:
                sys.stdout.write(f"\033[{last_rendered_lines}A\r")
                for _ in range(last_rendered_lines):
                    sys.stdout.write("\033[2K\n\r")
                sys.stdout.write(f"\033[{last_rendered_lines}A\r")

            # Prepare render buffer
            lines: List[str] = []
            filter_tag = f" [Filter: \033[1;36m{query}\033[0m]" if query else ""
            lines.append(f"\033[1;34m┌── 📁 Select File to Attach (@){filter_tag} \033[90m(↑/↓ Navigate, Enter Select, Esc Cancel)\033[0m\r")

            if not filtered:
                lines.append(f"\033[33m│   No files matched '{query}'\033[0m\r")
            else:
                # Viewport windowing
                start_idx = max(0, min(selected_idx - (max_visible // 2), len(filtered) - max_visible))
                visible_slice = filtered[start_idx : start_idx + max_visible]

                for v_i, file_path in enumerate(visible_slice):
                    actual_idx = start_idx + v_i
                    if actual_idx == selected_idx:
                        lines.append(f"\033[1;32m│ ▶ \033[7m {actual_idx + 1}. @{file_path} \033[0m\r")
                    else:
                        lines.append(f"\033[90m│   \033[37m{actual_idx + 1}. @{file_path}\033[0m\r")

                if len(filtered) > max_visible:
                    lines.append(f"\033[90m│   ... ({len(filtered)} total files in {root.name})\033[0m\r")

            lines.append("\033[1;34m└──\033[0m\r")

            # Write lines
            for l in lines:
                sys.stdout.write(f"{l}\n")
            sys.stdout.flush()
            last_rendered_lines = len(lines)

            # Read key/sequence as raw bytes
            raw = os.read(fd, 32)
            if not raw:
                break

            # 1. Arrow keys & Special Sequences
            if raw in (b"\x1b[A", b"\x1bOA"):  # Up arrow
                selected_idx = max(0, selected_idx - 1)
            elif raw in (b"\x1b[B", b"\x1bOB"):  # Down arrow
                selected_idx = min(len(filtered) - 1, selected_idx + 1)
            elif raw in (b"\x1b",):  # Escape / Cancel
                break
            elif raw in (b"\r", b"\n"):  # Enter
                if filtered and 0 <= selected_idx < len(filtered):
                    chosen = filtered[selected_idx]
                    return chosen
                break
            elif raw in (b"\x03", b"\x04"):  # Ctrl+C or Ctrl+D
                break
            elif raw in (b"\x7f", b"\x08", b"\x1b[3~"):  # Backspace / Delete
                if query:
                    query = query[:-1]
                    selected_idx = 0
            else:
                # Text input for live filtering
                try:
                    text_input = raw.decode("utf-8", errors="ignore")
                    # Ignore other escape sequences (like F-keys, page up/down)
                    if "\x1b" not in text_input and text_input.isprintable():
                        query += text_input
                        selected_idx = 0
                except Exception:
                    pass

    finally:
        sys.stdout.write("\033[?25h")  # Show cursor
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        if last_rendered_lines > 0:
            sys.stdout.write(f"\033[{last_rendered_lines}A\r")
            for _ in range(last_rendered_lines):
                sys.stdout.write("\033[2K\n\r")
            sys.stdout.write(f"\033[{last_rendered_lines}A\r")
            sys.stdout.flush()

    return None


def setup_file_completer(base_dir: Optional[Path] = None):
    """Configures GNU readline to auto-complete @file paths on Tab keypress."""
    if not readline:
        return

    root = (base_dir or Path.cwd()).resolve()

    def completer(text: str, state: int) -> Optional[str]:
        line = readline.get_line_buffer()
        endidx = readline.get_endidx()
        current_token = line[:endidx].split()[-1] if line[:endidx].split() else ""

        all_files = list_workspace_files(root)

        if current_token.startswith("@"):
            prefix = current_token[1:]
            matches = [f"@{f}" for f in all_files if f.startswith(prefix)]
        else:
            matches = [f for f in all_files if f.startswith(text)]

        if state < len(matches):
            return matches[state]
        return None

    readline.set_completer_delims(" \t\n`'\"=;")
    readline.set_completer(completer)
    readline.parse_and_bind("tab: complete")
    try:
        readline.parse_and_bind("set enable-bracketed-paste on")
    except Exception:
        pass


class FileMentionCompleter(Completer):
    """Auto-completes @file paths and workspace filenames in prompt_toolkit."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = (base_dir or Path.cwd()).resolve()

    def get_completions(self, document, complete_event):
        if not Completion:
            return
        text_before_cursor = document.text_before_cursor
        current_token = text_before_cursor.split()[-1] if text_before_cursor.split() else ""
        all_files = list_workspace_files(self.base_dir)

        if current_token.startswith("@"):
            prefix = current_token[1:]
            for f in all_files:
                if f.startswith(prefix):
                    yield Completion(f"@{f}", start_position=-len(current_token))
        elif current_token:
            for f in all_files:
                if f.startswith(current_token):
                    yield Completion(f, start_position=-len(current_token))


def resolve_file_mentions(
    user_input: str,
    base_dir: Optional[Path] = None,
    max_file_size: int = 100_000,
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Finds '@filepath' references in user_input, loads their contents,
    and returns (augmented_prompt, attachments_metadata).
    """
    root = (base_dir or Path.cwd()).resolve()
    pattern = r'@(?:\"([^\"]+)\"|\'([^\']+)\'|([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9_\-]+|[a-zA-Z0-9_\-]+))'

    matches = re.finditer(pattern, user_input)
    attachments: List[Dict[str, Any]] = []
    seen_paths = set()

    for match in matches:
        raw_path = match.group(1) or match.group(2) or match.group(3)
        if not raw_path:
            continue

        target = (root / raw_path).resolve()
        # Security check: must reside inside root or relative path
        if target in seen_paths:
            continue

        if target.is_file():
            seen_paths.add(target)
            try:
                size = target.stat().st_size
                if size > max_file_size:
                    with open(target, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read(max_file_size) + f"\n\n... [Content truncated: file size {size} bytes exceeds limit]"
                else:
                    with open(target, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()

                rel_name = str(target.relative_to(root)) if target.is_relative_to(root) else target.name
                attachments.append({
                    "path": str(target),
                    "rel_path": rel_name,
                    "size": size,
                    "content": content,
                })
            except Exception as e:
                attachments.append({
                    "path": str(target),
                    "rel_path": raw_path,
                    "error": str(e),
                })

    if not attachments:
        return user_input, []

    # Build augmented context
    file_context_blocks = []
    for att in attachments:
        if "content" in att:
            file_context_blocks.append(
                f"### 📄 Attached Local File: `{att['rel_path']}` ({att['size']} bytes)\n"
                f"```markdown\n{att['content']}\n```"
            )
        elif "error" in att:
            file_context_blocks.append(
                f"### ⚠ Failed to load `{att['rel_path']}`: {att['error']}"
            )

    header = "\n\n".join(file_context_blocks)
    augmented_prompt = f"{header}\n\n---\n**User Instruction:** {user_input}"
    return augmented_prompt, attachments


# -----------------------------------------------------------------------------
# 2. Content Exporter (Markdown & ZIP for Pages, Books, Shelves)
# -----------------------------------------------------------------------------

def sanitize_slug(name: str) -> str:
    """Sanitize string for safe cross-platform filesystem paths."""
    s = re.sub(r"[^\w\-\.]+", "_", name.strip().lower())
    return s[:64] or "unnamed"


def export_page_md(client: Any, page_id: int, output_dir: Path) -> Dict[str, Any]:
    """Export a single page as a standalone Markdown file with metadata."""
    res = client.request("GET", f"pages/{page_id}")
    if not isinstance(res, dict):
        raise RuntimeError(f"Failed to fetch page #{page_id}")

    output_dir.mkdir(parents=True, exist_ok=True)
    slug = res.get("slug") or sanitize_slug(res.get("name", f"page_{page_id}"))
    out_file = output_dir / f"{slug}.md"

    md = res.get("markdown") or res.get("html") or ""
    tags = res.get("tags", [])
    tag_str = ", ".join([f"{t.get('name')}:{t.get('value')}" if t.get('value') else t.get('name', '') for t in tags])

    frontmatter = [
        "---",
        f"id: {res.get('id')}",
        f"title: \"{res.get('name')}\"",
        f"book_id: {res.get('book_id')}",
        f"updated_at: \"{res.get('updated_at')}\"",
    ]
    if tag_str:
        frontmatter.append(f"tags: [{tag_str}]")
    frontmatter.append("---\n")

    full_content = "\n".join(frontmatter) + f"\n# {res.get('name')}\n\n{md}\n"
    out_file.write_text(full_content, encoding="utf-8")

    return {
        "type": "page",
        "id": page_id,
        "name": res.get("name"),
        "file": str(out_file),
        "size": out_file.stat().st_size,
    }


def export_book_tree(client: Any, book_id: int, target_dir: Path) -> Dict[str, Any]:
    """
    Exports a book into a folder of pages (and chapter subfolders).
    Returns dict with summary info and book directory path.
    """
    book = client.request("GET", f"books/{book_id}")
    if not isinstance(book, dict):
        raise RuntimeError(f"Failed to fetch book #{book_id}")

    book_slug = book.get("slug") or sanitize_slug(book.get("name", f"book_{book_id}"))
    book_dir = target_dir / book_slug
    book_dir.mkdir(parents=True, exist_ok=True)

    # Book README
    readme_content = (
        f"# {book.get('name')}\n\n"
        f"**Book ID:** {book_id}  \n"
        f"**Description:** {book.get('description', '')}  \n"
        f"**Created:** {book.get('created_at')}  \n\n---\n"
    )
    (book_dir / "README.md").write_text(readme_content, encoding="utf-8")

    contents = book.get("contents", [])
    page_count = 0
    chapter_count = 0

    for item in contents:
        itype = item.get("type")
        if itype == "page":
            pid = item["id"]
            page_data = client.request("GET", f"pages/{pid}")
            if isinstance(page_data, dict):
                md = page_data.get("markdown") or page_data.get("html") or ""
                pslug = page_data.get("slug") or sanitize_slug(page_data.get("name", f"page_{pid}"))
                (book_dir / f"{pslug}.md").write_text(f"# {page_data.get('name')}\n\n{md}\n", encoding="utf-8")
                page_count += 1
        elif itype == "chapter":
            chapter_count += 1
            cid = item["id"]
            cslug = item.get("slug") or sanitize_slug(item.get("name", f"chapter_{cid}"))
            chap_dir = book_dir / cslug
            chap_dir.mkdir(parents=True, exist_ok=True)

            chap_desc = item.get("description", "")
            (chap_dir / "README.md").write_text(f"# Chapter: {item.get('name')}\n\n{chap_desc}\n", encoding="utf-8")

            for cp in item.get("pages", []):
                cpid = cp["id"]
                cp_data = client.request("GET", f"pages/{cpid}")
                if isinstance(cp_data, dict):
                    md = cp_data.get("markdown") or cp_data.get("html") or ""
                    cpslug = cp_data.get("slug") or sanitize_slug(cp_data.get("name", f"page_{cpid}"))
                    (chap_dir / f"{cpslug}.md").write_text(f"# {cp_data.get('name')}\n\n{md}\n", encoding="utf-8")
                    page_count += 1

    return {
        "type": "book",
        "id": book_id,
        "name": book.get("name"),
        "directory": book_dir,
        "page_count": page_count,
        "chapter_count": chapter_count,
    }


def zip_directory(source_dir: Path, zip_dest: Path) -> Path:
    """Recursively zips source_dir into zip_dest archive."""
    zip_dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(source_dir):
            for f in files:
                full_path = Path(root) / f
                arcname = full_path.relative_to(source_dir.parent)
                zf.write(full_path, arcname)
    return zip_dest


def export_book_zip(client: Any, book_id: int, output_dir: Path) -> Dict[str, Any]:
    """Exports a book into a folder of pages, packaged as a ZIP file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        info = export_book_tree(client, book_id, tmp_path)
        book_dir = info["directory"]
        book_slug = book_dir.name

        zip_path = output_dir / f"{book_slug}.zip"
        zip_directory(book_dir, zip_path)

        return {
            "status": "success",
            "type": "book",
            "id": book_id,
            "name": info["name"],
            "archive": str(zip_path),
            "size_bytes": zip_path.stat().st_size,
            "page_count": info["page_count"],
            "chapter_count": info["chapter_count"],
        }


def export_shelf_zip(client: Any, shelf_id: int, output_dir: Path) -> Dict[str, Any]:
    """
    Exports a shelf into a folder containing each of its books (with pages/chapters),
    packaged as a ZIP file.
    """
    shelf = client.request("GET", f"shelves/{shelf_id}")
    if not isinstance(shelf, dict):
        raise RuntimeError(f"Failed to fetch shelf #{shelf_id}")

    output_dir.mkdir(parents=True, exist_ok=True)
    shelf_slug = shelf.get("slug") or sanitize_slug(shelf.get("name", f"shelf_{shelf_id}"))

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        shelf_dir = tmp_path / shelf_slug
        shelf_dir.mkdir(parents=True, exist_ok=True)

        # Shelf README
        shelf_readme = (
            f"# Shelf: {shelf.get('name')}\n\n"
            f"**Shelf ID:** {shelf_id}  \n"
            f"**Description:** {shelf.get('description', '')}  \n"
            f"**Total Books:** {len(shelf.get('books', []))}  \n\n---\n"
        )
        (shelf_dir / "README.md").write_text(shelf_readme, encoding="utf-8")

        total_pages = 0
        total_chapters = 0
        exported_books = []

        for b in shelf.get("books", []):
            bid = b["id"]
            book_info = export_book_tree(client, bid, shelf_dir)
            total_pages += book_info["page_count"]
            total_chapters += book_info["chapter_count"]
            exported_books.append(book_info["name"])

        zip_path = output_dir / f"{shelf_slug}.zip"
        zip_directory(shelf_dir, zip_path)

        return {
            "status": "success",
            "type": "shelf",
            "id": shelf_id,
            "name": shelf.get("name"),
            "archive": str(zip_path),
            "size_bytes": zip_path.stat().st_size,
            "books_count": len(exported_books),
            "books": exported_books,
            "total_pages": total_pages,
            "total_chapters": total_chapters,
        }


def export_wiki_entity(
    client: Any,
    entity_type: str,
    entity_id: int,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Unified entrypoint for exporting pages (markdown), books (zip), or shelves (zip)."""
    entity_type = entity_type.strip().lower()
    base_out = Path(output_path).resolve() if output_path else Path("./exports").resolve()

    if entity_type == "page":
        return export_page_md(client, entity_id, base_out)
    elif entity_type == "book":
        return export_book_zip(client, entity_id, base_out)
    elif entity_type == "shelf":
        return export_shelf_zip(client, entity_id, base_out)
    else:
        raise ValueError(f"Unknown export entity type '{entity_type}'. Must be 'page', 'book', or 'shelf'.")
