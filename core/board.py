"""
Board — generates and updates BOARD.md with task status table.
Auto-refreshed on every pipeline transition.
"""

from datetime import datetime, timezone
from pathlib import Path

from core.state_machine import StateMachine
from core.task_card import TaskCard


# Emoji-статусы для MD
STATUS_ICONS = {
    "0_inbox":       "📥 Inbox",
    "1_planning":    "📋 Planning",
    "2_ready":       "⏳ Ready",
    "3_in_progress": "🔨 In Progress",
    "4_review":      "🔍 Review",
    "5_rework":      "🔄 Rework",
    "6_completed":   "✅ Done",
    "7_archived":    "📦 Archived",
}


def generate_board(pipeline_root: str | Path, board_path: str | Path = "BOARD.md"):
    """Generate BOARD.md with current pipeline state."""
    sm = StateMachine(pipeline_root)
    summary = sm.get_pipeline_summary()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# 📊 AI Factory — Task Board",
        "",
        f"> Last updated: {now}",
        "",
    ]

    # ── Pipeline summary bar ──
    total = sum(summary.values())
    done = summary.get("6_completed", 0) + summary.get("7_archived", 0)
    pct = int(done / total * 100) if total > 0 else 0
    bar_filled = pct // 5
    bar_empty = 20 - bar_filled
    progress_bar = "█" * bar_filled + "░" * bar_empty

    lines.append(f"**Progress:** `{progress_bar}` {pct}% ({done}/{total} tasks)")
    lines.append("")

    # ── Summary counters ──
    parts = []
    for stage, icon in STATUS_ICONS.items():
        count = summary.get(stage, 0)
        if count > 0 or stage in ("0_inbox", "3_in_progress", "6_completed"):
            parts.append(f"{icon}: **{count}**")
    lines.append(" | ".join(parts))
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── Kanban columns (compact) ──
    stages_to_show = [
        "0_inbox", "1_planning", "2_ready", "3_in_progress",
        "4_review", "5_rework", "6_completed",
    ]

    # Собираем все задачи
    all_tasks: list[tuple[str, TaskCard]] = []
    for stage in stages_to_show:
        for task in sm.get_tasks(stage):
            all_tasks.append((stage, task))

    if not all_tasks:
        lines.append("*No tasks yet. Submit one:*")
        lines.append("```")
        lines.append('python cli/main.py submit "Your task description"')
        lines.append("```")
    else:
        # ── Main task table ──
        lines.append("## 📋 All Tasks")
        lines.append("")
        lines.append("| Status | ID | Title | Skills | Pri | Score | Cost | Agent |")
        lines.append("|--------|-----|-------|--------|-----|-------|------|-------|")

        for stage, task in all_tasks:
            icon = STATUS_ICONS.get(stage, stage)
            # Укорачиваем
            title = task.title[:35] + "…" if len(task.title) > 35 else task.title
            skills = ", ".join(task.required_skills) if task.required_skills else "-"
            score = f"{task.review_score:.0f}/10" if task.review_score > 0 else "-"
            cost = f"${task.cost:.3f}" if task.cost > 0 else "-"
            agent = task.assigned_agent_id[:12] if task.assigned_agent_id else "-"
            task_id = task.id[-8:]  # last 8 chars

            lines.append(
                f"| {icon} | `{task_id}` | {title} | {skills} | {task.priority} | {score} | {cost} | {agent} |"
            )

        lines.append("")

        # ── Active tasks detail ──
        active = [(s, t) for s, t in all_tasks if s in ("3_in_progress", "4_review", "5_rework")]
        if active:
            lines.append("## 🔨 Active Tasks")
            lines.append("")
            for stage, task in active:
                icon = STATUS_ICONS.get(stage, stage)
                lines.append(f"### {icon} {task.title}")
                lines.append(f"- **ID:** `{task.id}`")
                lines.append(f"- **Agent:** {task.assigned_agent_id or 'unassigned'}")
                if task.description:
                    desc = task.description[:200] + "…" if len(task.description) > 200 else task.description
                    lines.append(f"- **Description:** {desc}")
                if task.review_notes:
                    last = task.review_notes[-1]
                    lines.append(f"- **Last Review:** {last.verdict} (score: {last.score})")
                    for note in last.notes[:3]:
                        lines.append(f"  - {note}")
                lines.append("")

        # ── Recently completed ──
        completed = [(s, t) for s, t in all_tasks if s == "6_completed"]
        if completed:
            lines.append("## ✅ Completed")
            lines.append("")
            for _, task in completed[-10:]:  # last 10
                score_str = f" — score: {task.review_score:.0f}/10" if task.review_score > 0 else ""
                cost_str = f" — ${task.cost:.3f}" if task.cost > 0 else ""
                lines.append(f"- ~~{task.title}~~{score_str}{cost_str}")
            lines.append("")

    # ── Cost summary ──
    total_cost = sum(t.cost for _, t in all_tasks)
    if total_cost > 0:
        lines.append("---")
        lines.append(f"**Total cost:** ${total_cost:.4f}")
        lines.append("")

    # Write file
    board_file = Path(board_path)
    board_file.write_text("\n".join(lines), encoding="utf-8")
    return board_file
