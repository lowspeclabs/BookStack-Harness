"""
Harness-Side Goal Tracker & Plan Manager for BookStack AI Agent.

Provides robust multi-step goal decomposition, checklist management,
state persistence across turn interrupts/resteering, terminal UI widgets,
and dynamic context injection for LLM prompts.
"""

import os
import re
import sys
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Union


# ANSI Color / Formatting Constants
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GRAY = "\033[90m"
C_CYAN = "\033[36m"
C_BLUE = "\033[34m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_MAGENTA = "\033[35m"
C_RED = "\033[31m"
C_WHITE = "\033[37m"

STATUS_ICONS = {
    "completed": f"{C_GREEN}✓{C_RESET}",
    "in_progress": f"{C_YELLOW}{C_BOLD}▶{C_RESET}",
    "pending": f"{C_GRAY}○{C_RESET}",
    "failed": f"{C_RED}{C_BOLD}✗{C_RESET}",
    "skipped": f"{C_GRAY}−{C_RESET}",
}

ACTION_VERBS = [
    "read", "review", "check", "inspect", "summarize",
    "find", "search", "locate", "lookup", "query",
    "update", "modify", "edit", "patch", "append",
    "create", "add", "import", "generate", "write",
    "link", "cross-link", "connect", "reference",
    "export", "backup", "download", "archive",
    "delete", "remove", "clean",
]


class GoalTracker:
    """
    Harness-side tracker for user goals, sub-tasks, discovered entities,
    and uninterrupted workflow progression.
    """

    def __init__(self, logger: Optional[Any] = None):
        self.logger = logger
        self.goal: Optional[str] = None
        self.steps: List[Dict[str, Any]] = []
        self.context: Dict[str, Any] = {}
        self.last_user_prompt: Optional[str] = None
        self.active_attachments: List[Dict[str, Any]] = []
        self.last_resteer_prompt: Optional[str] = None
        self.interrupted: bool = False
        self.created_at: str = datetime.now().isoformat()
        self.updated_at: str = self.created_at

    def is_active(self) -> bool:
        """Returns True if there is an active goal or pending steps."""
        return bool(self.goal or self.steps)

    def mark_interrupted(self):
        """Marks that the active generation was interrupted by the user."""
        self.interrupted = True
        self.updated_at = datetime.now().isoformat()
        if self.logger:
            self.logger.log("GOAL_INTERRUPTED", f"Goal execution interrupted for '{self.goal}'")

    def clear_interrupted(self):
        """Clears the interrupted flag."""
        self.interrupted = False

    def is_interrupted(self) -> bool:
        """Returns True if previous turn was interrupted."""
        return self.interrupted

    def set_goal(
        self,
        goal: str,
        steps: Optional[List[Union[str, Dict[str, Any]]]] = None,
        context: Optional[Dict[str, Any]] = None,
        raw_prompt: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ):
        """Sets or replaces the active goal and optional step list."""
        self.goal = goal.strip()
        self.updated_at = datetime.now().isoformat()
        if raw_prompt:
            self.last_user_prompt = raw_prompt
        if attachments is not None:
            self.active_attachments = attachments
        if context:
            self.context.update(context)

        self.steps = []
        if steps:
            for idx, s in enumerate(steps, 1):
                if isinstance(s, str):
                    self.steps.append({
                        "id": idx,
                        "description": s.strip(),
                        "status": "pending",
                        "notes": "",
                    })
                elif isinstance(s, dict):
                    self.steps.append({
                        "id": s.get("id", idx),
                        "description": s.get("description", f"Step {idx}"),
                        "status": s.get("status", "pending"),
                        "notes": s.get("notes", ""),
                    })

        if self.logger:
            self.logger.log("GOAL_SET", f"Active goal set: {self.goal}", {"goal": self.goal, "steps": self.steps})

    def auto_init_from_prompt(
        self,
        user_input: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """
        Auto-detects multi-step goals from user prompts (or attached file instructions)
        and creates an organized step checklist.
        """
        if not user_input or len(user_input.strip()) < 15:
            return False

        # If an active goal already exists and is not completed, don't overwrite unless prompt has new multi-step clauses
        cleaned_text = re.sub(r"\[ATTACHMENT:[^\]]+\][\s\S]*?\[/ATTACHMENT\]", "", user_input).strip()
        cleaned_text = re.sub(r"### 📄 Attached Local File:[\s\S]*?```\n", "", cleaned_text).strip()
        if not cleaned_text:
            return False

        # 1. Numbered lists: 1. Step one 2. Step two
        numbered = re.findall(r"(?:^|\n)\s*\d+[\.\)]\s*([^\n]+)", cleaned_text)
        if len(numbered) >= 2:
            steps = [n.strip() for n in numbered if len(n.strip()) > 3]
            goal_summary = cleaned_text.split("\n")[0].strip()
            if len(goal_summary) > 90:
                goal_summary = goal_summary[:87] + "..."
            self.set_goal(
                goal=goal_summary,
                steps=steps,
                raw_prompt=user_input,
                attachments=attachments,
            )
            return True

        # 2. Bullet lists: - Step one \n - Step two
        bullets = re.findall(r"(?:^|\n)\s*[-*•]\s*([^\n]+)", cleaned_text)
        if len(bullets) >= 2:
            steps = [b.strip() for b in bullets if len(b.strip()) > 3]
            goal_summary = cleaned_text.split("\n")[0].strip()
            if len(goal_summary) > 90:
                goal_summary = goal_summary[:87] + "..."
            self.set_goal(
                goal=goal_summary,
                steps=steps,
                raw_prompt=user_input,
                attachments=attachments,
            )
            return True

        # 3. Comma / 'and then' / 'then' clause splitting for imperative verbs
        # Example: "read this file, find codeser page and update it host info, create codeserver app page and update it with info from file, link codeserver server to code server app page and vise versa"
        verb_pattern = "|".join(ACTION_VERBS)
        parts = re.split(
            rf",\s*(?=(?:{verb_pattern})\b)|\b(?:then|and\s+then)\s+(?=(?:{verb_pattern})\b)|\band\s+(?=(?:update|create|link|find|export|read|delete)\b)",
            cleaned_text,
            flags=re.IGNORECASE,
        )

        candidates = [p.strip(" \t\n.;-") for p in parts if len(p.strip(" \t\n.;-")) > 6]
        if len(candidates) >= 2:
            # Format candidate steps into clean sentences
            steps = []
            for c in candidates:
                # Capitalize first character
                c_clean = c[0].upper() + c[1:] if c else c
                steps.append(c_clean)

            goal_summary = cleaned_text.split("\n")[0].strip()
            if len(goal_summary) > 90:
                goal_summary = goal_summary[:87] + "..."

            self.set_goal(
                goal=goal_summary,
                steps=steps,
                raw_prompt=user_input,
                attachments=attachments,
            )
            return True

        # If not multi-step, still record prompt & attachments as primary objective for context & resteering
        goal_summary = cleaned_text.split("\n")[0].strip()
        if len(goal_summary) > 120:
            goal_summary = goal_summary[:117] + "..."
        self.goal = goal_summary
        self.last_user_prompt = user_input
        if attachments is not None:
            self.active_attachments = attachments
        return False

    def add_step(self, description: str, notes: str = "") -> int:
        """Adds a new step to the active plan."""
        idx = len(self.steps) + 1
        self.steps.append({
            "id": idx,
            "description": description.strip(),
            "status": "pending",
            "notes": notes,
        })
        self.updated_at = datetime.now().isoformat()
        if self.logger:
            self.logger.log("GOAL_STEP_ADD", f"Added step #{idx}: {description}", {"id": idx, "description": description})
        return idx

    def update_step(
        self,
        step_id_or_idx: int,
        status: str,
        notes: Optional[str] = None,
    ) -> bool:
        """Updates status ('pending', 'in_progress', 'completed', 'failed', 'skipped') of a step."""
        target = None
        for s in self.steps:
            if s.get("id") == step_id_or_idx:
                target = s
                break
        if not target and 1 <= step_id_or_idx <= len(self.steps):
            target = self.steps[step_id_or_idx - 1]

        if target:
            target["status"] = status
            if notes is not None:
                target["notes"] = notes
            self.updated_at = datetime.now().isoformat()
            if self.logger:
                self.logger.log("GOAL_STEP_UPDATE", f"Step #{target.get('id')} -> {status}", {"step": target})
            return True
        return False

    def complete_step(self, step_id_or_idx: int, notes: Optional[str] = None) -> bool:
        """Convenience method to mark a step as completed."""
        return self.update_step(step_id_or_idx, "completed", notes=notes)

    def set_context(self, key_or_dict: Union[str, Dict[str, Any]], value: Any = None):
        """Stores discovered entities/IDs in harness context."""
        if isinstance(key_or_dict, dict):
            self.context.update(key_or_dict)
        elif isinstance(key_or_dict, str):
            self.context[key_or_dict] = value
        self.updated_at = datetime.now().isoformat()

    def clear(self):
        """Resets the goal tracker entirely."""
        self.goal = None
        self.steps = []
        self.context = {}
        self.last_user_prompt = None
        self.active_attachments = []
        self.last_resteer_prompt = None
        self.interrupted = False
        self.updated_at = datetime.now().isoformat()
        if self.logger:
            self.logger.log("GOAL_CLEARED", "Goal tracker reset")

    def get_next_pending_step(self) -> Optional[Dict[str, Any]]:
        """Returns the first step that is in_progress or pending."""
        for s in self.steps:
            if s.get("status") in ("in_progress", "pending"):
                return s
        return None

    def get_resteer_prompt(self, user_directive: str) -> str:
        """
        Builds a comprehensive prompt when the user provides a re-steering instruction
        (e.g., 'try one page at a time') after an interrupt or turn pause,
        ensuring original goal and attachments are never lost.
        """
        self.last_resteer_prompt = user_directive
        self.interrupted = False

        parts = [
            f"### 🎛️ USER RE-STEER DIRECTIVE\n\"{user_directive}\"\n",
            "### 🎯 ONGOING ACTIVE GOAL & CONTEXT",
        ]
        objective = self.goal or self.last_user_prompt or "Prior active task"
        parts.append(f"**Primary Objective:** {objective}")
        if self.last_user_prompt and self.last_user_prompt != objective:
            parts.append(f"**Original Request:** \"{self.last_user_prompt}\"")

        if self.steps:
            parts.append("\n**Current Plan & Progress:**")
            for s in self.steps:
                st = s.get("status", "pending").upper()
                notes = f" (Notes: {s['notes']})" if s.get("notes") else ""
                parts.append(f"- [{st}] Step #{s.get('id')}: {s.get('description')}{notes}")

        if self.context:
            parts.append(f"\n**Discovered Entities / Context:** `{json.dumps(self.context)}`")

        if self.active_attachments:
            att_names = [a.get("rel_path", "attachment") for a in self.active_attachments]
            parts.append(f"\n**Active Attached File(s):** {', '.join(att_names)}")

        parts.append(
            "\n**EXECUTION INSTRUCTION:** Continue directly towards completing the goal above, strictly adhering to the user's re-steer directive (e.g. working sequentially, one page at a time). Start immediately with the next single step."
        )

        return "\n".join(parts)

    def render_panel(self, compact: bool = False) -> str:
        """Renders an ANSI terminal panel widget showing the goal and checklist."""
        if not self.goal and not self.steps:
            return ""

        term_width = 70
        try:
            import shutil
            cols = shutil.get_terminal_size((80, 24)).columns
            term_width = max(40, min(cols - 4, 85))
        except Exception:
            pass

        bar_len = max(10, term_width - 24)
        top_bar = f"{C_GRAY}┌─{C_RESET} {C_MAGENTA}{C_BOLD}🎯 Harness Goal Plan{C_RESET} {C_GRAY}{'─' * bar_len}{C_RESET}"
        bot_bar = f"{C_GRAY}└{'─' * (term_width - 2)}{C_RESET}"

        lines = [top_bar]

        if self.goal:
            lines.append(f"{C_GRAY}│{C_RESET}  {C_BOLD}Goal:{C_RESET} {C_CYAN}{self.goal}{C_RESET}")

        if self.steps:
            if not compact:
                lines.append(f"{C_GRAY}│{C_RESET}  {C_DIM}Checklist:{C_RESET}")
            for s in self.steps:
                icon = STATUS_ICONS.get(s.get("status", "pending"), "○")
                desc = s.get("description", "")
                st = s.get("status", "pending")
                color = C_GREEN if st == "completed" else (f"{C_YELLOW}{C_BOLD}" if st == "in_progress" else C_WHITE)
                notes = f" {C_GRAY}({s['notes']}){C_RESET}" if s.get("notes") else ""
                lines.append(f"{C_GRAY}│{C_RESET}    {icon} {C_GRAY}#{s.get('id')}{C_RESET} {color}{desc}{C_RESET}{notes}")

        if self.context and not compact:
            ctx_items = [f"{k}={v}" for k, v in self.context.items()]
            lines.append(f"{C_GRAY}│{C_RESET}  {C_GRAY}Context: {', '.join(ctx_items)}{C_RESET}")

        lines.append(bot_bar)
        return "\n".join(lines)

    def to_prompt_context(self) -> str:
        """Returns structured goal context for injection into LLM system or user turns."""
        if not self.is_active():
            return ""

        parts = ["[HARNESS GOAL TRACKER & EXECUTION PLAN]"]
        if self.goal:
            parts.append(f"Active Primary Goal: {self.goal}")

        if self.steps:
            parts.append("Current Sub-Tasks Checklist:")
            for s in self.steps:
                st = s.get("status", "pending").upper()
                notes_str = f" [Notes: {s['notes']}]" if s.get("notes") else ""
                parts.append(f"  - [{st}] Step #{s.get('id')}: {s.get('description')}{notes_str}")

        if self.context:
            parts.append(f"Discovered Entities & Context: {json.dumps(self.context)}")

        parts.append(
            "EXECUTION DIRECTIVE: Work sequentially. Execute ONE step at a time, update its status via `manage_plan`, and verify before proceeding to the next step. DO NOT attempt to batch all writes/reads simultaneously."
        )
        return "\n".join(parts)

    def handle_manage_plan_tool(self, args: Dict[str, Any]) -> str:
        """Executes the `manage_plan` tool call requested by the LLM."""
        action = args.get("action", "").strip().lower()

        if action == "create":
            goal = args.get("goal") or "Execute multi-step task"
            steps = args.get("steps", [])
            context = args.get("context", {})
            self.set_goal(goal=goal, steps=steps, context=context)
            # Live visual panel update
            print(f"\n{self.render_panel()}\n")
            return json.dumps({
                "status": "success",
                "action": "create",
                "goal": self.goal,
                "step_count": len(self.steps),
                "message": f"Plan initialized with {len(self.steps)} steps.",
            })

        elif action == "update_step":
            step_id = int(args.get("step_id", 1))
            status = args.get("status", "in_progress")
            notes = args.get("notes")
            ok = self.update_step(step_id, status, notes=notes)
            if ok:
                print(f"\n{self.render_panel()}\n")
                return json.dumps({"status": "success", "action": "update_step", "step_id": step_id, "new_status": status})
            return json.dumps({"status": "error", "message": f"Step #{step_id} not found."})

        elif action == "complete_step":
            step_id = int(args.get("step_id", 1))
            notes = args.get("notes")
            ok = self.complete_step(step_id, notes=notes)
            if ok:
                print(f"\n{self.render_panel()}\n")
                return json.dumps({"status": "success", "action": "complete_step", "step_id": step_id, "new_status": "completed"})
            return json.dumps({"status": "error", "message": f"Step #{step_id} not found."})

        elif action == "add_step":
            desc = args.get("description") or args.get("step") or "New step"
            notes = args.get("notes", "")
            new_id = self.add_step(desc, notes=notes)
            print(f"\n{self.render_panel()}\n")
            return json.dumps({"status": "success", "action": "add_step", "step_id": new_id, "description": desc})

        elif action == "set_context":
            ctx = args.get("context", {})
            self.set_context(ctx)
            return json.dumps({"status": "success", "action": "set_context", "context": self.context})

        elif action == "show":
            return json.dumps({
                "goal": self.goal,
                "steps": self.steps,
                "context": self.context,
                "is_active": self.is_active(),
            })

        elif action == "clear":
            self.clear()
            return json.dumps({"status": "success", "action": "clear", "message": "Goal plan cleared."})

        else:
            return json.dumps({"status": "error", "message": f"Unknown plan action: '{action}'"})

    def export_state(self) -> Dict[str, Any]:
        """Serializes tracker state for logs or persistent sessions."""
        return {
            "goal": self.goal,
            "steps": self.steps,
            "context": self.context,
            "last_user_prompt": self.last_user_prompt,
            "interrupted": self.interrupted,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def load_state(self, state: Dict[str, Any]):
        """Restores tracker state from serialized dictionary."""
        self.goal = state.get("goal")
        self.steps = state.get("steps", [])
        self.context = state.get("context", {})
        self.last_user_prompt = state.get("last_user_prompt")
        self.interrupted = state.get("interrupted", False)
        self.created_at = state.get("created_at", self.created_at)
        self.updated_at = state.get("updated_at", self.updated_at)


# -----------------------------------------------------------------------------
# Standalone CLI Demo / Self-Test
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    print("Testing GoalTracker...")
    tracker = GoalTracker()
    test_prompt = "read this file, find codeser page and update it host info, create codeserver app page and update it with info from file, link codeserver server to code server app page and vise versa"
    tracker.auto_init_from_prompt(test_prompt)
    print(tracker.render_panel())

    print("\nSimulating Step 1 completion:")
    tracker.complete_step(1, notes="Read codeserver-host-audit.md (13.5KB)")
    tracker.update_step(2, "in_progress")
    tracker.set_context({"server_page_id": 16, "app_book_id": 3})
    print(tracker.render_panel())

    print("\nSimulating Resteer Prompt:")
    resteer = tracker.get_resteer_prompt("try one page at a time")
    print(resteer)
    print("\nGoalTracker test complete.")
