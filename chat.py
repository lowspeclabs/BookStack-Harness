#!/usr/bin/env python3
"""
BookStack Chat Agent
An autonomous, interactive AI assistant that interacts with your BookStack wiki
using tool calling against the BookStack API.

Supports any OpenAI-compatible LLM endpoint (LM Studio, Ollama, vLLM, etc.).
Features harness-side goal tracking, plan management, and interactive @file resolution.
"""

import os
import re
import sys
import json
import time
import shutil
import textwrap
import argparse
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

# Auto-switch to .venv python if executed outside venv
_venv_python = Path(__file__).resolve().parent / ".venv" / "bin" / "python3"
if _venv_python.is_file() and Path(sys.executable).resolve() != _venv_python.resolve():
    os.execv(str(_venv_python), [str(_venv_python)] + sys.argv)

try:
    import requests
    from dotenv import load_dotenv
except ImportError:
    print(
        "Error: Missing required packages. Run `./setup.sh` or:\n"
        "  pip install -r requirements.txt\n",
        file=sys.stderr,
    )
    sys.exit(1)

# Import BookStackClient and modular utilities from subscripts
try:
    from subscripts.bookstack import BookStackClient
except ImportError:
    # Ensure parent directory is in sys.path
    sys.path.insert(0, str(Path(__file__).parent.resolve()))
    from subscripts.bookstack import BookStackClient

# Import modular utilities from subscripts
from subscripts.file_manager import (
    resolve_file_mentions,
    list_workspace_files,
    setup_file_completer,
    select_file_interactive,
    export_wiki_entity,
    FileMentionCompleter,
)
from subscripts.goal_tracker import GoalTracker

# Import prompt_toolkit for bulletproof bracketed paste and multi-line clipboard handling
try:
    import prompt_toolkit
    from prompt_toolkit import PromptSession, prompt as pt_prompt
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.history import InMemoryHistory
except ImportError:
    PromptSession = None
    pt_prompt = None
    ANSI = lambda x: x
    InMemoryHistory = None

# Enable GNU Readline bracketed paste mode globally as fallback
try:
    import readline
    readline.parse_and_bind("set enable-bracketed-paste on")
except Exception:
    pass


def flush_stdin():
    """Flush any pending unread keystrokes or pasted text from the standard input buffer."""
    try:
        import termios
        if hasattr(sys.stdin, "fileno") and sys.stdin.isatty():
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
            return
    except Exception:
        pass

    try:
        import msvcrt
        while msvcrt.kbhit():
            msvcrt.getch()
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Lightweight Markdown Terminal Renderer
# -----------------------------------------------------------------------------

def render_markdown(text: str) -> str:
    """Lightweight ANSI markdown renderer for terminal formatting."""
    lines = text.split("\n")
    out = []
    i = 0
    n = len(lines)

    def inline(t: str) -> str:
        # bold **text** or __text__
        t = re.sub(r"\*\*(.+?)\*\*", r"\033[1m\1\033[22m", t)
        t = re.sub(r"__(.+?)__", r"\033[1m\1\033[22m", t)
        # inline code `code`
        t = re.sub(r"`([^`]+)`", r"\033[33m\1\033[39m", t)
        # italic *text* or _text_
        t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\033[3m\1\033[23m", t)
        t = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\033[3m\1\033[23m", t)
        return t

    def visible_len(s: str) -> int:
        cleaned = re.sub(r"(\*\*|__|\*|_|`)", "", s)
        return len(cleaned)

    while i < n:
        line = lines[i]

        # 1. Code blocks
        if line.startswith("```"):
            lang = line[3:].strip() or "code"
            out.append(f"\033[90m┌──\033[0m \033[36m{lang}\033[0m")
            i += 1
            while i < n and not lines[i].startswith("```"):
                out.append(f"\033[90m│\033[0m   \033[37m{lines[i]}\033[0m")
                i += 1
            out.append("\033[90m└──\033[0m")
            i += 1
            continue

        # 2. Markdown Tables
        if line.strip().startswith("|") and line.strip().endswith("|") and i + 1 < n and ("---" in lines[i + 1] or ":--" in lines[i + 1]):
            table_rows = []
            while i < n and lines[i].strip().startswith("|") and lines[i].strip().endswith("|"):
                row = [c.strip() for c in lines[i].strip()[1:-1].split("|")]
                table_rows.append(row)
                i += 1

            if len(table_rows) >= 2:
                header = table_rows[0]
                rows = table_rows[2:] if len(table_rows) > 2 else []
                num_cols = len(header)

                for r in rows:
                    while len(r) < num_cols:
                        r.append("")

                col_widths = [visible_len(h) for h in header]
                for r in rows:
                    for col_idx in range(num_cols):
                        col_widths[col_idx] = max(col_widths[col_idx], visible_len(r[col_idx]))

                col_widths = [max(w, 3) for w in col_widths]

                top_b = "┌" + "┬".join("─" * (w + 2) for w in col_widths) + "┐"
                mid_b = "├" + "┼".join("─" * (w + 2) for w in col_widths) + "┤"
                bot_b = "└" + "┴".join("─" * (w + 2) for w in col_widths) + "┘"

                out.append(f"\033[90m{top_b}\033[0m")

                h_cells = []
                for idx, h in enumerate(header):
                    pad = col_widths[idx] - visible_len(h)
                    h_cells.append(f" \033[1;36m{inline(h)}\033[0m" + (" " * pad) + " ")
                out.append("\033[90m│\033[0m" + "\033[90m│\033[0m".join(h_cells) + "\033[90m│\033[0m")

                out.append(f"\033[90m{mid_b}\033[0m")

                for r in rows:
                    r_cells = []
                    for idx in range(num_cols):
                        val = r[idx]
                        pad = col_widths[idx] - visible_len(val)
                        r_cells.append(f" {inline(val)}" + (" " * pad) + " ")
                    out.append("\033[90m│\033[0m" + "\033[90m│\033[0m".join(r_cells) + "\033[90m│\033[0m")

                out.append(f"\033[90m{bot_b}\033[0m")
                continue

        # 3. Headers
        if line.startswith("# "):
            out.append(f"\n\033[1;36m━━━ {inline(line[2:].strip())} ━━━\033[0m")
            i += 1
            continue
        elif line.startswith("## "):
            out.append(f"\n\033[1;34m▶ {inline(line[3:].strip())}\033[0m")
            i += 1
            continue
        elif line.startswith("### "):
            out.append(f"\n\033[1;33m◆ {inline(line[4:].strip())}\033[0m")
            i += 1
            continue
        elif line.startswith("#### "):
            out.append(f"\n\033[1;37m• {inline(line[5:].strip())}\033[0m")
            i += 1
            continue

        # 4. Horizontal Rules
        if re.match(r"^(\-{3,}|\*{3,}|_{3,})$", line.strip()):
            out.append("\033[90m" + "─" * 60 + "\033[0m")
            i += 1
            continue

        # 5. Blockquotes
        if line.startswith(">"):
            quote_text = line.lstrip("> ").strip()
            out.append(f"  \033[36m▌\033[0m \033[3m{inline(quote_text)}\033[0m")
            i += 1
            continue

        # 6. Unordered lists
        list_match = re.match(r"^(\s*)([-*+])\s+(.+)$", line)
        if list_match:
            indent, bullet, content = list_match.groups()
            out.append(f"{indent}  \033[32m•\033[0m {inline(content)}")
            i += 1
            continue

        # 7. Ordered lists
        num_match = re.match(r"^(\s*)(\d+)\.\s+(.+)$", line)
        if num_match:
            indent, num, content = num_match.groups()
            out.append(f"{indent}  \033[33m{num}.\033[0m {inline(content)}")
            i += 1
            continue

        # Normal text line
        out.append(inline(line))
        i += 1

    return "\n".join(out)


# -----------------------------------------------------------------------------
# Tool Definitions & Registry
# -----------------------------------------------------------------------------

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "search_wiki",
            "description": "Search the BookStack wiki across page contents, titles, and tags using full-text search and advanced syntax.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string. Supports plain text keywords, exact phrases ('\"exact match\"'), tag filters ('[tag_name=value]' or '[tag_name]'), type filters ('{type:page}', '{type:book}', '{type:chapter}'), or book filters ('{in_book:slug_or_name}'). Examples: 'Debian', '{type:page} WireGuard', '[os=linux]'",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of results to return (default 10, max 50)",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": "Read the Markdown content and metadata of a BookStack page or multiple pages by ID. Supports paging through large pages using `offset` (character position) or `chunk` (chunk number) to prevent context loss.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "integer",
                        "description": "The numeric ID of a single page to read",
                    },
                    "page_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Optional list of numeric page IDs to read in batch (max 5)",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Starting character offset for reading long pages (default: 0). Use next_offset from previous result to read subsequent chunks.",
                    },
                    "chunk": {
                        "type": "integer",
                        "description": "Optional 1-based chunk number (e.g. 1 for first chunk, 2 for second chunk). Equivalent to offset = (chunk - 1) * limit.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of characters to return in this chunk (default: 4000, max: 10000).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_page",
            "description": "Find a page by its name or title in the wiki.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The exact or partial name of the page",
                    },
                    "book_id": {
                        "type": "integer",
                        "description": "Optional book ID to scope the search",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_books",
            "description": "List all available books in the BookStack instance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of books to list (default 20)",
                        "default": 20,
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_pages_in_book",
            "description": "List all pages contained within a specific book, including page names, tags, and update times. Use this to get an overview of a book's inventory and structure without reading each page individually.",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": {
                        "type": "integer",
                        "description": "The numeric ID of the parent book",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of pages to list (default 50)",
                        "default": 50,
                    },
                },
                "required": ["book_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_or_update_page",
            "description": "Create a new documentation page, or update it if a page with the same title already exists (upsert).",
            "parameters": {
                "type": "object",
                "properties": {
                    "book_id": {
                        "type": "integer",
                        "description": "The ID of the book to place the page in",
                    },
                    "name": {
                        "type": "string",
                        "description": "The title of the page",
                    },
                    "markdown": {
                        "type": "string",
                        "description": "The Markdown content for the page",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags formatted as 'name:value' or 'name'",
                    },
                },
                "required": ["book_id", "name", "markdown"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_wiki_content",
            "description": "Export a BookStack page as Markdown, or a book/shelf as a ZIP archive containing a folder hierarchy of Markdown files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["page", "book", "shelf"],
                        "description": "The type of entity to export ('page', 'book', or 'shelf')",
                    },
                    "entity_id": {
                        "type": "integer",
                        "description": "The numeric ID of the page, book, or shelf to export",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Optional destination directory for the exported file/archive (defaults to './exports')",
                    },
                },
                "required": ["entity_type", "entity_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_plan",
            "description": "Create, update, or track steps in your multi-step execution plan. Use this to record progress, mark steps as in_progress or completed, save discovered IDs/context, and ensure steady, sequential execution without batch timeouts or hangs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "update_step", "add_step", "complete_step", "set_context", "show", "clear"],
                        "description": "The plan action to perform",
                    },
                    "goal": {
                        "type": "string",
                        "description": "The overarching goal description (for 'create')",
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of step descriptions (for 'create')",
                    },
                    "step_id": {
                        "type": "integer",
                        "description": "The numeric ID of the step to update (for 'update_step' or 'complete_step')",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "completed", "failed", "skipped"],
                        "description": "New status for the step",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional notes or discovered details for the step",
                    },
                    "context": {
                        "type": "object",
                        "description": "Key-value dictionary of discovered entities/IDs (e.g. {'server_page_id': 16, 'app_book_id': 3})",
                    },
                },
                "required": ["action"],
            },
        },
    },
]


class WikiToolExecutor:
    """Executes tools against BookStack API and manages goal tracker integration."""

    def __init__(self, client: BookStackClient, goal_tracker: Optional[GoalTracker] = None):
        self.client = client
        self.goal_tracker = goal_tracker

    def set_goal_tracker(self, tracker: GoalTracker):
        self.goal_tracker = tracker

    def execute(self, name: str, args: Dict[str, Any]) -> str:
        try:
            if name == "manage_plan":
                if self.goal_tracker:
                    return self.goal_tracker.handle_manage_plan_tool(args)
                return json.dumps({"status": "error", "message": "Goal tracker not initialized on executor."})

            elif name == "search_wiki":
                query = args.get("query", "").strip()
                count = min(int(args.get("count", 10)), 50)
                res = self.client.request("GET", "search", params={"query": query, "count": count})
                if isinstance(res, dict) and "data" in res:
                    items = []
                    for item in res["data"]:
                        items.append({
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "type": item.get("type"),
                            "url": item.get("url"),
                            "preview": item.get("preview_html", {}).get("content", ""),
                        })
                    if not items:
                        # Auto-fallback: try matching page titles if full-text search returned empty
                        fuzzy_name = re.sub(r"[\[\]\{\}\"\']", "", query).strip()
                        if fuzzy_name:
                            fb_res = self.client.request("GET", "pages", params={"filter[name:like]": f"%{fuzzy_name}%", "count": 5})
                            if isinstance(fb_res, dict) and fb_res.get("data"):
                                fb_items = [
                                    {"id": p.get("id"), "name": p.get("name"), "type": "page", "tags": p.get("tags", [])}
                                    for p in fb_res["data"]
                                ]
                                return json.dumps({
                                    "total": len(fb_items),
                                    "results": fb_items,
                                    "note": f"Exact search for '{query}' returned 0 results, but found matching page titles via fuzzy name match."
                                })

                        return json.dumps({
                            "total": 0,
                            "results": [],
                            "message": f"No results found for query '{query}'. If the item does not exist, inform the user directly instead of searching repeatedly."
                        })
                    return json.dumps({"total": res.get("total", len(items)), "results": items})
                return json.dumps(res)

            elif name == "read_page":
                page_ids = args.get("page_ids")
                if not page_ids and "page_id" in args:
                    page_ids = [int(args["page_id"])]
                elif isinstance(page_ids, list):
                    page_ids = [int(pid) for pid in page_ids[:5]]
                else:
                    return json.dumps({"error": "Missing page_id or page_ids parameter"})

                limit = min(max(int(args.get("limit", 4000)), 500), 10000)
                if "chunk" in args and args["chunk"] is not None:
                    chunk_num = max(int(args["chunk"]), 1)
                    offset = (chunk_num - 1) * limit
                else:
                    offset = max(int(args.get("offset", 0)), 0)

                results = []
                for pid in page_ids:
                    res = self.client.request("GET", f"pages/{pid}")
                    if isinstance(res, dict):
                        raw_md = res.get("markdown") or res.get("html") or ""
                        total_chars = len(raw_md)

                        if offset >= total_chars and total_chars > 0:
                            chunk_slice = ""
                            has_more = False
                            next_offset = None
                            current_chunk = (offset // limit) + 1
                            total_chunks = (total_chars + limit - 1) // limit if total_chars > 0 else 1
                            content = f"[Offset {offset} is beyond the end of the page content ({total_chars} total characters). End of page reached.]"
                        else:
                            end_pos = min(offset + limit, total_chars)
                            chunk_slice = raw_md[offset:end_pos]
                            has_more = end_pos < total_chars
                            next_offset = end_pos if has_more else None
                            current_chunk = (offset // limit) + 1
                            total_chunks = (total_chars + limit - 1) // limit if total_chars > 0 else 1
                            remaining = total_chars - end_pos

                            header = ""
                            if offset > 0:
                                header = f"[... Continued from character offset {offset} (Chunk {current_chunk}/{total_chunks}) ...]\n\n"

                            footer = ""
                            if has_more:
                                footer = f"\n\n... [Content continues: {remaining} additional characters in wiki page (Chunk {current_chunk} of {total_chunks}). Call read_page(page_id={pid}, offset={next_offset}) to read next chunk]"
                            elif offset > 0:
                                footer = f"\n\n[End of page content reached (Total {total_chars} characters)]"

                            content = header + chunk_slice + footer

                        results.append({
                            "id": res.get("id"),
                            "name": res.get("name"),
                            "book_id": res.get("book_id"),
                            "updated_at": res.get("updated_at"),
                            "offset": offset,
                            "limit": limit,
                            "total_chars": total_chars,
                            "returned_chars": len(chunk_slice),
                            "chunk": current_chunk,
                            "total_chunks": total_chunks,
                            "has_more": has_more,
                            "next_offset": next_offset,
                            "markdown": content,
                            "tags": res.get("tags", []),
                        })
                if len(results) == 1:
                    return json.dumps(results[0])
                return json.dumps({"total": len(results), "pages": results})

            elif name == "find_page":
                page_name = args["name"]
                params = {"filter[name:like]": f"%{page_name}%", "count": 10}
                if args.get("book_id"):
                    params["filter[book_id]"] = args["book_id"]
                res = self.client.request("GET", "pages", params=params)
                if isinstance(res, dict) and "data" in res and not res["data"]:
                    return json.dumps({
                        "total": 0,
                        "data": [],
                        "message": f"No pages matching name '{page_name}' were found."
                    })
                return json.dumps(res)

            elif name == "list_books":
                count = int(args.get("count", 20))
                res = self.client.request("GET", "books", params={"count": count})
                if isinstance(res, dict) and "data" in res:
                    books = [
                        {"id": b.get("id"), "name": b.get("name"), "description": b.get("description")}
                        for b in res["data"]
                    ]
                    return json.dumps({"total": res.get("total", len(books)), "books": books})
                return json.dumps(res)

            elif name == "list_pages_in_book":
                book_id = int(args["book_id"])
                count = int(args.get("count", 50))
                res = self.client.request("GET", "pages", params={"filter[book_id]": book_id, "count": count})
                if isinstance(res, dict) and "data" in res:
                    pages = [
                        {
                            "id": p.get("id"),
                            "name": p.get("name"),
                            "slug": p.get("slug", ""),
                            "tags": [t.get("name", "") + (f":{t.get('value')}" if t.get("value") else "") for t in p.get("tags", []) if isinstance(t, dict)],
                            "updated_at": p.get("updated_at"),
                        }
                        for p in res["data"]
                    ]
                    return json.dumps({"total": res.get("total", len(pages)), "pages": pages})
                return json.dumps(res)

            elif name == "create_or_update_page":
                book_id = int(args["book_id"])
                page_name = args["name"]
                markdown_content = args["markdown"]
                raw_tags = args.get("tags", [])
                tags = []
                for t in raw_tags:
                    if ":" in t:
                        k, v = t.split(":", 1)
                        tags.append({"name": k.strip(), "value": v.strip()})
                    else:
                        tags.append({"name": t.strip(), "value": ""})

                # Check if page already exists (exact name or case-insensitive)
                existing = self.client.request("GET", "pages", params={"filter[name]": page_name, "filter[book_id]": book_id})
                if isinstance(existing, dict) and existing.get("data"):
                    page_id = existing["data"][0]["id"]
                    payload = {"name": page_name, "markdown": markdown_content, "changelog": "Updated via Chat Agent"}
                    if tags:
                        payload["tags"] = tags
                    res = self.client.request("PUT", f"pages/{page_id}", json_data=payload)
                    return json.dumps({"action": "updated", "id": page_id, "name": page_name, "status": "success"})
                else:
                    payload = {"book_id": book_id, "name": page_name, "markdown": markdown_content}
                    if tags:
                        payload["tags"] = tags
                    res = self.client.request("POST", "pages", json_data=payload)
                    return json.dumps({"action": "created", "id": res.get("id"), "name": page_name, "status": "success"})

            elif name == "export_wiki_content":
                entity_type = args.get("entity_type") or args.get("type", "page")
                entity_id = int(args.get("entity_id") or args.get("id"))
                output_dir = args.get("output_dir")
                res = export_wiki_entity(self.client, entity_type, entity_id, output_dir)
                return json.dumps(res)

            else:
                return json.dumps({"error": f"Unknown tool: {name}"})

        except Exception as e:
            return json.dumps({"error": str(e)})


# -----------------------------------------------------------------------------
# Agent Loop & LLM Client
# -----------------------------------------------------------------------------

SYSTEM_PROMPT = """You are BookStack Wiki Agent, an autonomous assistant dedicated to exploring, managing, maintaining, and exporting content in the user's BookStack documentation wiki.

You have direct access to tool functions to:
1. Search the wiki for topics, devices, configurations, and procedures (`search_wiki`).
2. Read pages directly (`read_page`, `find_page`). For long pages, `read_page` supports paging via `offset` (or `chunk`) so you can read through entire long pages without context loss.
3. List books and pages (`list_books`, `list_pages_in_book`).
4. Create and update documentation pages (`create_or_update_page`).
5. Export documentation (`export_wiki_content`):
   - `page`: Exports as a Markdown file.
   - `book`: Exports as a ZIP archive containing a folder of pages and chapter subfolders.
   - `shelf`: Exports as a ZIP archive containing folders for each book in the shelf.
6. Manage and track multi-step execution plans (`manage_plan`).

GOAL TRACKING & SEQUENTIAL EXECUTION:
1. When the user requests a multi-step workflow (e.g., reading attached audit files, finding target pages, updating server records, creating application pages, and cross-linking):
   - Check or initialize your checklist using `manage_plan`.
   - Work SEQUENTIALLY: execute ONE action at a time (e.g. read one page, update it, verify, then proceed to the next).
   - DO NOT attempt to batch all mutations or large read calls into a single speculative turn, as this can overload the backend or trigger timeouts.
   - Update step status to `in_progress` or `completed` via `manage_plan` as each sub-task is fulfilled.
2. RE-STEERING & INTERRUPTS: If the user provides a re-steering instruction (e.g. "try one page at a time"), immediately adopt that constraint while maintaining your focus on the active primary goal.

LOCAL FILE ATTACHMENTS & READING:
1. When the user attaches a file and asks you to "read", "review", "check", "inspect", or "summarize" it, DO NOT automatically create, import, or update pages in the wiki unless instructed to do so.
2. If instructed to import or create/update pages based on the file, do so methodically step-by-step.
3. NEVER make unilateral destructive modifications to the wiki.

CRITICAL RULES:
1. AGGREGATE & INVENTORY QUESTIONS: When asked how many items exist, for overviews, or for device counts, use `list_pages_in_book` or `search_wiki`. Categorize directly from page titles, slugs, and tags. NEVER attempt to read every single page in a book.
2. TARGETED LOOKUPS & PAGING: Read specific pages when deep technical details (IP addresses, MAC addresses, directory layouts, docker-compose files, configurations) are requested. If a page response indicates `has_more: true`, page through the rest of the document with `read_page(page_id=..., offset=...)` using `next_offset` instead of searching repeatedly.
3. EFFICIENCY: Once you have gathered sufficient page content or reached the relevant section, synthesize your conclusions immediately and formulate your final response without unnecessary tool chaining.
4. MISSING ITEMS & TYPOS: If a search returns 0 results, check for common typos or abbreviations (e.g., 'pve' for Proxmox). If still not found, state clearly that the item is not documented in the wiki.
5. FORMATTING: Format output with clean Markdown tables, bullet points, and code blocks.
"""


class SessionLogger:
    """Logs conversation messages, thinking intervals, tool calls, and results to files."""

    def __init__(self, log_dir: str = "logs", session_id: Optional[str] = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.log_file = self.log_dir / f"chat_{self.session_id}.log"
        self.jsonl_file = self.log_dir / f"chat_{self.session_id}.jsonl"

    def log(self, event_type: str, message: str, metadata: Optional[Dict[str, Any]] = None):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] [{event_type}] {message}\n"
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line)

        record = {
            "timestamp": timestamp,
            "session_id": self.session_id,
            "type": event_type,
            "message": message,
            "metadata": metadata or {},
        }
        with open(self.jsonl_file, "a", encoding="utf-8") as jf:
            jf.write(json.dumps(record) + "\n")


class RollingThoughtStreamer:
    """Streams model thinking in real-time within a rolling 4-line terminal viewport."""

    def __init__(self, message: str = "Thinking", max_scroll: int = 4, logger: Optional[SessionLogger] = None):
        self.message = message
        self.max_scroll = max_scroll
        self.logger = logger
        self.start_time = 0.0
        self.elapsed = 0.0
        self.token_count = 0
        self.full_thought = ""
        self.rendered_lines = 1 + self.max_scroll
        self._spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._spinner_idx = 0
        self._stop_ticker = threading.Event()
        self._ticker_thread: Optional[threading.Thread] = None

    def start(self):
        self.start_time = time.time()
        if self.logger:
            self.logger.log("THINKING", f"{self.message} started")
        now_str = datetime.now().strftime("%H:%M:%S")
        sys.stdout.write(f"  \033[90m[{now_str}]\033[0m \033[33m💭 {self.message}...\033[0m \033[90m(0.0s)\033[0m\n")
        for _ in range(self.max_scroll):
            sys.stdout.write("  \033[90m│\033[0m\n")
        sys.stdout.flush()

        self._stop_ticker.clear()
        self._ticker_thread = threading.Thread(target=self._ticker_loop, daemon=True)
        self._ticker_thread.start()

    def _get_term_width(self) -> int:
        try:
            cols = shutil.get_terminal_size((80, 24)).columns
            return max(30, min(cols - 8, 85))
        except Exception:
            return 72

    def _ticker_loop(self):
        while not self._stop_ticker.is_set():
            time.sleep(0.1)
            if not self.full_thought and not self._stop_ticker.is_set():
                self._spinner_idx += 1
                sp = self._spinner_chars[self._spinner_idx % len(self._spinner_chars)]
                elapsed = time.time() - self.start_time
                now_str = datetime.now().strftime("%H:%M:%S")
                sys.stdout.write(f"\033[{self.rendered_lines}A\r")
                sys.stdout.write(f"\033[K  \033[90m[{now_str}]\033[0m \033[33m{sp} {self.message}...\033[0m \033[90m({elapsed:.1f}s)\033[0m\n")
                for _ in range(self.max_scroll):
                    sys.stdout.write("  \033[90m│\033[0m\n")
                sys.stdout.flush()

    def push(self, token: str):
        if not token:
            return
        self.token_count += 1
        self.full_thought += token
        elapsed = time.time() - self.start_time
        now_str = datetime.now().strftime("%H:%M:%S")
        width = self._get_term_width()

        raw_lines = self.full_thought.split("\n")
        wrapped_lines: List[str] = []
        for rl in raw_lines:
            if not rl:
                wrapped_lines.append("")
            else:
                wrapped_lines.extend(textwrap.wrap(rl, width=width) or [""])

        display_lines = wrapped_lines[-self.max_scroll:] if len(wrapped_lines) >= self.max_scroll else wrapped_lines
        while len(display_lines) < self.max_scroll:
            display_lines.insert(0, "")

        sys.stdout.write(f"\033[{self.rendered_lines}A\r")
        sys.stdout.write(f"\033[K  \033[90m[{now_str}]\033[0m \033[33m💭 {self.message}...\033[0m \033[90m({elapsed:.1f}s)\033[0m\n")
        for line in display_lines:
            line_str = f" \033[37;2m{line}\033[0m" if line else ""
            sys.stdout.write(f"\033[K  \033[90m│\033[0m{line_str}\n")
        sys.stdout.flush()

    def finish(self, cancelled: bool = False):
        if self._ticker_thread and self._ticker_thread.is_alive():
            self._stop_ticker.set()
            self._ticker_thread.join()

        self.elapsed = time.time() - self.start_time
        now_str = datetime.now().strftime("%H:%M:%S")
        tps_str = f", {self.token_count / self.elapsed:.1f} t/s" if self.elapsed > 0 and self.token_count > 0 else ""

        tag = "\033[33m⏹ Thought interrupted\033[0m" if cancelled else f"\033[36m💡 Thought for {self.elapsed:.1f}s{tps_str}\033[0m"
        sys.stdout.write(f"\033[{self.rendered_lines}A\r")
        sys.stdout.write(f"\033[K  \033[90m[{now_str}]\033[0m {tag}\n")
        for _ in range(self.max_scroll):
            sys.stdout.write("\033[K\033[1B")
        sys.stdout.write(f"\033[{self.max_scroll}A\r")
        sys.stdout.flush()

        if self.logger:
            status = "CANCELLED" if cancelled else "THOUGHT"
            self.logger.log(status, f"{self.message} ({self.elapsed:.2f}s):\n{self.full_thought}", {"elapsed": self.elapsed, "thought": self.full_thought, "tokens": self.token_count, "cancelled": cancelled})


def extract_embedded_tool_calls(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Fallback parser for when local LLMs eject tool calls as raw XML/JSON text
    in the `content` field instead of standard OpenAI `tool_calls` structures.
    """
    if not text or ("<tool_call>" not in text and "<function_call>" not in text and "<function=" not in text):
        return text, []

    tool_calls = []
    cleaned_text = text

    # 1. XML parameter style: <tool_call><function=NAME><parameter=KEY>VAL</parameter>...</function></tool_call>
    p1 = re.compile(r"<tool_call>\s*<function=([a-zA-Z0-9_\-\.]+)>([\s\S]*?)</function>\s*</tool_call>", re.IGNORECASE)
    for m in p1.finditer(text):
        fn_name = m.group(1).strip()
        body = m.group(2)
        params = {}
        param_pattern = re.compile(r"<parameter=([a-zA-Z0-9_\-\.]+)>([\s\S]*?)</parameter>", re.IGNORECASE)
        for pm in param_pattern.finditer(body):
            pname = pm.group(1).strip()
            pval = pm.group(2).strip()
            if pval.isdigit():
                pval = int(pval)
            elif pval.lower() == "true":
                pval = True
            elif pval.lower() == "false":
                pval = False
            else:
                try:
                    pval = json.loads(pval)
                except Exception:
                    pass
            params[pname] = pval
        tool_calls.append({
            "id": f"call_text_{int(time.time()*1000)}_{len(tool_calls)+1}",
            "type": "function",
            "function": {"name": fn_name, "arguments": json.dumps(params)}
        })
    cleaned_text = p1.sub("", cleaned_text)

    # 2. JSON in <tool_call>...</tool_call> or <function_call>...</function_call>
    p2 = re.compile(r"<(?:tool_call|function_call)>\s*(\{[\s\S]*?\})\s*</(?:tool_call|function_call)>", re.IGNORECASE)
    for m in p2.finditer(cleaned_text):
        raw_json = m.group(1).strip()
        try:
            data = json.loads(raw_json)
            if isinstance(data, dict):
                fn_name = data.get("name") or data.get("function") or data.get("tool")
                raw_args = data.get("arguments") or data.get("parameters") or data.get("args") or {}
                if isinstance(raw_args, dict):
                    args_str = json.dumps(raw_args)
                else:
                    args_str = str(raw_args)
                if fn_name:
                    tool_calls.append({
                        "id": f"call_text_{int(time.time()*1000)}_{len(tool_calls)+1}",
                        "type": "function",
                        "function": {"name": fn_name, "arguments": args_str}
                    })
        except Exception:
            pass
    return cleaned_text.strip(), tool_calls


class RepetitionDetector:
    """Detects degenerate token loops (repeated identical lines or cyclic n-grams) during streaming."""

    def __init__(self, max_line_repeats: int = 4, max_ngram_repeats: int = 6, min_ngram_len: int = 15):
        self.max_line_repeats = max_line_repeats
        self.max_ngram_repeats = max_ngram_repeats
        self.min_ngram_len = min_ngram_len
        self.buffer = ""
        self.recent_lines: List[str] = []

    def push(self, token: str) -> bool:
        """Pushes token into detector. Returns True if a degenerate loop is detected."""
        if not token:
            return False
        self.buffer += token

        # 1. Line-level repetition check
        if "\n" in token:
            lines = self.buffer.split("\n")
            self.buffer = lines[-1]
            for l in lines[:-1]:
                cleaned = l.strip()
                if len(cleaned) >= 3:
                    if self.recent_lines and cleaned == self.recent_lines[-1]:
                        self.recent_lines.append(cleaned)
                        if len(self.recent_lines) >= self.max_line_repeats:
                            return True
                    else:
                        self.recent_lines = [cleaned]

        # 2. Substring / N-gram cyclic pattern check in tail of stream
        tail = self.buffer[-300:] if len(self.buffer) > 300 else self.buffer
        if len(tail) >= self.min_ngram_len * self.max_ngram_repeats:
            for pat_len in range(self.min_ngram_len, 60):
                pat = tail[-pat_len:]
                matches = 0
                idx = len(tail)
                while idx >= pat_len and tail[idx - pat_len:idx] == pat:
                    matches += 1
                    idx -= pat_len
                if matches >= self.max_ngram_repeats:
                    return True

        return False


class ChatAgent:
    """Agent loop managing conversation history, goal tracker, tool execution, and loop prevention."""

    def __init__(
        self,
        executor: WikiToolExecutor,
        llm_url: Optional[str] = None,
        model: Optional[str] = None,
        max_turns: int = 20,
        logger: Optional[SessionLogger] = None,
        auto_approve: bool = False,
    ):
        load_dotenv()
        self.logger = logger or SessionLogger()
        self.session_id = self.logger.session_id
        self.goal_tracker = GoalTracker(logger=self.logger)
        self.executor = executor
        self.executor.set_goal_tracker(self.goal_tracker)

        self.llm_url = (llm_url or os.getenv("LMSTUDIO_URL") or "http://192.168.1.9:1234/v1").rstrip("/")
        self.max_turns = max_turns
        self.auto_approve = auto_approve
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

        # Determine model:
        # 1. Explicit model passed via CLI --model / parameter
        # 2. Check currently loaded model in memory on endpoint
        # 3. Fallback to LMSTUDIO_MODEL env var or available models
        if model:
            self.model = model
            self.model_status = "explicit"
        else:
            loaded_model = self.detect_loaded_model()
            if loaded_model:
                self.model = loaded_model
                self.model_status = "currently loaded"
            else:
                env_model = os.getenv("LMSTUDIO_MODEL")
                if env_model:
                    self.model = env_model
                    self.model_status = "configured default"
                else:
                    available = self.list_available_models()
                    self.model = available[0] if available else "ornith-1.5-9b"
                    self.model_status = "fallback default"

        self.logger.log(
            "SYSTEM",
            f"Agent initialized with session_id={self.session_id}, model={self.model} ({self.model_status}), endpoint={self.llm_url}, max_turns={self.max_turns}"
        )

    def _cleanup_interrupted_history(self):
        """Clean up message history after an interrupt to preserve completed tool pairs while removing dangling incomplete calls."""
        while self.messages:
            last = self.messages[-1]
            # If the last message is an assistant message with tool calls that never received tool responses, remove it
            if last.get("role") == "assistant" and last.get("tool_calls"):
                self.messages.pop()
            # If the last message is an empty assistant message, remove it
            elif last.get("role") == "assistant" and not last.get("content") and not last.get("tool_calls"):
                self.messages.pop()
            else:
                break

    def _prune_history(self, max_tool_chars: int = 350):
        """Compact verbose tool outputs from prior conversation turns to preserve context window."""
        if len(self.messages) <= 4:
            return
        # Prune older messages, leaving system prompt and the latest interaction untouched
        for msg in self.messages[:-4]:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > max_tool_chars:
                    try:
                        data = json.loads(content)
                        if isinstance(data, dict):
                            summary_dict = {}
                            for k in ("id", "name", "action", "status", "total", "note", "message", "error", "offset", "limit", "chunk", "total_chunks", "has_more", "total_chars"):
                                if k in data:
                                    summary_dict[k] = data[k]
                            if "pages" in data:
                                summary_dict["page_count"] = len(data["pages"])
                                summary_dict["page_names"] = [p.get("name") for p in data["pages"] if isinstance(p, dict)][:10]
                            if "results" in data:
                                summary_dict["result_count"] = len(data["results"])
                                summary_dict["results"] = [r.get("name") for r in data["results"] if isinstance(r, dict)][:10]
                            msg["content"] = json.dumps(summary_dict)
                        else:
                            msg["content"] = content[:max_tool_chars] + "... [Compacted for context efficiency]"
                    except Exception:
                        msg["content"] = content[:max_tool_chars] + "... [Compacted for context efficiency]"

    def detect_loaded_model(self) -> Optional[str]:
        """Check what model is currently loaded in memory on the LLM endpoint."""
        base_url = re.sub(r"/v1/?$", "", self.llm_url).rstrip("/")

        # 1. LM Studio REST API (/api/v0/models)
        try:
            resp = requests.get(f"{base_url}/api/v0/models", timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and "data" in data:
                    # Prefer loaded LLM/VLM text models over embeddings/whisper
                    loaded_chat = [
                        m["id"]
                        for m in data["data"]
                        if isinstance(m, dict)
                        and m.get("state") == "loaded"
                        and m.get("type") in ("llm", "vlm")
                    ]
                    if loaded_chat:
                        return loaded_chat[0]

                    # Any loaded model
                    loaded_any = [
                        m["id"]
                        for m in data["data"]
                        if isinstance(m, dict) and m.get("state") == "loaded"
                    ]
                    if loaded_any:
                        return loaded_any[0]
        except Exception:
            pass

        # 2. Ollama /api/ps
        try:
            resp = requests.get(f"{base_url}/api/ps", timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and "models" in data and data["models"]:
                    first_model = data["models"][0]
                    return first_model.get("name") or first_model.get("model")
        except Exception:
            pass

        return None

    def get_model_catalog(self) -> List[Dict[str, Any]]:
        """Retrieve detailed model catalog including loaded status, architecture, and type."""
        base_url = re.sub(r"/v1/?$", "", self.llm_url).rstrip("/")
        catalog: List[Dict[str, Any]] = []
        seen_ids = set()

        # 1. LM Studio REST API (/api/v0/models)
        try:
            resp = requests.get(f"{base_url}/api/v0/models", timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and "data" in data:
                    for m in data["data"]:
                        if isinstance(m, dict) and "id" in m:
                            mid = m["id"]
                            seen_ids.add(mid)
                            catalog.append({
                                "id": mid,
                                "loaded": m.get("state") == "loaded",
                                "type": m.get("type", "llm"),
                                "quant": m.get("quantization", ""),
                                "arch": m.get("arch", ""),
                            })
                    return catalog
        except Exception:
            pass

        # 2. Ollama /api/ps and /api/tags
        loaded_names = set()
        try:
            ps_resp = requests.get(f"{base_url}/api/ps", timeout=4)
            if ps_resp.status_code == 200:
                ps_data = ps_resp.json()
                for m in ps_data.get("models", []):
                    name = m.get("name") or m.get("model")
                    if name:
                        loaded_names.add(name)
        except Exception:
            pass

        try:
            tags_resp = requests.get(f"{base_url}/api/tags", timeout=4)
            if tags_resp.status_code == 200:
                tags_data = tags_resp.json()
                for m in tags_data.get("models", []):
                    name = m.get("name") or m.get("model")
                    if name and name not in seen_ids:
                        seen_ids.add(name)
                        catalog.append({
                            "id": name,
                            "loaded": name in loaded_names,
                            "type": "llm",
                            "quant": "",
                            "arch": "",
                        })
                if catalog:
                    return catalog
        except Exception:
            pass

        # 3. Standard OpenAI /v1/models fallback
        try:
            resp = requests.get(f"{self.llm_url}/models", timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                raw_models = data.get("data", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                for m in raw_models:
                    mid = m.get("id") if isinstance(m, dict) else str(m)
                    if mid and mid not in seen_ids:
                        seen_ids.add(mid)
                        catalog.append({
                            "id": mid,
                            "loaded": False,
                            "type": "llm",
                            "quant": "",
                            "arch": "",
                        })
        except Exception:
            pass

        return catalog

    def list_available_models(self) -> List[str]:
        """Fetch list of available model IDs from the LLM endpoint."""
        catalog = self.get_model_catalog()
        if catalog:
            return [m["id"] for m in catalog]
        return []

    def set_model(self, new_model: str):
        """Switch current active model."""
        old_model = self.model
        self.model = new_model
        self.model_status = "user switched"
        self.logger.log("SYSTEM", f"Model switched from {old_model} to {new_model}")

    def chat(self, user_input: str, attachments: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
        """Process user input through the tool loop until a final answer is produced."""
        self.logger.log("USER", user_input)
        start_msg_count = len(self.messages)

        # Harness-Side Goal Tracking & Resteer Processing:
        effective_prompt = user_input
        if self.goal_tracker.is_interrupted() or (self.goal_tracker.is_active() and len(user_input.split()) <= 10 and not user_input.startswith("@")):
            # Resteering turn: preserve goal context
            effective_prompt = self.goal_tracker.get_resteer_prompt(user_input)
        else:
            # Auto-detect multi-step instructions from new user prompt
            self.goal_tracker.auto_init_from_prompt(user_input, attachments=attachments)

        # If goal plan is active, display the visual panel in the terminal
        if self.goal_tracker.is_active():
            print(f"\n{self.goal_tracker.render_panel()}\n")

        self.messages.append({"role": "user", "content": effective_prompt})

        # Track tool calls made in this conversational turn to prevent infinite loops
        seen_calls: Dict[str, int] = {}
        consecutive_empty_searches = 0
        read_page_count = 0
        turn = 0
        streamer: Optional[RollingThoughtStreamer] = None

        try:
            while turn < self.max_turns:
                turn += 1

                payload = {
                    "model": self.model,
                    "messages": self.messages,
                    "tools": TOOLS_SPEC,
                    "tool_choice": "auto",
                    "temperature": 0.2,
                    "frequency_penalty": float(os.getenv("LLM_FREQUENCY_PENALTY", "0.3")),
                    "presence_penalty": float(os.getenv("LLM_PRESENCE_PENALTY", "0.1")),
                    "stream": True,
                }

                streamer = RollingThoughtStreamer(f"Thinking (turn {turn}/{self.max_turns})", logger=self.logger)
                streamer.start()

                content_pieces: List[str] = []
                reasoning_pieces: List[str] = []
                tool_calls_dict: Dict[int, Dict[str, Any]] = {}
                in_think_tag = False
                loop_detector = RepetitionDetector()
                loop_aborted = False

                try:
                    resp = requests.post(
                        f"{self.llm_url}/chat/completions",
                        json=payload,
                        stream=True,
                        timeout=120,
                    )
                    resp.raise_for_status()

                    for line in resp.iter_lines():
                        if not line:
                            continue
                        decoded = line.decode("utf-8")
                        if decoded.startswith("data: "):
                            raw_json = decoded[6:].strip()
                            if raw_json == "[DONE]":
                                break
                            try:
                                chunk = json.loads(raw_json)
                            except Exception:
                                continue

                            choice = chunk.get("choices", [{}])[0]
                            delta = choice.get("delta", {})

                            # 1. Reasoning / Thinking tokens
                            rc = delta.get("reasoning_content") or delta.get("reasoning")
                            if rc:
                                reasoning_pieces.append(rc)
                                streamer.push(rc)
                                if loop_detector.push(rc):
                                    loop_aborted = True
                                    break

                            # 2. Content tokens (and <think> tag handling)
                            c = delta.get("content")
                            if c:
                                if "<think>" in c:
                                    in_think_tag = True
                                    parts = c.split("<think>", 1)
                                    if parts[0]:
                                        content_pieces.append(parts[0])
                                    if len(parts) > 1 and parts[1]:
                                        if "</think>" in parts[1]:
                                            think_p, after_p = parts[1].split("</think>", 1)
                                            reasoning_pieces.append(think_p)
                                            streamer.push(think_p)
                                            if loop_detector.push(think_p):
                                                loop_aborted = True
                                                break
                                            in_think_tag = False
                                            if after_p:
                                                content_pieces.append(after_p)
                                        else:
                                            reasoning_pieces.append(parts[1])
                                            streamer.push(parts[1])
                                            if loop_detector.push(parts[1]):
                                                loop_aborted = True
                                                break
                                elif in_think_tag:
                                    if "</think>" in c:
                                        think_p, after_p = c.split("</think>", 1)
                                        reasoning_pieces.append(think_p)
                                        streamer.push(think_p)
                                        if loop_detector.push(think_p):
                                            loop_aborted = True
                                            break
                                        in_think_tag = False
                                        if after_p:
                                            content_pieces.append(after_p)
                                    else:
                                        reasoning_pieces.append(c)
                                        streamer.push(c)
                                        if loop_detector.push(c):
                                            loop_aborted = True
                                            break
                                else:
                                    content_pieces.append(c)
                                    if loop_detector.push(c):
                                        loop_aborted = True
                                        break

                            # 3. Tool call chunks
                            tcs = delta.get("tool_calls", [])
                            for tc in tcs:
                                idx = tc.get("index", 0)
                                if idx not in tool_calls_dict:
                                    tool_calls_dict[idx] = {
                                        "id": tc.get("id") or f"call_{int(time.time()*1000)}_{idx}",
                                        "name": tc.get("function", {}).get("name", ""),
                                        "arguments": tc.get("function", {}).get("arguments", ""),
                                    }
                                else:
                                    if tc.get("id"):
                                        tool_calls_dict[idx]["id"] = tc["id"]
                                    if tc.get("function", {}).get("name"):
                                        tool_calls_dict[idx]["name"] += tc["function"]["name"]
                                    if tc.get("function", {}).get("arguments"):
                                        tool_calls_dict[idx]["arguments"] += tc["function"]["arguments"]

                    if loop_aborted:
                        now_str = datetime.now().strftime("%H:%M:%S")
                        print(f"\n  \033[90m[{now_str}]\033[0m \033[33m⚠ Degenerate repetition loop detected in model output (auto-aborted stream).\033[0m")
                        self.logger.log("WARN", "Degenerate repetition loop detected in stream; auto-aborted.")

                except Exception as e:
                    streamer.finish()
                    streamer = None
                    err_msg = f"❌ LLM API Error ({self.llm_url}): {e}"
                    self.logger.log("ERROR", err_msg)
                    self.messages = self.messages[:start_msg_count]
                    return err_msg
                finally:
                    if streamer:
                        streamer.finish()
                        streamer = None

                full_content = "".join(content_pieces)
                tool_calls = []
                for idx in sorted(tool_calls_dict.keys()):
                    item = tool_calls_dict[idx]
                    tool_calls.append({
                        "id": item["id"],
                        "type": "function",
                        "function": {"name": item["name"], "arguments": item["arguments"]},
                    })

                # Fallback: Extract raw XML / JSON tool calls ejected directly into content
                if not tool_calls and full_content:
                    cleaned_content, fallback_calls = extract_embedded_tool_calls(full_content)
                    if fallback_calls:
                        tool_calls = fallback_calls
                        full_content = cleaned_content

                assistant_msg: Dict[str, Any] = {"role": "assistant"}
                if full_content:
                    assistant_msg["content"] = full_content
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                if reasoning_pieces:
                    assistant_msg["reasoning_content"] = "".join(reasoning_pieces)
                self.messages.append(assistant_msg)

                if not tool_calls:
                    self.logger.log("AGENT", full_content)
                    self._prune_history()
                    return full_content

                # Execute tool calls with loop suppression
                for tc in tool_calls:
                    func_name = tc.get("function", {}).get("name", "")
                    call_id = tc.get("id", f"call_{int(time.time()*1000)}")
                    raw_args = tc.get("function", {}).get("arguments", "{}")
                    try:
                        args_dict = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    except Exception:
                        args_dict = {}

                    raw_summary = json.dumps(args_dict)
                    args_summary = raw_summary if len(raw_summary) <= 60 else raw_summary[:57] + "..."

                    # Create signature for duplicate detection
                    call_sig = f"{func_name}:{json.dumps(args_dict, sort_keys=True)}"
                    seen_calls[call_sig] = seen_calls.get(call_sig, 0) + 1
                    self.logger.log("TOOL_CALL", f"{func_name}({json.dumps(args_dict)})", {"tool": func_name, "args": args_dict})
                    now_str = datetime.now().strftime("%H:%M:%S")
                    t0 = time.time()

                    # Watchdog: If exact same tool call executed > 1 time, suppress repeated execution
                    if seen_calls[call_sig] > 1:
                        elapsed = time.time() - t0
                        end_str = datetime.now().strftime("%H:%M:%S")
                        print(f"  \033[90m[{end_str}]\033[0m \033[33m⚠ Skipped (Duplicate):\033[0m {func_name}({args_summary}) \033[90m({elapsed:.2f}s)\033[0m")
                        if func_name == "read_page":
                            tool_result = json.dumps({
                                "error": "Duplicate tool call suppressed. You have already received this chunk. If the page has more content (has_more: true), call read_page with 'offset' set to next_offset (e.g. offset=4000) to read the next chunk, or synthesize your final response."
                            })
                        else:
                            tool_result = json.dumps({
                                "error": "Duplicate tool call suppressed. You have already received the result for this query. Please synthesize your final response for the user."
                            })
                    elif func_name == "search_wiki" and consecutive_empty_searches >= 2:
                        elapsed = time.time() - t0
                        end_str = datetime.now().strftime("%H:%M:%S")
                        print(f"  \033[90m[{end_str}]\033[0m \033[33m⚠ Capped (No Results Found):\033[0m {func_name}({args_summary}) \033[90m({elapsed:.2f}s)\033[0m")
                        tool_result = json.dumps({
                            "message": "Multiple searches returned 0 results. The requested item does not exist in BookStack. Please formulate your final response to inform the user."
                        })
                    elif func_name == "read_page" and read_page_count >= 12:
                        elapsed = time.time() - t0
                        end_str = datetime.now().strftime("%H:%M:%S")
                        print(f"  \033[90m[{end_str}]\033[0m \033[33m⚠ Capped (Inspection Limit):\033[0m {func_name}({args_summary}) \033[90m({elapsed:.2f}s)\033[0m")
                        tool_result = json.dumps({
                            "message": "Inspection limit reached. You have sufficient page context. You MUST now synthesize your final summary and answer the user directly without making any more tool calls."
                        })
                    else:
                        # Safety Guardrail: Prompt user before executing mutating wiki actions
                        if func_name == "create_or_update_page" and not self.auto_approve:
                            book_id = args_dict.get("book_id", "?")
                            page_name = args_dict.get("name", "Untitled")
                            content_len = len(args_dict.get("markdown", ""))
                            print(f"\n  \033[90m[{now_str}]\033[0m \033[1;33m🛡️  Action Approval Required:\033[0m \033[1;37mcreate_or_update_page\033[0m")
                            print(f"      • Target Book ID : \033[36m#{book_id}\033[0m")
                            print(f"      • Page Title     : \033[1;32m{page_name}\033[0m")
                            print(f"      • Content Size   : \033[90m{content_len:,} characters\033[0m")

                            approval_prompt_ansi = (
                                "\033[1;33m    Approve modification?\033[0m "
                                "[\033[1;32my\033[0m(yes)/"
                                "\033[1;31mn\033[0m(no)/"
                                "\033[1;36ma\033[0m(auto-all)]: "
                            )
                            flush_stdin()
                            ans = ""
                            while True:
                                try:
                                    if pt_prompt:
                                        ans = pt_prompt(ANSI(approval_prompt_ansi)).strip().lower()
                                    else:
                                        ans = input(approval_prompt_ansi).strip().lower()
                                except (KeyboardInterrupt, EOFError):
                                    ans = "n"
                                    print()
                                    break

                                if ans in ("y", "yes", "n", "no", "a", "auto", "auto-all"):
                                    break

                                print("    \033[33m⚠ Unrecognized response. Please enter 'y' (yes), 'n' (no), or 'a' (auto-approve all).\033[0m")

                            if ans in ("a", "auto", "auto-all"):
                                self.auto_approve = True
                                print("    \033[1;32m✓ Auto-approval enabled for remaining actions in this session.\033[0m\n")
                            elif ans in ("n", "no"):
                                elapsed = time.time() - t0
                                end_str = datetime.now().strftime("%H:%M:%S")
                                print(f"    \033[90m[{end_str}]\033[0m \033[31m✗ Denied by user:\033[0m create_or_update_page('{page_name}') \033[90m({elapsed:.2f}s)\033[0m\n")
                                tool_result = json.dumps({
                                    "status": "denied",
                                    "message": f"Modification denied by user for page '{page_name}' in Book #{book_id}. Do NOT create or modify this page. Ask the user what they would like to do with the content instead."
                                })
                                self.logger.log("TOOL_DENIED", f"User denied create_or_update_page for '{page_name}'", {"tool": func_name, "args": args_dict})
                                self.messages.append({
                                    "role": "tool",
                                    "tool_call_id": call_id,
                                    "name": func_name,
                                    "content": tool_result,
                                })
                                continue

                            # If approved, show executing state
                            exec_str = datetime.now().strftime("%H:%M:%S")
                            print(f"  \033[90m[{exec_str}]\033[0m \033[34m⚙ Executing:\033[0m create_or_update_page('{page_name}') ... ", end="", flush=True)
                        else:
                            print(f"  \033[90m[{now_str}]\033[0m \033[34m⚙ Calling:\033[0m {func_name}({args_summary}) ... ", end="", flush=True)

                        if func_name == "read_page":
                            read_page_count += 1

                        tool_result = self.executor.execute(func_name, args_dict)
                        elapsed = time.time() - t0
                        end_str = datetime.now().strftime("%H:%M:%S")
                        call_label = f"create_or_update_page('{args_dict.get('name', 'Untitled')}')" if func_name == "create_or_update_page" else f"{func_name}({args_summary})"
                        print(f"\r\033[K  \033[90m[{end_str}]\033[0m \033[32m✓ Done:\033[0m {call_label} \033[90m({elapsed:.2f}s)\033[0m")

                        # Track consecutive empty searches
                        if func_name == "search_wiki" and '"total": 0' in tool_result:
                            consecutive_empty_searches += 1
                        elif func_name == "search_wiki":
                            consecutive_empty_searches = 0

                    self.logger.log(
                        "TOOL_RESULT",
                        f"{func_name} ({elapsed:.2f}s) -> {tool_result[:300]}",
                        {"tool": func_name, "elapsed": elapsed, "result": tool_result},
                    )

                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": func_name,
                        "content": tool_result,
                    })

            # Fallback Synthesis: If turns exhausted, force one final prompt asking the model to synthesize all gathered data
            self.messages.append({
                "role": "user",
                "content": "Please synthesize and summarize your final conclusion and answer based on all the information and pages gathered above."
            })
            streamer = RollingThoughtStreamer("Synthesizing final summary", logger=self.logger)
            streamer.start()
            synth_pieces: List[str] = []
            synth_detector = RepetitionDetector()
            synth_aborted = False
            try:
                resp = requests.post(
                    f"{self.llm_url}/chat/completions",
                    json={
                        "model": self.model,
                        "messages": self.messages,
                        "temperature": 0.3,
                        "frequency_penalty": float(os.getenv("LLM_FREQUENCY_PENALTY", "0.3")),
                        "presence_penalty": float(os.getenv("LLM_PRESENCE_PENALTY", "0.1")),
                        "stream": True,
                    },
                    stream=True,
                    timeout=120,
                )
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    decoded = line.decode("utf-8")
                    if decoded.startswith("data: "):
                        raw_json = decoded[6:].strip()
                        if raw_json == "[DONE]":
                            break
                        try:
                            chunk = json.loads(raw_json)
                        except Exception:
                            continue
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        rc = delta.get("reasoning_content") or delta.get("reasoning")
                        if rc:
                            streamer.push(rc)
                            if synth_detector.push(rc):
                                synth_aborted = True
                                break
                        c = delta.get("content")
                        if c:
                            synth_pieces.append(c)
                            if synth_detector.push(c):
                                synth_aborted = True
                                break

                if synth_aborted:
                    now_str = datetime.now().strftime("%H:%M:%S")
                    print(f"\n  \033[90m[{now_str}]\033[0m \033[33m⚠ Degenerate repetition loop detected in synthesis output (auto-aborted).\033[0m")
                    self.logger.log("WARN", "Degenerate repetition loop detected in synthesis stream; auto-aborted.")

            except Exception as e:
                streamer.finish()
                streamer = None
                err_msg = f"Completed tool inspection. Summary synthesis error: {e}"
                self.logger.log("AGENT", err_msg)
                return err_msg
            finally:
                if streamer:
                    streamer.finish()
                    streamer = None

            summary_content = "".join(synth_pieces) or "Finished processing."
            self.logger.log("AGENT", summary_content)
            self._prune_history()
            return summary_content

        except KeyboardInterrupt:
            if streamer:
                streamer.finish(cancelled=True)
            # Mark interrupted in goal tracker so the goal state, primary objective, and attachments are preserved
            self.goal_tracker.mark_interrupted()
            self._cleanup_interrupted_history()
            now_str = datetime.now().strftime("%H:%M:%S")
            print(f"\n  \033[90m[{now_str}]\033[0m \033[1;33m⏹ Generation stopped by user.\033[0m Task context and gathered data preserved. You can enter a new prompt to resteer (e.g. \033[36m'try one page at a time'\033[0m).\n")
            self.logger.log("USER_INTERRUPT", "Generation stopped by user (Ctrl+C)")
            return None


# -----------------------------------------------------------------------------
# CLI Entrypoint
# -----------------------------------------------------------------------------

def print_session_exit_banner(agent: ChatAgent):
    """Prints a summary banner displaying session ID and log locations on exit."""
    agent.logger.log("SESSION_END", f"Session {agent.session_id} ended")
    print("\n" + "─" * 60)
    print(f" 📋 \033[1;36mSession Summary\033[0m")
    print(f"    • Session ID : \033[33m{agent.session_id}\033[0m")
    print(f"    • Log File   : \033[32m{agent.logger.log_file}\033[0m")
    print(f"    • JSONL Trace: \033[32m{agent.logger.jsonl_file}\033[0m")
    print("─" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="BookStack Interactive AI Chat Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ./chat.py
  ./chat.py "@notes.md import this into book 3 as Release Notes"
  ./chat.py "Export the Servers book as a zip"
  ./chat.py --model ornith-1.5-9b "List all servers running Debian in our wiki"
""",
    )
    parser.add_argument("prompt", nargs="?", help="Optional prompt to run non-interactively")
    parser.add_argument("--model", help="LLM model identifier (overrides LMSTUDIO_MODEL env)")
    parser.add_argument("--url", help="OpenAI-compatible LLM endpoint URL (overrides LMSTUDIO_URL env)")
    parser.add_argument("--max-turns", type=int, default=20, help="Maximum tool interaction turns per query (default: 20)")
    parser.add_argument("--yes", "-y", action="store_true", help="Auto-approve all mutating actions (page creation/updates) without interactive prompts")

    args = parser.parse_args()

    # Enable tab-completion for @file mentions
    setup_file_completer()

    try:
        wiki_client = BookStackClient()
        executor = WikiToolExecutor(wiki_client)
        agent = ChatAgent(executor, llm_url=args.url, model=args.model, max_turns=args.max_turns, auto_approve=args.yes)
    except Exception as e:
        print(f"Initialization Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.prompt:
        # One-shot CLI mode with file attachment resolution
        try:
            augmented_prompt, attachments = resolve_file_mentions(args.prompt)
            if attachments:
                now_str = datetime.now().strftime("%H:%M:%S")
                for att in attachments:
                    if "content" in att:
                        print(f"  \033[90m[{now_str}]\033[0m \033[36m📎 Attached file:\033[0m \033[33m{att['rel_path']}\033[0m \033[90m({att['size']} bytes)\033[0m")
            answer = agent.chat(augmented_prompt, attachments=attachments)
            if answer:
                print(f"\n{render_markdown(answer)}")
        finally:
            print_session_exit_banner(agent)
        return

    # Interactive REPL mode
    print("=" * 60)
    print(" 📚 BookStack Wiki AI Agent")
    print(f" Session ID: \033[33m{agent.session_id}\033[0m")
    status_tag = f" \033[32m({agent.model_status})\033[0m" if hasattr(agent, "model_status") else ""
    print(f" Model: \033[1;36m{agent.model}\033[0m{status_tag} @ \033[33m{agent.llm_url}\033[0m")
    print(" Mentions: \033[33m@filename\033[0m (Tab-complete local files to attach & import)")
    print(" Commands: \033[90m/goal\033[0m (plan status), \033[90m/files\033[0m (list files), \033[90m/model\033[0m (switch model), \033[90m/clear\033[0m, \033[90m/exit\033[0m")
    print("=" * 60 + "\n")

    exit_interrupt_count = 0
    prompt_str = "\001\033[1;32m\002You>\001\033[0m\002 "

    prompt_session = None
    if PromptSession:
        completer = FileMentionCompleter() if FileMentionCompleter else None
        prompt_session = PromptSession(completer=completer, history=InMemoryHistory())

    try:
        while True:
            try:
                if prompt_session:
                    user_input = prompt_session.prompt(ANSI("\033[1;32mYou>\033[0m ")).strip()
                else:
                    user_input = input(prompt_str).strip()
                exit_interrupt_count = 0
                if not user_input:
                    continue
                if user_input in ("/exit", "/quit", "exit", "quit"):
                    print("Goodbye!")
                    break
                if user_input == "/clear":
                    agent.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                    agent.goal_tracker.clear()
                    print("Conversation history and goal plan cleared.")
                    continue

                # Goal Plan Commands: /goal or /plan
                if user_input.startswith("/goal") or user_input.startswith("/plan"):
                    sub_parts = user_input.split(maxsplit=2)
                    if len(sub_parts) == 1 or sub_parts[1].lower() in ("show", "list", "status"):
                        if agent.goal_tracker.is_active():
                            print(f"\n{agent.goal_tracker.render_panel()}\n")
                        else:
                            print("\n\033[90m○ No active goal or plan set. Enter a prompt to auto-initialize or `/goal set <goal>`\033[0m\n")
                        continue
                    elif sub_parts[1].lower() in ("clear", "reset"):
                        agent.goal_tracker.clear()
                        print("✓ Goal plan cleared.")
                        continue
                    elif sub_parts[1].lower() == "set" and len(sub_parts) > 2:
                        agent.goal_tracker.set_goal(sub_parts[2])
                        print(f"\n{agent.goal_tracker.render_panel()}\n")
                        continue
                    elif sub_parts[1].lower() == "add" and len(sub_parts) > 2:
                        idx = agent.goal_tracker.add_step(sub_parts[2])
                        print(f"✓ Added step #{idx}.\n\n{agent.goal_tracker.render_panel()}\n")
                        continue
                    elif sub_parts[1].lower() == "done" and len(sub_parts) > 2:
                        try:
                            s_id = int(sub_parts[2])
                            agent.goal_tracker.complete_step(s_id)
                            print(f"\n{agent.goal_tracker.render_panel()}\n")
                        except ValueError:
                            print("Usage: /goal done <step_number>")
                        continue
                    else:
                        print("Goal Commands: `/goal` (show), `/goal set <desc>`, `/goal add <step>`, `/goal done <#>`, `/goal clear`")
                        continue

                # File Selector Commands
                if user_input in ("@", "/files", "/file", "/attach") or (user_input.startswith("@") and len(user_input.split()) == 1 and not (Path.cwd() / user_input.lstrip("@")).is_file()):
                    filter_str = user_input.lstrip("@") if user_input.startswith("@") and user_input != "@" else ""
                    selected = select_file_interactive(initial_filter=filter_str)
                    if selected:
                        print(f" Selected: \033[1;33m@{selected}\033[0m")
                        if prompt_session:
                            action_prompt = prompt_session.prompt(ANSI(f"\033[1;36mInstruction for @{selected}>\033[0m ")).strip()
                        else:
                            action_prompt = input(f"\001\033[1;36m\002Instruction for @{selected}>\001\033[0m\002 ").strip()
                        if action_prompt:
                            user_input = f"@{selected} {action_prompt}"
                        else:
                            user_input = f"@{selected} Please review and summarize the contents of this file."
                    else:
                        continue

                # Model Switcher Commands
                if user_input.startswith("/model") or user_input.startswith("/models"):
                    parts = user_input.split(maxsplit=1)
                    catalog = agent.get_model_catalog()
                    if len(parts) == 1 or parts[1].strip().lower() == "list":
                        print(f"\n Current Model: \033[1;36m{agent.model}\033[0m ({agent.model_status})")
                        if catalog:
                            print("\n Models on Endpoint:")
                            for item in catalog:
                                mid = item["id"]
                                is_active = (mid == agent.model)
                                is_loaded = item.get("loaded", False)
                                mtype = item.get("type", "")
                                quant = item.get("quant", "")

                                flags = []
                                if is_loaded:
                                    flags.append("\033[1;32mLOADED IN MEMORY\033[0m")
                                if is_active:
                                    flags.append("\033[1;36mACTIVE\033[0m")

                                meta = []
                                if mtype and mtype != "llm":
                                    meta.append(mtype.upper())
                                if quant:
                                    meta.append(quant)
                                meta_str = f" \033[90m[{', '.join(meta)}]\033[0m" if meta else ""

                                flag_str = f" ({', '.join(flags)})" if flags else ""
                                print(f"   • \033[33m{mid}\033[0m{meta_str}{flag_str}")
                        print("\n Usage: \033[90m/model <model_name>\033[0m to switch models.\n")
                        continue
                    else:
                        target = parts[1].strip()
                        # Check exact match
                        matched_id = target
                        all_ids = [m["id"] for m in catalog] if catalog else []
                        if target not in all_ids and all_ids:
                            # Try case-insensitive exact or substring match
                            exact_ci = [mid for mid in all_ids if mid.lower() == target.lower()]
                            if exact_ci:
                                matched_id = exact_ci[0]
                            else:
                                partial = [mid for mid in all_ids if target.lower() in mid.lower()]
                                if len(partial) == 1:
                                    matched_id = partial[0]
                                elif len(partial) > 1:
                                    print(f"\n Multiple models matched '{target}':")
                                    for p in partial:
                                        print(f"   • \033[33m{p}\033[0m")
                                    print(" Please specify the full model name.\n")
                                    continue

                        agent.set_model(matched_id)
                        print(f"\n✓ Switched active model to: \033[1;36m{agent.model}\033[0m\n")
                        continue

                # Resolve file mentions (@filename)
                augmented_prompt, attachments = resolve_file_mentions(user_input)
                if attachments:
                    now_str = datetime.now().strftime("%H:%M:%S")
                    for att in attachments:
                        if "content" in att:
                            print(f"  \033[90m[{now_str}]\033[0m \033[36m📎 Attached file:\033[0m \033[33m{att['rel_path']}\033[0m \033[90m({att['size']} bytes)\033[0m")
                        elif "error" in att:
                            print(f"  \033[90m[{now_str}]\033[0m \033[31m⚠ Attachment error:\033[0m \033[33m{att['rel_path']}\033[0m - {att['error']}")

                response = agent.chat(augmented_prompt, attachments=attachments)
                if response is not None:
                    print(f"\n\033[1;35mAgent>\033[0m\n{render_markdown(response)}\n")

            except KeyboardInterrupt:
                exit_interrupt_count += 1
                if exit_interrupt_count == 1:
                    print("\n\033[33mPress Ctrl+C again or type /exit to close the session.\033[0m")
                    continue
                else:
                    print("\nGoodbye!")
                    break
            except EOFError:
                print("\nGoodbye!")
                break
    finally:
        print_session_exit_banner(agent)


if __name__ == "__main__":
    main()
