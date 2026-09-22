# BookStack CLI & AI Chat Agent

A powerful, full-featured Python command-line interface and interactive AI assistant for interacting with the [BookStack](https://www.bookstackapp.com/) REST API. Designed for developers, sysadmins, and automated AI agents.

---

## Features

- 🔑 **Flexible Authentication**: Reads credentials from `.env`, environment variables, or CLI options.
- 🤖 **Interactive AI Assistant (`chat.py`)**: Conversational agent with full tool calling against BookStack (compatible with local LM Studio, Ollama, vLLM).
- 👥 **Human & Agent Friendly**: Formats results in clean readable text by default, or structured JSON with `--json` (`-j`).
- 🔄 **Idempotent Upserts**: `pages upsert` avoids duplicate pages in automation and sync pipelines.
- 📝 **Terminal Editing & Diff**: `pages edit` (opens in `$EDITOR`) and `pages diff` (terminal colored diffs).
- 📁 **Bulk Import & Export**: `pages import-dir` (import markdown directory) and `books export-tree` (export book to directory structure).
- 🔒 **Content Permissions**: Granular role-based permissions management (`permissions get/set`).
- ⚡ **Easy Setup**: Includes `setup.sh` to initialize Python virtual environments (`.venv`) and dependencies automatically via `uv` or standard Python.

---

## Installation & Setup

### 1. Run the Setup Script
The included setup script creates a virtual environment `.venv` and installs all required dependencies:

```bash
chmod +x setup.sh
./setup.sh
```

### 2. Configure Credentials

> **CRITICAL: DO NOT OVERWRITE EXISTING `.env`**  
> If an active `.env` file already exists, **do not overwrite or replace it**. The `setup.sh` script automatically checks and will never overwrite an existing `.env` file. Only create a `.env` if one does not already exist.

If configuring for the first time, copy `.env.example` to `.env`:

```bash
# Only if .env does NOT already exist:
cp -n .env.example .env
```

Ensure your `.env` contains valid instance credentials:

```ini
BOOKSTACK_BASE_URL=https://wiki.example.com
BOOKSTACK_TOKEN_ID=your_token_id_here
BOOKSTACK_TOKEN_SECRET=your_token_secret_here

# Optional: LLM Configuration for chat.py
LMSTUDIO_URL=http://127.0.0.1:1234/v1
LMSTUDIO_MODEL=your-model-name
```

---

## Quick Start

Activate the virtual environment or run directly through `.venv/bin/python`:

```bash
# Option A: Activate virtual environment
source .venv/bin/activate
python subscripts/bookstack.py status

# Option B: Run via .venv directly
.venv/bin/python subscripts/bookstack.py status
```

---

## Interactive AI Chat Agent (`chat.py`)

Run an interactive conversation with your wiki powered by your local LLM:

```bash
# Launch interactive REPL
./chat.py

# Or run one-shot queries directly from CLI
./chat.py "What IP address and role does HPZ6G4 have in our wiki?"
./chat.py "Create a troubleshooting page for Proxmox backup in the Servers book"
```

Inside the interactive chat session:
- `/model` or `/models` — View current model and list all available models on the endpoint
- `/model <model_name>` — Switch the active LLM model on-the-fly
- `/clear` — Reset conversation history
- `/exit` or `/quit` — Exit chat and display session summary

All chat sessions, model thinking phases, tool calls, and responses are automatically logged to the `logs/` directory (`logs/chat_<timestamp>.log` and `.jsonl`).

---

## Common Usage Examples

### 1. Check System Connection & Status
```bash
python subscripts/bookstack.py status
```

### 2. Search & Direct Find
```bash
# Search across all content
python subscripts/bookstack.py search "HPZ6G4"

# Direct lookup by name or slug
python subscripts/bookstack.py pages find "HPZ6G4"
python subscripts/bookstack.py books find "Servers"
```

### 3. Manage Shelves & Books
```bash
# List books (defaults to list without needing the subcommand)
python subscripts/bookstack.py books

# Export book to local directory tree of Markdown files
python subscripts/bookstack.py books export-tree 3 --output-dir ./backup_servers
```

### 4. Create, Upsert & Edit Pages
```bash
# Idempotent Upsert (Creates if new, Updates if page title exists)
python subscripts/bookstack.py pages upsert --book-id 3 --name "Docker Swarm" --markdown-file ./swarm.md

# Interactive terminal edit with $EDITOR
python subscripts/bookstack.py pages edit 6

# View diff between remote page and local file
python subscripts/bookstack.py pages diff 6 --markdown-file ./updated.md

# Batch import a directory of Markdown files
python subscripts/bookstack.py pages import-dir --book-id 3 --dir ./my-docs --recursive
```

### 5. Attachments, Images & Comments
```bash
# Upload a file attachment
python subscripts/bookstack.py attachments upload --page-id 6 --file ./spec.pdf --name "Spec Sheet"

# Upload an image to the gallery
python subscripts/bookstack.py images upload --page-id 6 --file ./diagram.png --name "Topology"

# Add a comment
python subscripts/bookstack.py comments create --page-id 6 --text "Verified connectivity"
```

---

## Command Reference Summary

- `status` - Healthcheck and instance info
- `search <query>` - Global full-text search
- `shelves` - `list`, `get`, `find`, `create`, `update`, `delete`
- `books` - `list`, `get`, `find`, `create`, `update`, `delete`, `export`, `export-tree`
- `chapters` - `list`, `get`, `create`, `update`, `delete`, `export`
- `pages` - `list`, `get`, `find`, `content`, `create`, `upsert`, `update`, `edit`, `diff`, `import-dir`, `delete`, `export`
- `attachments` - `list`, `get`, `upload`, `delete`
- `images` - `list`, `get`, `upload`, `download`, `delete`
- `comments` - `list`, `get`, `create`, `update`, `delete`
- `permissions` - `get`, `set`
- `users` - `list`, `get`, `create`, `update`, `delete`
- `roles` - `list`, `get`
- `audit-log` - `list`
- `recycle-bin` - `list`, `restore`, `destroy`
- `tags` - `names`, `values`

---

## Documentation

- [AGENTS.md](docs/AGENTS.md) - Agent guidelines and integration patterns
- [API_README.md](docs/API_README.md) - Complete REST API reference and payload specifications
