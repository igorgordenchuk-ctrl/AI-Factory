"""
AI Factory CLI — command-line interface.

Usage:
    python cli/main.py submit "Create a calculator in Python"
    python cli/main.py status
    python cli/main.py inspect task-a1b2c3d4
    python cli/main.py agents
"""

import json
import os
import sys
from pathlib import Path

# Fix Windows encoding for Unicode output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# Проект root
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.task_card import TaskCard, _now
from core.state_machine import StateMachine

console = Console()
PIPELINE_ROOT = BASE_DIR / "pipeline"


@click.group()
def cli():
    """AI Factory -- Multi-agent task orchestration system."""
    pass


@cli.command()
@click.argument("description")
@click.option("--title", "-t", default="", help="Short task title")
@click.option("--priority", "-p", default=5, help="Priority 1-10 (1=highest)")
@click.option("--tags", default="", help="Comma-separated tags")
def submit(description: str, title: str, priority: int, tags: str):
    """Submit a new task to the factory."""
    # Формируем заголовок из описания если не указан
    if not title:
        title = description[:80]

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    task = TaskCard(
        title=title,
        description=description,
        priority=priority,
        tags=tag_list,
        status="new",
        stage_folder="0_inbox",
    )

    # Сохраняем в inbox
    inbox = PIPELINE_ROOT / "0_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    path = task.save(inbox)

    console.print(Panel.fit(
        f"[bold green]Задача создана[/bold green]\n"
        f"ID: {task.id}\n"
        f"Заголовок: {task.title}\n"
        f"Приоритет: {task.priority}\n"
        f"Файл: {path}",
        border_style="green",
    ))
    console.print(
        "[dim]Бригадир подберёт эту задачу из pipeline/0_inbox/ автоматически.[/dim]"
    )


@cli.command()
def status():
    """Show current pipeline status."""
    sm = StateMachine(PIPELINE_ROOT)
    summary = sm.get_pipeline_summary()

    table = Table(title="Pipeline Status", show_header=True)
    table.add_column("Стадия", style="cyan")
    table.add_column("Папка", style="dim")
    table.add_column("Задач", justify="right", style="green")

    labels = {
        "0_inbox": "Входящие",
        "1_planning": "Планирование",
        "2_ready": "Готовы",
        "3_in_progress": "В работе",
        "4_review": "На проверке",
        "5_rework": "Доработка",
        "6_completed": "Выполнено",
        "7_archived": "Архив",
    }

    total = 0
    for stage, count in summary.items():
        label = labels.get(stage, stage)
        table.add_row(label, stage, str(count))
        total += count

    table.add_row("", "[bold]ВСЕГО[/bold]", f"[bold]{total}[/bold]")
    console.print(table)

    # Стоимость
    dashboard_path = BASE_DIR / "registry" / "dashboard.json"
    if dashboard_path.exists():
        data = json.loads(dashboard_path.read_text(encoding="utf-8"))
        costs = data.get("costs", {})
        if costs:
            console.print(
                f"\n  Стоимость: ${costs.get('cost_usd', 0):.4f} | "
                f"Токены: {costs.get('input_tokens', 0)}in / {costs.get('output_tokens', 0)}out | "
                f"API вызовов: {costs.get('api_calls', 0)}"
            )


@cli.command()
@click.argument("task_id")
def inspect(task_id: str):
    """Show detailed info about a specific task."""
    # Ищем задачу во всех папках пайплайна
    for stage_dir in sorted(PIPELINE_ROOT.iterdir()):
        if stage_dir.is_dir():
            task_file = stage_dir / f"{task_id}.json"
            if task_file.exists():
                task = TaskCard.load(task_file)
                _print_task_detail(task)
                return

    console.print(f"[red]Задача {task_id} не найдена[/red]")


def _print_task_detail(task: TaskCard):
    """Print detailed task information."""
    console.print(Panel.fit(
        f"[bold]{task.title}[/bold]\n"
        f"ID: {task.id} | Проект: {task.project_id}\n"
        f"Статус: [cyan]{task.status}[/cyan] | Папка: {task.stage_folder}\n"
        f"Приоритет: {task.priority} | Попытка: {task.attempt}/{task.max_attempts}",
        border_style="blue",
    ))

    if task.description:
        console.print(f"\n[bold]Описание:[/bold]\n{task.description}")

    if task.acceptance_criteria:
        console.print("\n[bold]Критерии приёмки:[/bold]")
        for i, ac in enumerate(task.acceptance_criteria, 1):
            console.print(f"  {i}. {ac}")

    if task.required_skills:
        console.print(f"\n[bold]Навыки:[/bold] {', '.join(task.required_skills)}")

    if task.depends_on:
        console.print(f"[bold]Зависит от:[/bold] {', '.join(task.depends_on)}")

    if task.subtask_ids:
        console.print(f"[bold]Подзадачи:[/bold] {', '.join(task.subtask_ids)}")

    if task.output_files:
        console.print(f"\n[bold]Выходные файлы:[/bold]")
        for f in task.output_files[:20]:
            console.print(f"  {f}")

    if task.review_notes:
        console.print("\n[bold]Ревью:[/bold]")
        for rn in task.review_notes:
            color = "green" if rn.verdict == "APPROVED" else "red"
            console.print(f"  [{color}]{rn.verdict}[/{color}] Score: {rn.score}")
            for note in rn.notes:
                console.print(f"    - {note}")

    if task.cost > 0:
        console.print(f"\n[bold]Стоимость:[/bold] ${task.cost:.4f}")


@cli.command()
def agents():
    """List all registered agents."""
    agents_path = BASE_DIR / "registry" / "agents.json"
    if not agents_path.exists():
        console.print("[dim]Нет зарегистрированных агентов[/dim]")
        return

    data = json.loads(agents_path.read_text(encoding="utf-8"))
    agent_list = data.get("agents", [])

    if not agent_list:
        console.print("[dim]Нет зарегистрированных агентов[/dim]")
        return

    table = Table(title="Agents", show_header=True)
    table.add_column("ID", style="cyan")
    table.add_column("Имя")
    table.add_column("Тип")
    table.add_column("Навыки")
    table.add_column("Статус", justify="center")
    table.add_column("Выполнено", justify="right")

    for a in agent_list:
        status_color = "green" if a["status"] == "idle" else "yellow"
        table.add_row(
            a["agent_id"],
            a["name"],
            a["type"],
            ", ".join(a.get("skills", [])),
            f"[{status_color}]{a['status']}[/{status_color}]",
            str(a.get("tasks_completed", 0)),
        )

    console.print(table)


@cli.command()
def tasks():
    """List all tasks across pipeline stages."""
    sm = StateMachine(PIPELINE_ROOT)

    table = Table(title="All Tasks", show_header=True)
    table.add_column("ID", style="cyan")
    table.add_column("Заголовок")
    table.add_column("Стадия")
    table.add_column("Навыки")
    table.add_column("Приоритет", justify="right")
    table.add_column("Стоимость", justify="right")

    stages = [
        "0_inbox", "1_planning", "2_ready", "3_in_progress",
        "4_review", "5_rework", "6_completed",
    ]

    for stage in stages:
        for task in sm.get_tasks(stage):
            stage_colors = {
                "0_inbox": "white",
                "1_planning": "blue",
                "2_ready": "cyan",
                "3_in_progress": "yellow",
                "4_review": "magenta",
                "5_rework": "red",
                "6_completed": "green",
            }
            color = stage_colors.get(stage, "white")
            table.add_row(
                task.id,
                task.title[:40],
                f"[{color}]{stage}[/{color}]",
                ", ".join(task.required_skills),
                str(task.priority),
                f"${task.cost:.4f}" if task.cost > 0 else "-",
            )

    console.print(table)


if __name__ == "__main__":
    cli()
