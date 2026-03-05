"""
AI Factory — entry point.
Initializes the factory: creates folders, starts agents, launches the main loop.

Usage:
    python start.py
"""

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# Проект root
BASE_DIR = Path(__file__).parent
os.chdir(BASE_DIR)
sys.path.insert(0, str(BASE_DIR))

load_dotenv()

from core.task_card import TaskCard
from core.state_machine import StateMachine
from core.skill_registry import SkillRegistry
from core.token_tracker import TokenTracker
from core.agent_factory import AgentFactory
from core.foreman import Foreman
from core.supervisor import Supervisor
from core.file_watcher import PollingWatcher
from core.parallel_manager import ParallelManager

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)-20s] %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("factory")

console = Console()

# ── Global state ──
_running = True


def load_config() -> dict:
    """Load factory.yaml config."""
    config_path = BASE_DIR / "config" / "factory.yaml"
    if config_path.exists():
        return yaml.safe_load(config_path.read_text(encoding="utf-8"))
    return {}


def create_pipeline_dirs():
    """Create pipeline folder structure."""
    stages = [
        "0_inbox", "1_planning", "2_ready", "3_in_progress",
        "4_review", "5_rework", "6_completed", "7_archived",
    ]
    for stage in stages:
        (BASE_DIR / "pipeline" / stage).mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "workspace").mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "registry").mkdir(parents=True, exist_ok=True)


def init_registry():
    """Initialize empty registry files if they don't exist."""
    agents_path = BASE_DIR / "registry" / "agents.json"
    if not agents_path.exists():
        agents_path.write_text(
            json.dumps({"agents": []}, indent=2), encoding="utf-8"
        )

    dashboard_path = BASE_DIR / "registry" / "dashboard.json"
    if not dashboard_path.exists():
        dashboard_path.write_text(
            json.dumps({"pipeline": {}, "agents": {}, "costs": {}}, indent=2),
            encoding="utf-8",
        )


def update_dashboard(state_machine: StateMachine, agent_factory: AgentFactory, token_tracker: TokenTracker):
    """Update dashboard.json with current state."""
    from datetime import datetime, timezone

    dashboard = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": state_machine.get_pipeline_summary(),
        "agents": {
            "active": agent_factory.get_active_count(),
            "total": len(agent_factory.get_all_agents()),
            "list": agent_factory.get_all_agents(),
        },
        "costs": token_tracker.get_totals(),
        "agent_costs": token_tracker.get_agent_costs(),
    }

    dashboard_path = BASE_DIR / "registry" / "dashboard.json"
    dashboard_path.write_text(
        json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def print_status(state_machine: StateMachine, token_tracker: TokenTracker):
    """Print current pipeline status to console."""
    summary = state_machine.get_pipeline_summary()
    costs = token_tracker.get_totals()

    table = Table(title="Pipeline Status", show_header=True)
    table.add_column("Stage", style="cyan")
    table.add_column("Tasks", justify="right", style="green")

    stage_labels = {
        "0_inbox": "Входящие",
        "1_planning": "Планирование",
        "2_ready": "Готовы",
        "3_in_progress": "В работе",
        "4_review": "На проверке",
        "5_rework": "Доработка",
        "6_completed": "Выполнено",
        "7_archived": "Архив",
    }

    for stage, count in summary.items():
        label = stage_labels.get(stage, stage)
        style = "bold yellow" if count > 0 and stage not in ("6_completed", "7_archived") else ""
        table.add_row(f"{label} ({stage})", str(count), style=style)

    console.print(table)
    console.print(
        f"  Токены: {costs['input_tokens']}in / {costs['output_tokens']}out | "
        f"Стоимость: ${costs['cost_usd']:.4f} | "
        f"API вызовов: {costs['api_calls']}"
    )


def main():
    global _running

    console.print(Panel.fit(
        "[bold cyan]AI FACTORY[/bold cyan] — Мульти-агентная система оркестрации задач",
        border_style="cyan",
    ))

    # Проверяем API ключ
    if not os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY") == "your-api-key-here":
        console.print("[bold red]Ошибка:[/bold red] Установите ANTHROPIC_API_KEY в .env файле")
        sys.exit(1)

    # Загружаем конфигурацию
    config = load_config()
    agents_cfg = config.get("agents", {})
    watcher_cfg = config.get("watcher", {})

    # Создаём структуру папок
    create_pipeline_dirs()
    init_registry()
    console.print("[green]Структура папок создана[/green]")

    # Инициализируем компоненты
    pipeline_root = BASE_DIR / "pipeline"
    state_machine = StateMachine(pipeline_root)
    skill_registry = SkillRegistry(BASE_DIR / "config" / "skills")
    token_tracker = TokenTracker(BASE_DIR / "registry" / "cost_log.jsonl")
    agent_factory = AgentFactory(skill_registry, token_tracker, BASE_DIR / "registry" / "agents.json")
    parallel_mgr = ParallelManager(max_workers=agents_cfg.get("workers", {}).get("max_parallel", 5))

    console.print(f"[green]Навыки загружены:[/green] {', '.join(skill_registry.all_ids())}")

    # Создаём Foreman
    foreman_cfg = agents_cfg.get("foreman", {})
    foreman = Foreman(
        model=foreman_cfg.get("model", "claude-sonnet-4-6"),
        skill_registry=skill_registry,
        state_machine=state_machine,
        token_tracker=token_tracker,
        max_turns=foreman_cfg.get("max_turns", 15),
    )
    console.print("[green]Бригадир создан[/green]")

    # Создаём Supervisor
    supervisor_cfg = agents_cfg.get("supervisor", {})
    supervisor = Supervisor(
        model=supervisor_cfg.get("model", "claude-sonnet-4-6"),
        state_machine=state_machine,
        token_tracker=token_tracker,
        approval_threshold=supervisor_cfg.get("approval_threshold", 7.0),
        max_turns=supervisor_cfg.get("max_turns", 10),
    )
    console.print("[green]Контролёр создан[/green]")

    # ── Callbacks для watchers ──

    def on_inbox_task(file_path: Path):
        """Бригадир обрабатывает новую задачу из inbox."""
        try:
            task = TaskCard.load(file_path)
            console.print(f"\n[bold yellow]Новая задача:[/bold yellow] {task.title} ({task.id})")
            subtasks = foreman.process_inbox_task(task)
            if subtasks:
                console.print(f"  Декомпозировано на [cyan]{len(subtasks)}[/cyan] подзадач")
                for st in subtasks:
                    deps = f" (зависит от: {', '.join(st.depends_on)})" if st.depends_on else ""
                    console.print(f"    - {st.title} [{', '.join(st.required_skills)}]{deps}")

                # Создаём воркеров для нужных навыков и запускаем
                needed_skills = set()
                for st in subtasks:
                    for skill in st.required_skills:
                        needed_skills.add(skill)

                for skill in needed_skills:
                    worker = agent_factory.get_or_create_worker([skill])
                    worker.state_machine = state_machine

                    def worker_loop(w=worker):
                        while _running:
                            result = w.pick_and_execute()
                            if result:
                                agent_factory.increment_completed(w.agent_id)
                                agent_factory.update_status(w.agent_id, "idle")
                            time.sleep(1)

                    parallel_mgr.submit_worker(worker.agent_id, worker_loop)
                    console.print(f"  [green]Воркер запущен:[/green] {worker.name} ({worker.agent_id})")

        except Exception as e:
            logger.error(f"Ошибка обработки inbox: {e}", exc_info=True)

    def on_review_task(file_path: Path):
        """Контролёр проверяет выполненную задачу."""
        try:
            task = TaskCard.load(file_path)
            console.print(f"\n[bold blue]На проверке:[/bold blue] {task.title} ({task.id})")
            reviewed = supervisor.review_task(task)
            if reviewed:
                last_review = reviewed.review_notes[-1] if reviewed.review_notes else None
                if last_review:
                    verdict_color = "green" if last_review.verdict == "APPROVED" else "red"
                    console.print(
                        f"  Результат: [{verdict_color}]{last_review.verdict}[/{verdict_color}] "
                        f"(score: {last_review.score})"
                    )
        except Exception as e:
            logger.error(f"Ошибка ревью: {e}", exc_info=True)

    # ── Запускаем watchers ──
    interval = watcher_cfg.get("interval_seconds", 2)

    inbox_watcher = PollingWatcher(
        pipeline_root / "0_inbox", on_inbox_task, interval, "Inbox"
    )
    review_watcher = PollingWatcher(
        pipeline_root / "4_review", on_review_task, interval, "Review"
    )

    inbox_watcher.start()
    review_watcher.start()

    # ── Dashboard server ──
    server_cfg = config.get("server", {})
    server_host = server_cfg.get("host", "127.0.0.1")
    server_port = server_cfg.get("port", 5050)

    import threading
    from server.server import start_server
    server_thread = threading.Thread(
        target=start_server,
        args=(server_host, server_port),
        daemon=True,
        name="dashboard-server",
    )
    server_thread.start()
    console.print(f"[green]Dashboard:[/green] http://{server_host}:{server_port}")

    # ── Graceful shutdown ──
    def shutdown(sig=None, frame=None):
        global _running
        _running = False
        console.print("\n[yellow]Остановка фабрики...[/yellow]")
        inbox_watcher.stop()
        review_watcher.stop()
        parallel_mgr.shutdown(wait=False)
        update_dashboard(state_machine, agent_factory, token_tracker)
        console.print("[green]Фабрика остановлена.[/green]")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)

    # ── Main status loop ──
    console.print()
    console.print(Panel.fit(
        "[bold green]ФАБРИКА ЗАПУЩЕНА[/bold green]\n"
        f"Dashboard: http://{server_host}:{server_port}\n"
        f"Мониторинг: pipeline/0_inbox/ (каждые {interval} сек)\n"
        "Для отправки задачи: python cli/main.py submit \"Описание задачи\"\n"
        "Или через Dashboard в браузере\n"
        "Ctrl+C для остановки",
        border_style="green",
    ))

    tick = 0
    while _running:
        time.sleep(interval)
        tick += 1

        # Обновляем дашборд каждые 10 секунд
        if tick % 5 == 0:
            update_dashboard(state_machine, agent_factory, token_tracker)

        # Выводим статус каждые 30 секунд
        if tick % 15 == 0:
            console.print()
            print_status(state_machine, token_tracker)


if __name__ == "__main__":
    main()
