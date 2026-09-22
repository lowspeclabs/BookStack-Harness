#!/usr/bin/env python3
"""
BookStack CLI
A robust CLI for interacting with the BookStack REST API.
Designed for both human users and AI/automation agents.
"""

import os
import sys
import time
import json
import difflib
import tempfile
import subprocess
import argparse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import requests
    from dotenv import load_dotenv
except ImportError:
    print(
        "Error: Missing required packages. Please run `./setup.sh` or install requirements:\n"
        "  pip install -r requirements.txt\n",
        file=sys.stderr,
    )
    sys.exit(1)


# -----------------------------------------------------------------------------
# Configuration & Client
# -----------------------------------------------------------------------------

class BookStackClient:
    """Client for BookStack REST API with automatic rate-limiting backoff."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token_id: Optional[str] = None,
        token_secret: Optional[str] = None,
        verify_ssl: bool = True,
        debug: bool = False,
        max_retries: int = 3,
    ):
        load_dotenv()

        self.base_url = (
            base_url
            or os.getenv("BOOKSTACK_BASE_URL")
            or ""
        ).rstrip("/")
        
        self.token_id = token_id or os.getenv("BOOKSTACK_TOKEN_ID") or ""
        self.token_secret = token_secret or os.getenv("BOOKSTACK_TOKEN_SECRET") or ""
        self.verify_ssl = verify_ssl
        self.debug = debug
        self.max_retries = max_retries

        if not self.base_url:
            raise ValueError(
                "BookStack base URL is required. Set BOOKSTACK_BASE_URL in .env or pass --base-url."
            )
        if not self.token_id or not self.token_secret:
            raise ValueError(
                "BookStack API Token ID and Secret are required. Set BOOKSTACK_TOKEN_ID and "
                "BOOKSTACK_TOKEN_SECRET in .env or pass --token-id and --token-secret."
            )

        self.api_url = f"{self.base_url}/api"
        self.headers = {
            "Authorization": f"Token {self.token_id}:{self.token_secret}",
            "Accept": "application/json",
        }

    def request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        raw_response: bool = False,
    ) -> Union[Dict[str, Any], List[Any], bytes, str]:
        """Send HTTP request to BookStack API with automatic retry on 429."""
        url = f"{self.api_url}/{endpoint.lstrip('/')}"
        
        headers = dict(self.headers)
        if json_data is not None and not files:
            headers["Content-Type"] = "application/json"

        for attempt in range(self.max_retries + 1):
            if self.debug:
                print(f"[DEBUG] {method} {url} (attempt {attempt + 1})", file=sys.stderr)
                if params:
                    print(f"[DEBUG] Params: {params}", file=sys.stderr)
                if json_data:
                    print(f"[DEBUG] JSON: {json_data}", file=sys.stderr)

            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params,
                    json=json_data,
                    data=data,
                    files=files,
                    verify=self.verify_ssl,
                )
            except requests.RequestException as req_err:
                if attempt < self.max_retries:
                    backoff = (2 ** attempt) * 1.5
                    if self.debug:
                        print(f"[DEBUG] Network error: {req_err}. Retrying in {backoff:.1f}s...", file=sys.stderr)
                    time.sleep(backoff)
                    continue
                raise

            if self.debug:
                print(f"[DEBUG] Status: {response.status_code}", file=sys.stderr)

            # Handle Rate Limiting (HTTP 429)
            if response.status_code == 429 and attempt < self.max_retries:
                retry_after = int(response.headers.get("Retry-After", (2 ** attempt) * 2))
                if self.debug:
                    print(f"[DEBUG] Rate limited (429). Retrying after {retry_after}s...", file=sys.stderr)
                time.sleep(retry_after)
                continue

            if raw_response:
                response.raise_for_status()
                return response.content

            # Handle empty responses (like 204 No Content)
            if response.status_code == 204 or not response.content:
                return {"status": "success", "code": response.status_code}

            try:
                res_json = response.json()
            except ValueError:
                response.raise_for_status()
                return response.text

            if not response.ok:
                err_msg = res_json.get("error", {}).get("message") or response.reason
                err_code = res_json.get("error", {}).get("code") or response.status_code
                raise RuntimeError(f"API Error ({err_code}): {err_msg}")

            return res_json


# -----------------------------------------------------------------------------
# Helpers & Output Formatters
# -----------------------------------------------------------------------------

def parse_tags(tags_input: Optional[List[str]]) -> Optional[List[Dict[str, str]]]:
    """Parse tag arguments formatted as 'name' or 'name:value'."""
    if not tags_input:
        return None
    tags = []
    for tag_str in tags_input:
        if ":" in tag_str:
            name, value = tag_str.split(":", 1)
            tags.append({"name": name.strip(), "value": value.strip()})
        else:
            tags.append({"name": tag_str.strip(), "value": ""})
    return tags


def parse_filters(filter_args: Optional[List[str]]) -> Dict[str, str]:
    """Parse filter list like ['name=Guide', 'created_at:gt=2024-01-01'] into query params."""
    params = {}
    if not filter_args:
        return params
    for f in filter_args:
        if "=" in f:
            k, v = f.split("=", 1)
            params[f"filter[{k.strip()}]"] = v.strip()
        else:
            params[f"filter[{f.strip()}]"] = ""
    return params


def print_output(data: Any, as_json: bool = False, title: Optional[str] = None):
    """Output data formatted as JSON or human-readable text."""
    if as_json:
        print(json.dumps(data, indent=2))
        return

    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            # Listing response
            total = data.get("total", len(data["data"]))
            count = len(data["data"])
            print(f"{'=== ' + title + ' ===' if title else '=== Results ==='} (Showing {count} of {total})")
            if not data["data"]:
                print("  (No records found)")
                return
            for item in data["data"]:
                item_id = item.get("id", "-")
                deletable = item.get("deletable") if isinstance(item.get("deletable"), dict) else {}
                name = (
                    item.get("display_name")
                    or item.get("name")
                    or deletable.get("name")
                    or item.get("activity")
                    or item.get("type")
                    or item.get("key")
                    or item.get("slug")
                    or item.get("query")
                    or "-"
                )
                type_name = item.get("type") or item.get("deletable_type") or ""
                extra = []
                if "key" in item and name != item["key"]:
                    extra.append(f"Action: {item['key']}")
                if "updated_at" in item:
                    extra.append(f"Updated: {item['updated_at']}")
                elif "created_at" in item:
                    extra.append(f"Created: {item['created_at']}")
                if "slug" in item and item.get("name"):
                    extra.append(f"Slug: {item['slug']}")
                extra_str = f" ({', '.join(extra)})" if extra else ""
                type_str = f"[{type_name}] " if type_name else ""
                print(f"  • ID {item_id:4}: {type_str}{name}{extra_str}")
            return

        # Single object response
        if title:
            print(f"=== {title} ===")
        for key, val in data.items():
            if key in ("html", "markdown") and isinstance(val, str) and len(val) > 200:
                print(f"  {key}: <{len(val)} characters of text>")
            elif isinstance(val, (list, dict)):
                val_str = json.dumps(val)
                if len(val_str) > 100:
                    val_str = val_str[:97] + "..."
                print(f"  {key}: {val_str}")
            else:
                print(f"  {key}: {val}")
    elif isinstance(data, list):
        if title:
            print(f"=== {title} ({len(data)} items) ===")
        for item in data:
            print(f"  • {item}")
    else:
        print(data)


def color_diff(diff_lines: List[str]) -> str:
    """Add terminal ANSI colors to unified diff output."""
    colored = []
    for line in diff_lines:
        if line.startswith("+") and not line.startswith("+++"):
            colored.append(f"\033[32m{line}\033[0m")  # Green
        elif line.startswith("-") and not line.startswith("---"):
            colored.append(f"\033[31m{line}\033[0m")  # Red
        elif line.startswith("@"):
            colored.append(f"\033[36m{line}\033[0m")  # Cyan
        else:
            colored.append(line)
    return "".join(colored)


# -----------------------------------------------------------------------------
# Command Implementations
# -----------------------------------------------------------------------------

def cmd_status(client: BookStackClient, args: argparse.Namespace):
    res = client.request("GET", "system")
    print_output(res, as_json=args.json, title="BookStack System Status")


def cmd_shelves(client: BookStackClient, args: argparse.Namespace):
    action = getattr(args, "action", None) or "list"
    if action == "list":
        params = {"count": getattr(args, "count", 20), "offset": getattr(args, "offset", 0)}
        if getattr(args, "sort", None):
            params["sort"] = args.sort
        params.update(parse_filters(getattr(args, "filter", None)))
        res = client.request("GET", "shelves", params=params)
        print_output(res, as_json=args.json, title="Shelves")
    elif action == "get":
        res = client.request("GET", f"shelves/{args.id}")
        print_output(res, as_json=args.json, title=f"Shelf #{args.id}")
    elif action == "find":
        query = args.query
        params = {"filter[name:like]": f"%{query}%", "count": 100}
        res = client.request("GET", "shelves", params=params)
        if isinstance(res, dict) and "data" in res and res["data"]:
            for item in res["data"]:
                if item.get("name", "").lower() == query.lower() or item.get("slug", "").lower() == query.lower():
                    print_output(item, as_json=args.json, title=f"Shelf Match: '{query}'")
                    return
        print_output(res, as_json=args.json, title=f"Shelves Matching: '{query}'")
    elif action == "create":
        payload: Dict[str, Any] = {"name": args.name}
        if args.description:
            payload["description"] = args.description
        if args.books:
            payload["books"] = [int(b.strip()) for b in args.books.split(",") if b.strip()]
        tags = parse_tags(args.tags)
        if tags:
            payload["tags"] = tags
        res = client.request("POST", "shelves", json_data=payload)
        print_output(res, as_json=args.json, title="Created Shelf")
    elif action == "update":
        payload = {}
        if args.name:
            payload["name"] = args.name
        if args.description is not None:
            payload["description"] = args.description
        if args.books is not None:
            payload["books"] = [int(b.strip()) for b in args.books.split(",") if b.strip()]
        tags = parse_tags(args.tags)
        if tags is not None:
            payload["tags"] = tags
        res = client.request("PUT", f"shelves/{args.id}", json_data=payload)
        print_output(res, as_json=args.json, title=f"Updated Shelf #{args.id}")
    elif action == "delete":
        res = client.request("DELETE", f"shelves/{args.id}")
        print_output(res, as_json=args.json, title=f"Deleted Shelf #{args.id}")


def cmd_books(client: BookStackClient, args: argparse.Namespace):
    action = getattr(args, "action", None) or "list"
    if action == "list":
        params = {"count": getattr(args, "count", 20), "offset": getattr(args, "offset", 0)}
        if getattr(args, "sort", None):
            params["sort"] = args.sort
        params.update(parse_filters(getattr(args, "filter", None)))
        res = client.request("GET", "books", params=params)
        print_output(res, as_json=args.json, title="Books")
    elif action == "get":
        res = client.request("GET", f"books/{args.id}")
        print_output(res, as_json=args.json, title=f"Book #{args.id}")
    elif action == "find":
        query = args.query
        params = {"filter[name:like]": f"%{query}%", "count": 100}
        res = client.request("GET", "books", params=params)
        if isinstance(res, dict) and "data" in res and res["data"]:
            for item in res["data"]:
                if item.get("name", "").lower() == query.lower() or item.get("slug", "").lower() == query.lower():
                    print_output(item, as_json=args.json, title=f"Book Match: '{query}'")
                    return
        print_output(res, as_json=args.json, title=f"Books Matching: '{query}'")
    elif action == "create":
        payload: Dict[str, Any] = {"name": args.name}
        if args.description:
            payload["description"] = args.description
        if args.default_template_id:
            payload["default_template_id"] = args.default_template_id
        tags = parse_tags(args.tags)
        if tags:
            payload["tags"] = tags
        res = client.request("POST", "books", json_data=payload)
        print_output(res, as_json=args.json, title="Created Book")
    elif action == "update":
        payload = {}
        if args.name:
            payload["name"] = args.name
        if args.description is not None:
            payload["description"] = args.description
        if args.default_template_id is not None:
            payload["default_template_id"] = args.default_template_id
        tags = parse_tags(args.tags)
        if tags is not None:
            payload["tags"] = tags
        res = client.request("PUT", f"books/{args.id}", json_data=payload)
        print_output(res, as_json=args.json, title=f"Updated Book #{args.id}")
    elif action == "delete":
        res = client.request("DELETE", f"books/{args.id}")
        print_output(res, as_json=args.json, title=f"Deleted Book #{args.id}")
    elif action == "export":
        content = client.request(
            "GET", f"books/{args.id}/export/{args.format}", raw_response=True
        )
        if args.output:
            Path(args.output).write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
            print(f"Exported book #{args.id} ({args.format}) to {args.output}")
        else:
            if isinstance(content, bytes):
                sys.stdout.buffer.write(content)
            else:
                sys.stdout.write(content)
    elif action == "export-tree":
        book = client.request("GET", f"books/{args.id}")
        if not isinstance(book, dict):
            raise RuntimeError(f"Failed to fetch book #{args.id}")
        out_dir = Path(args.output_dir or f"./book_{args.id}_{book.get('slug', 'export')}")
        out_dir.mkdir(parents=True, exist_ok=True)

        # Write Book Readme
        book_readme = f"# {book.get('name')}\n\n{book.get('description', '')}\n"
        (out_dir / "README.md").write_text(book_readme, encoding="utf-8")

        # Export all pages and chapters
        contents = book.get("contents", [])
        exported_count = 0
        for item in contents:
            item_type = item.get("type")
            if item_type == "page":
                page_id = item["id"]
                page_data = client.request("GET", f"pages/{page_id}")
                if isinstance(page_data, dict):
                    md = page_data.get("markdown") or page_data.get("html") or ""
                    file_path = out_dir / f"{item.get('slug', page_id)}.md"
                    file_path.write_text(f"# {item.get('name')}\n\n{md}", encoding="utf-8")
                    exported_count += 1
            elif item_type == "chapter":
                chap_id = item["id"]
                chap_dir = out_dir / item.get("slug", f"chapter_{chap_id}")
                chap_dir.mkdir(parents=True, exist_ok=True)
                chap_pages = item.get("pages", [])
                for cp in chap_pages:
                    cp_id = cp["id"]
                    cp_data = client.request("GET", f"pages/{cp_id}")
                    if isinstance(cp_data, dict):
                        md = cp_data.get("markdown") or cp_data.get("html") or ""
                        file_path = chap_dir / f"{cp.get('slug', cp_id)}.md"
                        file_path.write_text(f"# {cp.get('name')}\n\n{md}", encoding="utf-8")
                        exported_count += 1

        print_output(
            {"status": "success", "book_id": args.id, "exported_pages": exported_count, "directory": str(out_dir)},
            as_json=args.json,
            title=f"Exported Book Tree to {out_dir}",
        )


def cmd_chapters(client: BookStackClient, args: argparse.Namespace):
    action = getattr(args, "action", None) or "list"
    if action == "list":
        params = {"count": getattr(args, "count", 20), "offset": getattr(args, "offset", 0)}
        if getattr(args, "sort", None):
            params["sort"] = args.sort
        if getattr(args, "book_id", None):
            params["filter[book_id]"] = args.book_id
        params.update(parse_filters(getattr(args, "filter", None)))
        res = client.request("GET", "chapters", params=params)
        print_output(res, as_json=args.json, title="Chapters")
    elif action == "get":
        res = client.request("GET", f"chapters/{args.id}")
        print_output(res, as_json=args.json, title=f"Chapter #{args.id}")
    elif action == "create":
        payload: Dict[str, Any] = {"book_id": args.book_id, "name": args.name}
        if args.description:
            payload["description"] = args.description
        if args.priority is not None:
            payload["priority"] = args.priority
        tags = parse_tags(args.tags)
        if tags:
            payload["tags"] = tags
        res = client.request("POST", "chapters", json_data=payload)
        print_output(res, as_json=args.json, title="Created Chapter")
    elif action == "update":
        payload = {}
        if args.book_id:
            payload["book_id"] = args.book_id
        if args.name:
            payload["name"] = args.name
        if args.description is not None:
            payload["description"] = args.description
        if args.priority is not None:
            payload["priority"] = args.priority
        tags = parse_tags(args.tags)
        if tags is not None:
            payload["tags"] = tags
        res = client.request("PUT", f"chapters/{args.id}", json_data=payload)
        print_output(res, as_json=args.json, title=f"Updated Chapter #{args.id}")
    elif action == "delete":
        res = client.request("DELETE", f"chapters/{args.id}")
        print_output(res, as_json=args.json, title=f"Deleted Chapter #{args.id}")
    elif action == "export":
        content = client.request(
            "GET", f"chapters/{args.id}/export/{args.format}", raw_response=True
        )
        if args.output:
            Path(args.output).write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
            print(f"Exported chapter #{args.id} ({args.format}) to {args.output}")
        else:
            if isinstance(content, bytes):
                sys.stdout.buffer.write(content)
            else:
                sys.stdout.write(content)


def cmd_pages(client: BookStackClient, args: argparse.Namespace):
    action = getattr(args, "action", None) or "list"
    if action == "list":
        params = {"count": getattr(args, "count", 20), "offset": getattr(args, "offset", 0)}
        if getattr(args, "sort", None):
            params["sort"] = args.sort
        if getattr(args, "book_id", None):
            params["filter[book_id]"] = args.book_id
        if getattr(args, "chapter_id", None):
            params["filter[chapter_id]"] = args.chapter_id
        params.update(parse_filters(getattr(args, "filter", None)))
        res = client.request("GET", "pages", params=params)
        print_output(res, as_json=args.json, title="Pages")
    elif action == "get":
        res = client.request("GET", f"pages/{args.id}")
        offset = getattr(args, "offset", None)
        limit = getattr(args, "limit", None)
        if isinstance(res, dict) and (offset is not None or limit is not None):
            raw_md = res.get("markdown") or res.get("html") or ""
            off = int(offset or 0)
            lim = int(limit) if limit is not None else len(raw_md)
            res["offset"] = off
            res["limit"] = lim
            res["total_chars"] = len(raw_md)
            res["returned_chars"] = len(raw_md[off:off + lim])
            res["has_more"] = (off + lim) < len(raw_md)
            res["next_offset"] = (off + lim) if res["has_more"] else None
            if "markdown" in res:
                res["markdown"] = raw_md[off:off + lim]
        print_output(res, as_json=args.json, title=f"Page #{args.id}")
    elif action == "find":
        query = args.query
        params = {"filter[name:like]": f"%{query}%", "count": 100}
        if getattr(args, "book_id", None):
            params["filter[book_id]"] = args.book_id
        res = client.request("GET", "pages", params=params)
        if isinstance(res, dict) and "data" in res and res["data"]:
            for item in res["data"]:
                if item.get("name", "").lower() == query.lower() or item.get("slug", "").lower() == query.lower():
                    print_output(item, as_json=args.json, title=f"Page Match: '{query}'")
                    return
        print_output(res, as_json=args.json, title=f"Pages Matching: '{query}'")
    elif action == "content":
        res = client.request("GET", f"pages/{args.id}")
        if isinstance(res, dict):
            fmt = args.format or "markdown"
            content = res.get(fmt) or res.get("html") or res.get("raw_html") or ""
            offset = getattr(args, "offset", None)
            limit = getattr(args, "limit", None)
            if offset is not None or limit is not None:
                off = int(offset or 0)
                lim = int(limit) if limit is not None else len(content)
                content = content[off:off + lim]
            print(content)
        else:
            print(res)
    elif action == "create":
        payload: Dict[str, Any] = {"name": args.name}
        if args.book_id:
            payload["book_id"] = args.book_id
        if args.chapter_id:
            payload["chapter_id"] = args.chapter_id
        if not args.book_id and not args.chapter_id:
            raise ValueError("Either --book-id or --chapter-id must be provided when creating a page.")

        if args.markdown_file:
            payload["markdown"] = Path(args.markdown_file).read_text(encoding="utf-8")
        elif args.html_file:
            payload["html"] = Path(args.html_file).read_text(encoding="utf-8")
        elif args.markdown:
            payload["markdown"] = args.markdown
        elif args.html:
            payload["html"] = args.html
        elif not sys.stdin.isatty():
            payload["markdown"] = sys.stdin.read()
        else:
            raise ValueError("Page content is required. Supply --markdown, --html, --markdown-file, --html-file, or pipe via stdin.")

        if args.priority is not None:
            payload["priority"] = args.priority
        if args.changelog:
            payload["changelog"] = args.changelog
        tags = parse_tags(args.tags)
        if tags:
            payload["tags"] = tags

        res = client.request("POST", "pages", json_data=payload)
        print_output(res, as_json=args.json, title="Created Page")
    elif action == "upsert":
        # Search if a page with identical name exists in the book/chapter
        name = args.name
        params = {"filter[name]": name, "count": 10}
        if args.book_id:
            params["filter[book_id]"] = args.book_id
        if args.chapter_id:
            params["filter[chapter_id]"] = args.chapter_id

        existing = client.request("GET", "pages", params=params)
        found_id = None
        if isinstance(existing, dict) and existing.get("data"):
            found_id = existing["data"][0]["id"]

        # Prepare content
        content_md = None
        content_html = None
        if args.markdown_file:
            content_md = Path(args.markdown_file).read_text(encoding="utf-8")
        elif args.html_file:
            content_html = Path(args.html_file).read_text(encoding="utf-8")
        elif args.markdown:
            content_md = args.markdown
        elif args.html:
            content_html = args.html
        elif not sys.stdin.isatty():
            content_md = sys.stdin.read()

        if found_id:
            # Update existing
            payload = {"name": name}
            if content_md is not None:
                payload["markdown"] = content_md
            if content_html is not None:
                payload["html"] = content_html
            if args.changelog:
                payload["changelog"] = args.changelog
            tags = parse_tags(args.tags)
            if tags is not None:
                payload["tags"] = tags
            res = client.request("PUT", f"pages/{found_id}", json_data=payload)
            print_output(res, as_json=args.json, title=f"Upserted (Updated) Page #{found_id}")
        else:
            # Create new
            payload = {"name": name}
            if args.book_id:
                payload["book_id"] = args.book_id
            if args.chapter_id:
                payload["chapter_id"] = args.chapter_id
            if not args.book_id and not args.chapter_id:
                raise ValueError("Either --book-id or --chapter-id is required to create a new page.")
            if content_md is not None:
                payload["markdown"] = content_md
            elif content_html is not None:
                payload["html"] = content_html
            else:
                payload["markdown"] = f"# {name}\n"
            tags = parse_tags(args.tags)
            if tags:
                payload["tags"] = tags
            res = client.request("POST", "pages", json_data=payload)
            print_output(res, as_json=args.json, title="Upserted (Created) New Page")
    elif action == "update":
        payload = {}
        if args.name:
            payload["name"] = args.name
        if args.book_id:
            payload["book_id"] = args.book_id
        if args.chapter_id:
            payload["chapter_id"] = args.chapter_id

        if args.markdown_file:
            payload["markdown"] = Path(args.markdown_file).read_text(encoding="utf-8")
        elif args.html_file:
            payload["html"] = Path(args.html_file).read_text(encoding="utf-8")
        elif args.markdown:
            payload["markdown"] = args.markdown
        elif args.html:
            payload["html"] = args.html

        if args.priority is not None:
            payload["priority"] = args.priority
        if args.changelog:
            payload["changelog"] = args.changelog
        tags = parse_tags(args.tags)
        if tags is not None:
            payload["tags"] = tags

        res = client.request("PUT", f"pages/{args.id}", json_data=payload)
        print_output(res, as_json=args.json, title=f"Updated Page #{args.id}")
    elif action == "edit":
        # Interactive edit with $EDITOR
        page = client.request("GET", f"pages/{args.id}")
        if not isinstance(page, dict):
            raise RuntimeError(f"Page #{args.id} not found.")
        current_content = page.get("markdown") or page.get("raw_html") or page.get("html") or ""

        editor = os.getenv("EDITOR") or "nano"
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w+", encoding="utf-8") as tf:
            tf.write(current_content)
            temp_path = tf.name

        try:
            subprocess.check_call([editor, temp_path])
            new_content = Path(temp_path).read_text(encoding="utf-8")
            if new_content == current_content:
                print("No changes made.")
                return
            res = client.request("PUT", f"pages/{args.id}", json_data={"markdown": new_content, "changelog": "Edited via CLI"})
            print_output(res, as_json=args.json, title=f"Saved changes to Page #{args.id}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    elif action == "diff":
        # Compare local vs remote content
        page = client.request("GET", f"pages/{args.id}")
        if not isinstance(page, dict):
            raise RuntimeError(f"Page #{args.id} not found.")
        remote_content = page.get("markdown") or page.get("html") or ""

        local_content = ""
        if args.markdown_file:
            local_content = Path(args.markdown_file).read_text(encoding="utf-8")
        elif args.markdown:
            local_content = args.markdown
        elif not sys.stdin.isatty():
            local_content = sys.stdin.read()
        else:
            raise ValueError("Provide --markdown-file, --markdown, or pipe to compare against remote page.")

        diff = list(
            difflib.unified_diff(
                remote_content.splitlines(keepends=True),
                local_content.splitlines(keepends=True),
                fromfile=f"remote:page_{args.id}",
                tofile="local:content",
            )
        )
        if not diff:
            print("No differences found.")
        else:
            print(color_diff(diff))
    elif action == "import-dir":
        input_dir = Path(args.dir)
        if not input_dir.is_dir():
            raise FileNotFoundError(f"Directory not found: {args.dir}")

        pattern = "**/*.md" if args.recursive else "*.md"
        files = list(input_dir.glob(pattern))
        if not files:
            print(f"No markdown files found in {args.dir}")
            return

        imported = []
        for file in sorted(files):
            text = file.read_text(encoding="utf-8")
            # Extract H1 or use file stem
            name = file.stem.replace("-", " ").replace("_", " ").title()
            for line in text.splitlines():
                if line.startswith("# "):
                    name = line.lstrip("# ").strip()
                    break

            payload = {
                "name": name,
                "markdown": text,
            }
            if args.book_id:
                payload["book_id"] = args.book_id
            if args.chapter_id:
                payload["chapter_id"] = args.chapter_id

            res = client.request("POST", "pages", json_data=payload)
            if isinstance(res, dict):
                imported.append({"id": res.get("id"), "name": name, "file": str(file)})
                if not args.json:
                    print(f"  ✓ Imported: '{name}' (ID: {res.get('id')}) from {file.name}")

        print_output(
            {"status": "success", "imported_count": len(imported), "pages": imported},
            as_json=args.json,
            title=f"Imported {len(imported)} pages from {args.dir}",
        )
    elif action == "delete":
        res = client.request("DELETE", f"pages/{args.id}")
        print_output(res, as_json=args.json, title=f"Deleted Page #{args.id}")
    elif action == "export":
        content = client.request(
            "GET", f"pages/{args.id}/export/{args.format}", raw_response=True
        )
        if args.output:
            Path(args.output).write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
            print(f"Exported page #{args.id} ({args.format}) to {args.output}")
        else:
            if isinstance(content, bytes):
                sys.stdout.buffer.write(content)
            else:
                sys.stdout.write(content)


def cmd_search(client: BookStackClient, args: argparse.Namespace):
    params = {"query": args.query, "page": args.page, "count": args.count}
    res = client.request("GET", "search", params=params)
    print_output(res, as_json=args.json, title=f"Search Results: '{args.query}'")


def cmd_attachments(client: BookStackClient, args: argparse.Namespace):
    action = getattr(args, "action", None) or "list"
    if action == "list":
        params = {"count": getattr(args, "count", 20), "offset": getattr(args, "offset", 0)}
        if getattr(args, "sort", None):
            params["sort"] = args.sort
        params.update(parse_filters(getattr(args, "filter", None)))
        res = client.request("GET", "attachments", params=params)
        print_output(res, as_json=args.json, title="Attachments")
    elif action == "get":
        res = client.request("GET", f"attachments/{args.id}")
        print_output(res, as_json=args.json, title=f"Attachment #{args.id}")
    elif action == "upload":
        if args.file:
            filepath = Path(args.file)
            if not filepath.exists():
                raise FileNotFoundError(f"File not found: {args.file}")
            with open(filepath, "rb") as f:
                data = {"uploaded_to": str(args.page_id), "name": args.name or filepath.name}
                files = {"file": (filepath.name, f)}
                res = client.request("POST", "attachments", data=data, files=files)
        elif args.link:
            payload = {
                "uploaded_to": args.page_id,
                "name": args.name or args.link,
                "link": args.link,
            }
            res = client.request("POST", "attachments", json_data=payload)
        else:
            raise ValueError("Either --file or --link is required for upload.")
        print_output(res, as_json=args.json, title="Uploaded Attachment")
    elif action == "delete":
        res = client.request("DELETE", f"attachments/{args.id}")
        print_output(res, as_json=args.json, title=f"Deleted Attachment #{args.id}")


def cmd_images(client: BookStackClient, args: argparse.Namespace):
    action = getattr(args, "action", None) or "list"
    if action == "list":
        params = {"count": getattr(args, "count", 20), "offset": getattr(args, "offset", 0)}
        if getattr(args, "sort", None):
            params["sort"] = args.sort
        params.update(parse_filters(getattr(args, "filter", None)))
        res = client.request("GET", "image-gallery", params=params)
        print_output(res, as_json=args.json, title="Image Gallery")
    elif action == "get":
        res = client.request("GET", f"image-gallery/{args.id}")
        print_output(res, as_json=args.json, title=f"Image #{args.id}")
    elif action == "upload":
        filepath = Path(args.file)
        if not filepath.exists():
            raise FileNotFoundError(f"File not found: {args.file}")
        with open(filepath, "rb") as f:
            data = {
                "uploaded_to": str(args.page_id),
                "name": args.name or filepath.name,
                "type": args.type or "gallery",
            }
            files = {"image": (filepath.name, f)}
            res = client.request("POST", "image-gallery", data=data, files=files)
        print_output(res, as_json=args.json, title="Uploaded Image")
    elif action == "download":
        content = client.request("GET", f"image-gallery/{args.id}/data", raw_response=True)
        if args.output:
            Path(args.output).write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
            print(f"Downloaded image #{args.id} to {args.output}")
        else:
            sys.stdout.buffer.write(content if isinstance(content, bytes) else content.encode("utf-8"))
    elif action == "delete":
        res = client.request("DELETE", f"image-gallery/{args.id}")
        print_output(res, as_json=args.json, title=f"Deleted Image #{args.id}")


def cmd_comments(client: BookStackClient, args: argparse.Namespace):
    action = getattr(args, "action", None) or "list"
    if action == "list":
        params = {"count": getattr(args, "count", 20), "offset": getattr(args, "offset", 0)}
        if getattr(args, "page_id", None):
            params["filter[page_id]"] = args.page_id
        params.update(parse_filters(getattr(args, "filter", None)))
        res = client.request("GET", "comments", params=params)
        print_output(res, as_json=args.json, title="Comments")
    elif action == "get":
        res = client.request("GET", f"comments/{args.id}")
        print_output(res, as_json=args.json, title=f"Comment #{args.id}")
    elif action == "create":
        html_val = args.html
        if not html_val and args.text:
            html_val = f"<p>{args.text}</p>"
        if not html_val:
            raise ValueError("Either --text or --html is required for comment creation.")
        payload: Dict[str, Any] = {"page_id": args.page_id, "html": html_val}
        reply_to = getattr(args, "reply_to", None) or getattr(args, "parent_id", None)
        if reply_to:
            payload["reply_to"] = reply_to
        res = client.request("POST", "comments", json_data=payload)
        print_output(res, as_json=args.json, title="Created Comment")
    elif action == "update":
        payload = {}
        if args.html:
            payload["html"] = args.html
        elif args.text:
            payload["html"] = f"<p>{args.text}</p>"
        res = client.request("PUT", f"comments/{args.id}", json_data=payload)
        print_output(res, as_json=args.json, title=f"Updated Comment #{args.id}")
    elif action == "delete":
        res = client.request("DELETE", f"comments/{args.id}")
        print_output(res, as_json=args.json, title=f"Deleted Comment #{args.id}")


def cmd_permissions(client: BookStackClient, args: argparse.Namespace):
    action = getattr(args, "action", None) or "get"
    content_type = args.content_type
    content_id = args.content_id
    endpoint = f"content-permissions/{content_type}/{content_id}"

    if action == "get":
        res = client.request("GET", endpoint)
        print_output(res, as_json=args.json, title=f"Permissions for {content_type} #{content_id}")
    elif action == "set":
        payload: Dict[str, Any] = {}
        if args.fallback_inherits is not None:
            payload["fallback_inherits"] = args.fallback_inherits
        if args.role_id is not None:
            role_perm = {"role_id": args.role_id}
            if args.view is not None:
                role_perm["view"] = args.view
            if args.create is not None:
                role_perm["create"] = args.create
            if args.update is not None:
                role_perm["update"] = args.update
            if args.delete is not None:
                role_perm["delete"] = args.delete
            payload["role_permissions"] = [role_perm]
        res = client.request("PUT", endpoint, json_data=payload)
        print_output(res, as_json=args.json, title=f"Updated Permissions for {content_type} #{content_id}")


def cmd_users(client: BookStackClient, args: argparse.Namespace):
    action = getattr(args, "action", None) or "list"
    if action == "list":
        params = {"count": getattr(args, "count", 20), "offset": getattr(args, "offset", 0)}
        if getattr(args, "sort", None):
            params["sort"] = args.sort
        params.update(parse_filters(getattr(args, "filter", None)))
        res = client.request("GET", "users", params=params)
        print_output(res, as_json=args.json, title="Users")
    elif action == "get":
        res = client.request("GET", f"users/{args.id}")
        print_output(res, as_json=args.json, title=f"User #{args.id}")
    elif action == "create":
        payload: Dict[str, Any] = {"name": args.name, "email": args.email}
        if args.password:
            payload["password"] = args.password
        if args.roles:
            payload["roles"] = [int(r.strip()) for r in args.roles.split(",") if r.strip()]
        if args.send_invite:
            payload["send_invite"] = True
        res = client.request("POST", "users", json_data=payload)
        print_output(res, as_json=args.json, title="Created User")
    elif action == "update":
        payload = {}
        if args.name:
            payload["name"] = args.name
        if args.email:
            payload["email"] = args.email
        if args.password:
            payload["password"] = args.password
        if args.roles is not None:
            payload["roles"] = [int(r.strip()) for r in args.roles.split(",") if r.strip()]
        if args.language:
            payload["language"] = args.language
        res = client.request("PUT", f"users/{args.id}", json_data=payload)
        print_output(res, as_json=args.json, title=f"Updated User #{args.id}")
    elif action == "delete":
        params = {}
        if args.migrate_ownership_id:
            params["migrate_ownership_id"] = args.migrate_ownership_id
        res = client.request("DELETE", f"users/{args.id}", params=params)
        print_output(res, as_json=args.json, title=f"Deleted User #{args.id}")


def cmd_roles(client: BookStackClient, args: argparse.Namespace):
    action = getattr(args, "action", None) or "list"
    if action == "list":
        params = {"count": getattr(args, "count", 20), "offset": getattr(args, "offset", 0)}
        if getattr(args, "sort", None):
            params["sort"] = args.sort
        params.update(parse_filters(getattr(args, "filter", None)))
        res = client.request("GET", "roles", params=params)
        print_output(res, as_json=args.json, title="Roles")
    elif action == "get":
        res = client.request("GET", f"roles/{args.id}")
        print_output(res, as_json=args.json, title=f"Role #{args.id}")


def cmd_audit(client: BookStackClient, args: argparse.Namespace):
    params = {"count": getattr(args, "count", 20), "offset": getattr(args, "offset", 0)}
    if getattr(args, "sort", None):
        params["sort"] = args.sort
    params.update(parse_filters(getattr(args, "filter", None)))
    res = client.request("GET", "audit-log", params=params)
    print_output(res, as_json=args.json, title="Audit Log")


def cmd_recycle(client: BookStackClient, args: argparse.Namespace):
    action = getattr(args, "action", None) or "list"
    if action == "list":
        params = {"count": getattr(args, "count", 20), "offset": getattr(args, "offset", 0)}
        res = client.request("GET", "recycle-bin", params=params)
        print_output(res, as_json=args.json, title="Recycle Bin")
    elif action == "restore":
        res = client.request("PUT", f"recycle-bin/{args.id}")
        print_output(res, as_json=args.json, title=f"Restored Item #{args.id}")
    elif action == "destroy":
        res = client.request("DELETE", f"recycle-bin/{args.id}")
        print_output(res, as_json=args.json, title=f"Permanently Deleted Item #{args.id}")


def cmd_tags(client: BookStackClient, args: argparse.Namespace):
    action = args.action
    if action == "names":
        res = client.request("GET", "tags/names")
        print_output(res, as_json=args.json, title="Tag Names")
    elif action == "values":
        res = client.request("GET", "tags/values-for-name", params={"name": args.name})
        print_output(res, as_json=args.json, title=f"Tag Values for '{args.name}'")


# -----------------------------------------------------------------------------
# CLI Parser Setup
# -----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bookstack",
        description="CLI tool to interact with the BookStack REST API for humans and AI agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ./bookstack.py status
  ./bookstack.py search "API documentation"
  ./bookstack.py books
  ./bookstack.py pages find "HPZ6G4"
  ./bookstack.py pages upsert --book-id 3 --name "Nginx Setup" --markdown-file ./doc.md
  ./bookstack.py pages edit 42
  ./bookstack.py pages diff 42 --markdown-file ./updated.md
  ./bookstack.py pages import-dir --book-id 3 --dir ./docs --recursive
  ./bookstack.py books export-tree 3 --output-dir ./backup_servers
""",
    )

    # Global options
    parser.add_argument("--json", "-j", action="store_true", help="Output result as JSON")
    parser.add_argument("--base-url", help="BookStack Base URL (overrides BOOKSTACK_BASE_URL env)")
    parser.add_argument("--token-id", help="API Token ID (overrides BOOKSTACK_TOKEN_ID env)")
    parser.add_argument("--token-secret", help="API Token Secret (overrides BOOKSTACK_TOKEN_SECRET env)")
    parser.add_argument("--insecure", "-k", action="store_true", help="Disable SSL certificate verification")
    parser.add_argument("--debug", action="store_true", help="Print debug HTTP info to stderr")

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Common listing arguments helper
    def add_listing_args(p):
        p.add_argument("--count", type=int, default=20, help="Number of records to return (default: 20)")
        p.add_argument("--offset", type=int, default=0, help="Record offset for pagination (default: 0)")
        p.add_argument("--sort", help="Sort order (e.g. '+name', '-created_at')")
        p.add_argument("--filter", action="append", help="Filter criteria (e.g. 'name:like=%doc%', 'id=5')")

    # --- Status / System ---
    subparsers.add_parser("status", help="Check BookStack API connection and system information")

    # --- Search ---
    p_search = subparsers.add_parser("search", help="Search content across shelves, books, chapters, and pages")
    p_search.add_argument("query", help="Search query string (e.g. 'install guide', '[tag=value]')")
    p_search.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    p_search.add_argument("--count", type=int, default=20, help="Results per page (default: 20, max: 100)")

    # --- Shelves ---
    p_shelves = subparsers.add_parser("shelves", help="Manage bookshelves")
    add_listing_args(p_shelves)
    sp_shelves = p_shelves.add_subparsers(dest="action")
    
    sp_shelves_list = sp_shelves.add_parser("list", help="List shelves")
    add_listing_args(sp_shelves_list)

    sp_shelves_get = sp_shelves.add_parser("get", help="Get shelf details")
    sp_shelves_get.add_argument("id", type=int, help="Shelf ID")

    sp_shelves_find = sp_shelves.add_parser("find", help="Find shelf by exact name or slug")
    sp_shelves_find.add_argument("query", help="Shelf name or slug")

    sp_shelves_create = sp_shelves.add_parser("create", help="Create a new shelf")
    sp_shelves_create.add_argument("--name", required=True, help="Shelf name")
    sp_shelves_create.add_argument("--description", help="Shelf description")
    sp_shelves_create.add_argument("--books", help="Comma-separated book IDs (e.g. '1,2,5')")
    sp_shelves_create.add_argument("--tags", nargs="*", help="Tags in format 'name' or 'name:value'")

    sp_shelves_update = sp_shelves.add_parser("update", help="Update an existing shelf")
    sp_shelves_update.add_argument("id", type=int, help="Shelf ID")
    sp_shelves_update.add_argument("--name", help="Shelf name")
    sp_shelves_update.add_argument("--description", help="Shelf description")
    sp_shelves_update.add_argument("--books", help="Comma-separated book IDs")
    sp_shelves_update.add_argument("--tags", nargs="*", help="Tags")

    sp_shelves_delete = sp_shelves.add_parser("delete", help="Delete a shelf")
    sp_shelves_delete.add_argument("id", type=int, help="Shelf ID")

    # --- Books ---
    p_books = subparsers.add_parser("books", help="Manage books")
    add_listing_args(p_books)
    sp_books = p_books.add_subparsers(dest="action")

    sp_books_list = sp_books.add_parser("list", help="List books")
    add_listing_args(sp_books_list)

    sp_books_get = sp_books.add_parser("get", help="Get book details and table of contents")
    sp_books_get.add_argument("id", type=int, help="Book ID")

    sp_books_find = sp_books.add_parser("find", help="Find book by exact name or slug")
    sp_books_find.add_argument("query", help="Book name or slug")

    sp_books_create = sp_books.add_parser("create", help="Create a new book")
    sp_books_create.add_argument("--name", required=True, help="Book name")
    sp_books_create.add_argument("--description", help="Book description")
    sp_books_create.add_argument("--default-template-id", type=int, help="Default page template ID")
    sp_books_create.add_argument("--tags", nargs="*", help="Tags")

    sp_books_update = sp_books.add_parser("update", help="Update a book")
    sp_books_update.add_argument("id", type=int, help="Book ID")
    sp_books_update.add_argument("--name", help="Book name")
    sp_books_update.add_argument("--description", help="Book description")
    sp_books_update.add_argument("--default-template-id", type=int, help="Default page template ID")
    sp_books_update.add_argument("--tags", nargs="*", help="Tags")

    sp_books_delete = sp_books.add_parser("delete", help="Delete a book")
    sp_books_delete.add_argument("id", type=int, help="Book ID")

    sp_books_export = sp_books.add_parser("export", help="Export a book")
    sp_books_export.add_argument("id", type=int, help="Book ID")
    sp_books_export.add_argument("--format", choices=["html", "pdf", "plaintext", "markdown", "zip"], default="markdown", help="Export format")
    sp_books_export.add_argument("--output", "-o", help="Output file path (prints to stdout if omitted)")

    sp_books_tree = sp_books.add_parser("export-tree", help="Export book into folder hierarchy of Markdown files")
    sp_books_tree.add_argument("id", type=int, help="Book ID")
    sp_books_tree.add_argument("--output-dir", "-o", help="Target output directory")

    # --- Chapters ---
    p_chapters = subparsers.add_parser("chapters", help="Manage chapters")
    add_listing_args(p_chapters)
    p_chapters.add_argument("--book-id", type=int, help="Filter by parent book ID")
    sp_chapters = p_chapters.add_subparsers(dest="action")

    sp_chapters_list = sp_chapters.add_parser("list", help="List chapters")
    add_listing_args(sp_chapters_list)
    sp_chapters_list.add_argument("--book-id", type=int, help="Filter by parent book ID")

    sp_chapters_get = sp_chapters.add_parser("get", help="Get chapter details and contained pages")
    sp_chapters_get.add_argument("id", type=int, help="Chapter ID")

    sp_chapters_create = sp_chapters.add_parser("create", help="Create a chapter")
    sp_chapters_create.add_argument("--book-id", type=int, required=True, help="Parent book ID")
    sp_chapters_create.add_argument("--name", required=True, help="Chapter name")
    sp_chapters_create.add_argument("--description", help="Chapter description")
    sp_chapters_create.add_argument("--priority", type=int, help="Order priority")
    sp_chapters_create.add_argument("--tags", nargs="*", help="Tags")

    sp_chapters_update = sp_chapters.add_parser("update", help="Update a chapter")
    sp_chapters_update.add_argument("id", type=int, help="Chapter ID")
    sp_chapters_update.add_argument("--book-id", type=int, help="Parent book ID")
    sp_chapters_update.add_argument("--name", help="Chapter name")
    sp_chapters_update.add_argument("--description", help="Chapter description")
    sp_chapters_update.add_argument("--priority", type=int, help="Order priority")
    sp_chapters_update.add_argument("--tags", nargs="*", help="Tags")

    sp_chapters_delete = sp_chapters.add_parser("delete", help="Delete a chapter")
    sp_chapters_delete.add_argument("id", type=int, help="Chapter ID")

    sp_chapters_export = sp_chapters.add_parser("export", help="Export a chapter")
    sp_chapters_export.add_argument("id", type=int, help="Chapter ID")
    sp_chapters_export.add_argument("--format", choices=["html", "pdf", "plaintext", "markdown", "zip"], default="markdown", help="Export format")
    sp_chapters_export.add_argument("--output", "-o", help="Output file path")

    # --- Pages ---
    p_pages = subparsers.add_parser("pages", help="Manage pages")
    add_listing_args(p_pages)
    p_pages.add_argument("--book-id", type=int, help="Filter by parent book ID")
    p_pages.add_argument("--chapter-id", type=int, help="Filter by parent chapter ID")
    sp_pages = p_pages.add_subparsers(dest="action")

    sp_pages_list = sp_pages.add_parser("list", help="List pages")
    add_listing_args(sp_pages_list)
    sp_pages_list.add_argument("--book-id", type=int, help="Filter by parent book ID")
    sp_pages_list.add_argument("--chapter-id", type=int, help="Filter by parent chapter ID")

    sp_pages_get = sp_pages.add_parser("get", help="Get page metadata and content")
    sp_pages_get.add_argument("id", type=int, help="Page ID")
    sp_pages_get.add_argument("--offset", type=int, help="Character offset for content")
    sp_pages_get.add_argument("--limit", type=int, help="Max characters to return in content")

    sp_pages_find = sp_pages.add_parser("find", help="Find page by exact name or slug")
    sp_pages_find.add_argument("query", help="Page name or slug")
    sp_pages_find.add_argument("--book-id", type=int, help="Limit search to book ID")

    sp_pages_content = sp_pages.add_parser("content", help="Print raw page markdown or HTML content")
    sp_pages_content.add_argument("id", type=int, help="Page ID")
    sp_pages_content.add_argument("--format", choices=["markdown", "html", "raw_html"], default="markdown", help="Content format to output")
    sp_pages_content.add_argument("--offset", type=int, help="Character offset from start of content")
    sp_pages_content.add_argument("--limit", type=int, help="Max characters to output")

    sp_pages_create = sp_pages.add_parser("create", help="Create a page")
    sp_pages_create.add_argument("--name", required=True, help="Page name")
    sp_pages_create.add_argument("--book-id", type=int, help="Parent book ID (if top-level)")
    sp_pages_create.add_argument("--chapter-id", type=int, help="Parent chapter ID")
    sp_pages_create.add_argument("--markdown", help="Markdown content string")
    sp_pages_create.add_argument("--html", help="HTML content string")
    sp_pages_create.add_argument("--markdown-file", help="Path to markdown file to upload")
    sp_pages_create.add_argument("--html-file", help="Path to HTML file to upload")
    sp_pages_create.add_argument("--tags", nargs="*", help="Tags")
    sp_pages_create.add_argument("--priority", type=int, help="Order priority")
    sp_pages_create.add_argument("--changelog", help="Change log summary")

    sp_pages_upsert = sp_pages.add_parser("upsert", help="Create page or update if matching name exists")
    sp_pages_upsert.add_argument("--name", required=True, help="Page name")
    sp_pages_upsert.add_argument("--book-id", type=int, help="Parent book ID")
    sp_pages_upsert.add_argument("--chapter-id", type=int, help="Parent chapter ID")
    sp_pages_upsert.add_argument("--markdown", help="Markdown content string")
    sp_pages_upsert.add_argument("--html", help="HTML content string")
    sp_pages_upsert.add_argument("--markdown-file", help="Path to markdown file")
    sp_pages_upsert.add_argument("--html-file", help="Path to HTML file")
    sp_pages_upsert.add_argument("--tags", nargs="*", help="Tags")
    sp_pages_upsert.add_argument("--changelog", help="Change log summary")

    sp_pages_update = sp_pages.add_parser("update", help="Update a page")
    sp_pages_update.add_argument("id", type=int, help="Page ID")
    sp_pages_update.add_argument("--name", help="Page name")
    sp_pages_update.add_argument("--book-id", type=int, help="Parent book ID")
    sp_pages_update.add_argument("--chapter-id", type=int, help="Parent chapter ID")
    sp_pages_update.add_argument("--markdown", help="Markdown content string")
    sp_pages_update.add_argument("--html", help="HTML content string")
    sp_pages_update.add_argument("--markdown-file", help="Path to markdown file")
    sp_pages_update.add_argument("--html-file", help="Path to HTML file")
    sp_pages_update.add_argument("--tags", nargs="*", help="Tags")
    sp_pages_update.add_argument("--priority", type=int, help="Order priority")
    sp_pages_update.add_argument("--changelog", help="Change log summary")

    sp_pages_edit = sp_pages.add_parser("edit", help="Open page markdown in terminal $EDITOR and save changes")
    sp_pages_edit.add_argument("id", type=int, help="Page ID")

    sp_pages_diff = sp_pages.add_parser("diff", help="Show terminal diff between remote page and local content")
    sp_pages_diff.add_argument("id", type=int, help="Page ID")
    sp_pages_diff.add_argument("--markdown", help="Local Markdown string")
    sp_pages_diff.add_argument("--markdown-file", help="Local Markdown file path")

    sp_pages_import = sp_pages.add_parser("import-dir", help="Import directory of Markdown files into book/chapter")
    sp_pages_import.add_argument("--dir", required=True, help="Path to folder containing .md files")
    sp_pages_import.add_argument("--book-id", type=int, help="Target book ID")
    sp_pages_import.add_argument("--chapter-id", type=int, help="Target chapter ID")
    sp_pages_import.add_argument("--recursive", "-r", action="store_true", help="Search subdirectories recursively")

    sp_pages_delete = sp_pages.add_parser("delete", help="Delete a page")
    sp_pages_delete.add_argument("id", type=int, help="Page ID")

    sp_pages_export = sp_pages.add_parser("export", help="Export a page")
    sp_pages_export.add_argument("id", type=int, help="Page ID")
    sp_pages_export.add_argument("--format", choices=["html", "pdf", "plaintext", "markdown", "zip"], default="markdown", help="Export format")
    sp_pages_export.add_argument("--output", "-o", help="Output file path")

    # --- Attachments ---
    p_att = subparsers.add_parser("attachments", help="Manage page attachments")
    add_listing_args(p_att)
    sp_att = p_att.add_subparsers(dest="action")

    sp_att_list = sp_att.add_parser("list", help="List attachments")
    add_listing_args(sp_att_list)

    sp_att_get = sp_att.add_parser("get", help="Get attachment metadata")
    sp_att_get.add_argument("id", type=int, help="Attachment ID")

    sp_att_upload = sp_att.add_parser("upload", help="Upload a file or link attachment to a page")
    sp_att_upload.add_argument("--page-id", type=int, required=True, help="Page ID to attach to")
    sp_att_upload.add_argument("--file", help="File path to upload")
    sp_att_upload.add_argument("--link", help="External URL link")
    sp_att_upload.add_argument("--name", help="Display name for attachment")

    sp_att_delete = sp_att.add_parser("delete", help="Delete an attachment")
    sp_att_delete.add_argument("id", type=int, help="Attachment ID")

    # --- Image Gallery ---
    p_img = subparsers.add_parser("images", help="Manage gallery and drawing images")
    add_listing_args(p_img)
    sp_img = p_img.add_subparsers(dest="action")

    sp_img_list = sp_img.add_parser("list", help="List gallery images")
    add_listing_args(sp_img_list)

    sp_img_get = sp_img.add_parser("get", help="Get image metadata")
    sp_img_get.add_argument("id", type=int, help="Image ID")

    sp_img_upload = sp_img.add_parser("upload", help="Upload an image to a page")
    sp_img_upload.add_argument("--page-id", type=int, required=True, help="Page ID")
    sp_img_upload.add_argument("--file", required=True, help="Image file path")
    sp_img_upload.add_argument("--name", help="Image name")
    sp_img_upload.add_argument("--type", choices=["gallery", "drawio"], default="gallery", help="Image type")

    sp_img_download = sp_img.add_parser("download", help="Download raw image file")
    sp_img_download.add_argument("id", type=int, help="Image ID")
    sp_img_download.add_argument("--output", "-o", help="Output file path")

    sp_img_delete = sp_img.add_parser("delete", help="Delete an image")
    sp_img_delete.add_argument("id", type=int, help="Image ID")

    # --- Comments ---
    p_comm = subparsers.add_parser("comments", help="Manage page comments")
    add_listing_args(p_comm)
    p_comm.add_argument("--page-id", type=int, help="Filter by page ID")
    sp_comm = p_comm.add_subparsers(dest="action")

    sp_comm_list = sp_comm.add_parser("list", help="List comments")
    add_listing_args(sp_comm_list)
    sp_comm_list.add_argument("--page-id", type=int, help="Filter by page ID")

    sp_comm_get = sp_comm.add_parser("get", help="Get comment details")
    sp_comm_get.add_argument("id", type=int, help="Comment ID")

    sp_comm_create = sp_comm.add_parser("create", help="Create a comment on a page")
    sp_comm_create.add_argument("--page-id", type=int, required=True, help="Page ID")
    sp_comm_create.add_argument("--text", help="Comment text")
    sp_comm_create.add_argument("--html", help="Comment HTML")
    sp_comm_create.add_argument("--parent-id", type=int, help="Parent comment ID (for replies)")
    sp_comm_create.add_argument("--reply-to", type=int, help="Reply-to comment ID")

    sp_comm_update = sp_comm.add_parser("update", help="Update a comment")
    sp_comm_update.add_argument("id", type=int, help="Comment ID")
    sp_comm_update.add_argument("--text", help="Comment text")
    sp_comm_update.add_argument("--html", help="Comment HTML")

    sp_comm_delete = sp_comm.add_parser("delete", help="Delete a comment")
    sp_comm_delete.add_argument("id", type=int, help="Comment ID")

    # --- Permissions ---
    p_perm = subparsers.add_parser("permissions", help="Manage content permissions")
    sp_perm = p_perm.add_subparsers(dest="action")

    sp_perm_get = sp_perm.add_parser("get", help="Get permissions for content item")
    sp_perm_get.add_argument("content_type", choices=["bookshelf", "book", "chapter", "page"], help="Content type")
    sp_perm_get.add_argument("content_id", type=int, help="Content item ID")

    sp_perm_set = sp_perm.add_parser("set", help="Set role-based permissions for content item")
    sp_perm_set.add_argument("content_type", choices=["bookshelf", "book", "chapter", "page"], help="Content type")
    sp_perm_set.add_argument("content_id", type=int, help="Content item ID")
    sp_perm_set.add_argument("--role-id", type=int, help="Target Role ID to grant/restrict")
    sp_perm_set.add_argument("--view", action=argparse.BooleanOptionalAction, help="View permission")
    sp_perm_set.add_argument("--create", action=argparse.BooleanOptionalAction, help="Create permission")
    sp_perm_set.add_argument("--update", action=argparse.BooleanOptionalAction, help="Update permission")
    sp_perm_set.add_argument("--delete", action=argparse.BooleanOptionalAction, help="Delete permission")
    sp_perm_set.add_argument("--fallback-inherits", action=argparse.BooleanOptionalAction, help="Fallback to role inheritance")

    # --- Users ---
    p_users = subparsers.add_parser("users", help="Manage users")
    add_listing_args(p_users)
    sp_users = p_users.add_subparsers(dest="action")

    sp_users_list = sp_users.add_parser("list", help="List users")
    add_listing_args(sp_users_list)

    sp_users_get = sp_users.add_parser("get", help="Get user details")
    sp_users_get.add_argument("id", type=int, help="User ID")

    sp_users_create = sp_users.add_parser("create", help="Create a user")
    sp_users_create.add_argument("--name", required=True, help="User display name")
    sp_users_create.add_argument("--email", required=True, help="User email")
    sp_users_create.add_argument("--password", help="User password")
    sp_users_create.add_argument("--roles", help="Comma-separated role IDs")
    sp_users_create.add_argument("--send-invite", action="store_true", help="Send invite email")

    sp_users_update = sp_users.add_parser("update", help="Update a user")
    sp_users_update.add_argument("id", type=int, help="User ID")
    sp_users_update.add_argument("--name", help="User display name")
    sp_users_update.add_argument("--email", help="User email")
    sp_users_update.add_argument("--password", help="User password")
    sp_users_update.add_argument("--roles", help="Comma-separated role IDs")
    sp_users_update.add_argument("--language", help="Language code")

    sp_users_delete = sp_users.add_parser("delete", help="Delete a user")
    sp_users_delete.add_argument("id", type=int, help="User ID")
    sp_users_delete.add_argument("--migrate-ownership-id", type=int, help="User ID to migrate content ownership to")

    # --- Roles ---
    p_roles = subparsers.add_parser("roles", help="View user roles")
    add_listing_args(p_roles)
    sp_roles = p_roles.add_subparsers(dest="action")
    sp_roles_list = sp_roles.add_parser("list", help="List roles")
    add_listing_args(sp_roles_list)
    sp_roles_get = sp_roles.add_parser("get", help="Get role details")
    sp_roles_get.add_argument("id", type=int, help="Role ID")

    # --- Audit Log ---
    p_audit = subparsers.add_parser("audit-log", help="View system activity audit log")
    add_listing_args(p_audit)
    sp_audit = p_audit.add_subparsers(dest="action")
    sp_audit_list = sp_audit.add_parser("list", help="List audit log entries")
    add_listing_args(sp_audit_list)

    # --- Recycle Bin ---
    p_recycle = subparsers.add_parser("recycle-bin", help="View and manage soft-deleted items")
    add_listing_args(p_recycle)
    sp_recycle = p_recycle.add_subparsers(dest="action")
    sp_recycle_list = sp_recycle.add_parser("list", help="List recycle bin items")
    add_listing_args(sp_recycle_list)
    sp_recycle_restore = sp_recycle.add_parser("restore", help="Restore deleted item")
    sp_recycle_restore.add_argument("id", type=int, help="Deletion record ID")
    sp_recycle_destroy = sp_recycle.add_parser("destroy", help="Permanently purge item")
    sp_recycle_destroy.add_argument("id", type=int, help="Deletion record ID")

    # --- Tags ---
    p_tags = subparsers.add_parser("tags", help="Explore system tags")
    sp_tags = p_tags.add_subparsers(dest="action", required=True)
    sp_tags.add_parser("names", help="List all tag names")
    sp_tags_val = sp_tags.add_parser("values", help="List values for a specific tag name")
    sp_tags_val.add_argument("--name", required=True, help="Tag name")

    return parser


# -----------------------------------------------------------------------------
# Main Dispatch
# -----------------------------------------------------------------------------

def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    try:
        client = BookStackClient(
            base_url=args.base_url,
            token_id=args.token_id,
            token_secret=args.token_secret,
            verify_ssl=not args.insecure,
            debug=args.debug,
        )

        dispatch_map = {
            "status": cmd_status,
            "search": cmd_search,
            "shelves": cmd_shelves,
            "books": cmd_books,
            "chapters": cmd_chapters,
            "pages": cmd_pages,
            "attachments": cmd_attachments,
            "images": cmd_images,
            "comments": cmd_comments,
            "permissions": cmd_permissions,
            "users": cmd_users,
            "roles": cmd_roles,
            "audit-log": cmd_audit,
            "recycle-bin": cmd_recycle,
            "tags": cmd_tags,
        }

        handler = dispatch_map.get(args.command)
        if handler:
            handler(client, args)
        else:
            parser.print_help()

    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2), file=sys.stderr)
        else:
            print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
