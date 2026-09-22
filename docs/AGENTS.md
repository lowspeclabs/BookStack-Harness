# AGENTS.md — BookStack CLI Agent & Automation Guide

This guide is intended for AI coding agents, autonomous agents, and headless scripts interacting with BookStack via `bookstack.py`.

---

## 1. Execution Guidelines for Agents

### Interpreter Path
Always invoke the CLI using the dedicated virtual environment Python interpreter to avoid environment mismatch:

```bash
/home/svaye/Scripts/BookStack/.venv/bin/python /home/svaye/Scripts/BookStack/bookstack.py <command> [options]
```

### JSON Mode
Always supply `--json` (or `-j`) when invoking commands programmatically. This ensures deterministic, parseable JSON on `stdout`:

```bash
/home/svaye/Scripts/BookStack/.venv/bin/python /home/svaye/Scripts/BookStack/bookstack.py --json <command>
```

### Credentials & Environment (.env Preservation)
- **CRITICAL: NEVER overwrite or delete an existing `.env` file**.
- Always read existing credentials from `/home/svaye/Scripts/BookStack/.env` or trust the CLI's automatic environment variable loader.

### Exit Codes
- `0`: Command succeeded. JSON result is on `stdout`.
- `1`: API error, missing credentials, or client runtime error. Error object `{"error": "..."}` is emitted on `stderr` (or formatted message if `--json` was not passed).
- `2`: CLI argument validation or syntax error from `argparse`.

---

## 2. Core Agent Workflows

### 2.1 Health Check & Connection Validation
Before performing bulk operations, verify the connection:

```bash
.venv/bin/python bookstack.py --json status
```

**Response Schema**:
```json
{
  "app_version": "v24.05",
  "app_name": "BookStack",
  "php_version": "8.2.18",
  "system_id": "..."
}
```

---

### 2.2 Finding & Reading Existing Documentation

#### Search Knowledge Base
```bash
.venv/bin/python bookstack.py --json search "authentication flow" --count 5
```

**Response Schema**:
```json
{
  "data": [
    {
      "id": 42,
      "name": "OAuth2 Authentication Setup",
      "slug": "oauth2-authentication-setup",
      "type": "page",
      "book_id": 10,
      "preview_html": { "name": "...", "content": "..." },
      "tags": [{"name": "auth", "value": "oauth2"}]
    }
  ],
  "total": 1
}
```

#### Fetch Raw Page Content
To extract the exact Markdown content of a page without HTML formatting overhead:

```bash
.venv/bin/python bookstack.py pages content 42 --format markdown
```

For large pages, page through content using `--offset` and `--limit`:

```bash
# Read second chunk of 4000 characters starting at offset 4000
.venv/bin/python bookstack.py pages content 42 --offset 4000 --limit 4000
```

Or get full metadata and content (supports `--offset` and `--limit` with pagination metadata):

```bash
.venv/bin/python bookstack.py --json pages get 42 --offset 0 --limit 4000
```

#### Direct Lookup by Name or Slug
```bash
.venv/bin/python bookstack.py --json pages find "Kubernetes Setup" --book-id 5
```

---

### 2.3 Creating & Updating Content

#### Idempotent Upsert (Recommended for Agents)
To avoid duplicate pages and branching logic, use `pages upsert`:

```bash
.venv/bin/python bookstack.py --json pages upsert \
  --book-id 5 \
  --name "API Integration Guide" \
  --markdown-file /path/to/content.md \
  --tags "category:api" "status:active"
```

#### Create Page from File
```bash
.venv/bin/python bookstack.py --json pages create \
  --book-id 5 \
  --name "API Integration Guide" \
  --markdown-file /path/to/content.md \
  --tags "category:api" "status:active"
```

#### Create Page via Standard Input (Heredoc / Pipe)
When generating content on-the-fly, stream directly into stdin:

```bash
cat << 'EOF' | .venv/bin/python bookstack.py --json pages upsert --book-id 5 --name "Release Notes 2.0"
# Release Notes 2.0
- Added automated sync.
- Improved indexing.
EOF
```

#### Update Page Content
```bash
.venv/bin/python bookstack.py --json pages update 42 \
  --name "Updated API Integration Guide" \
  --markdown-file /path/to/updated.md \
  --changelog "Updated endpoint signatures"
```

---

### 2.4 Managing Books and Chapters

#### Create Hierarchy
```bash
# 1. Create a Book
BOOK_JSON=$(.venv/bin/python bookstack.py --json books create --name "System Architecture" --description "Internal system diagrams")
BOOK_ID=$(echo "$BOOK_JSON" | jq -r '.id')

# 2. Create a Chapter in the Book
CHAPTER_JSON=$(.venv/bin/python bookstack.py --json chapters create --book-id "$BOOK_ID" --name "Authentication Services")
CHAPTER_ID=$(echo "$CHAPTER_JSON" | jq -r '.id')

# 3. Create a Page under the Chapter
.venv/bin/python bookstack.py --json pages create \
  --chapter-id "$CHAPTER_ID" \
  --name "JWT Token Verification" \
  --markdown "# JWT Details\nVerification keys and rotation policy."
```

---

### 2.5 Filtering & Pagination for Large Datasets

Listing endpoints (`pages`, `books`, `chapters`, `shelves`, `attachments`, `users`, `audit-log`) accept filter arrays:

```bash
# Filter pages by tag or name pattern
.venv/bin/python bookstack.py --json pages list \
  --count 50 \
  --offset 0 \
  --sort "-updated_at" \
  --filter "name:like=%deployment%"
```

#### Supported Filter Operators
- `name:like=%term%` (Contains)
- `created_at:gt=2024-01-01` (Date comparisons)
- `id:ne=5` (Not equal)
- `book_id=12` (Direct equality)

---

### 2.6 File & Image Uploads

#### Uploading Attachments
```bash
.venv/bin/python bookstack.py --json attachments upload \
  --page-id 42 \
  --file "/path/to/spec.pdf" \
  --name "OpenAPI Specification PDF"
```

#### Uploading Images to Gallery
```bash
.venv/bin/python bookstack.py --json images upload \
  --page-id 42 \
  --file "/path/to/diagram.png" \
  --name "Service Architecture"
```

---

## 3. Best Practices for LLM / Subagent Workflows

1. **Query Before Creating**: Always run a search (`bookstack.py --json search "<topic>"`) to avoid creating duplicate pages.
2. **Use Markdown Format**: Prefer Markdown (`--markdown` or `--markdown-file`) over raw HTML for cleaner revision histories in BookStack.
3. **Structured Tagging**: Tag pages consistently (e.g. `type:spec`, `system:auth`, `status:draft`) to enable automated filtering.
4. **Error Handling**: Check the shell return code `$?`. If non-zero, parse `stderr` for `{ "error": "<msg>" }`.
5. **Rate Limiting**: Default rate limit is 180 req/min. Introduce a small delay if executing large automated batch migrations.
6. **Output Management**: Avoid arbitrary byte slicing (like `head -c 4000`) on JSON responses as it splits tokens mid-string. Use `--count <N>` pagination or filter with `jq` instead.
