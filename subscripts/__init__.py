"""
BookStack Agent Subscripts Package.

Provides modular sub-systems for the BookStack Chat Agent & CLI:
- bookstack: Core REST API client and CLI interface.
- goal_tracker: Harness-side goal planning, auto-decomposition, and progress tracking.
- file_manager: File mention resolution, tab-completion, interactive selector, and export utilities.
"""

from .bookstack import BookStackClient
from .goal_tracker import GoalTracker
from .file_manager import (
    resolve_file_mentions,
    list_workspace_files,
    setup_file_completer,
    select_file_interactive,
    export_wiki_entity,
    export_page_md,
    export_book_tree,
    export_book_zip,
    export_shelf_zip,
)

__all__ = [
    "BookStackClient",
    "GoalTracker",
    "resolve_file_mentions",
    "list_workspace_files",
    "setup_file_completer",
    "select_file_interactive",
    "export_wiki_entity",
    "export_page_md",
    "export_book_tree",
    "export_book_zip",
    "export_shelf_zip",
]
