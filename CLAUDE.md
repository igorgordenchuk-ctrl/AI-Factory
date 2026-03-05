# AI Factory

Multi-agent task orchestration system. Users submit tasks → AI decomposes into subtasks → agents execute → supervisor reviews → done.

## Quick Start

```bash
# 1. Set API key in .env
# 2. Install deps
pip install -r requirements.txt

# 3. Start factory (runs pipeline + dashboard)
python start.py

# 4. Submit task (in another terminal)
python cli/main.py submit "Create a REST API with Flask"

# 5. Open dashboard
# http://localhost:5050
```

## Architecture

```
User → [0_inbox] → Foreman decomposes → [2_ready] → Worker executes → [4_review] → Supervisor checks
                                                                                       ├─ APPROVE → [6_completed]
                                                                                       └─ REWORK  → [5_rework] → Worker fixes
```

**Models by role:**
- **Foreman** (decomposition): `claude-sonnet-4-6` — smart planning
- **Supervisor** (review): `claude-sonnet-4-6` — quality control
- **Workers** (execution): `claude-haiku-4-5-20251001` — fast & cheap

## Key Files

- `start.py` — entry point, initializes factory
- `cli/main.py` — CLI: submit, status, inspect, agents, tasks
- `BOARD.md` — auto-updated task board (refreshed on every transition)
- `config/factory.yaml` — models, limits, costs
- `config/skills/*.yaml` — agent skill definitions
- `core/agent.py` — BaseAgent with tool-use loop
- `core/foreman.py` — task decomposition
- `core/worker.py` — task execution
- `core/supervisor.py` — QA review

## CLI Commands

```bash
python cli/main.py submit "description" [-t title] [-p priority] [--tags "a,b"]
python cli/main.py status          # pipeline summary
python cli/main.py tasks           # all tasks table
python cli/main.py inspect TASK_ID # task detail
python cli/main.py agents          # registered agents
```

## Pipeline Folders

```
pipeline/0_inbox/        → incoming tasks
pipeline/1_planning/     → foreman decomposes
pipeline/2_ready/        → waiting for worker
pipeline/3_in_progress/  → being executed
pipeline/4_review/       → supervisor checking
pipeline/5_rework/       → needs fixes
pipeline/6_completed/    → done
pipeline/7_archived/     → old tasks
```

## Adding Skills

Create `config/skills/your_skill.yaml`:
```yaml
skill_id: your_skill
name: "Your Skill Name"
system_prompt: |
  You are an expert at...
tools: [read_file, write_file, run_python]
preferred_model: "claude-sonnet-4-6"
cost_tier: "medium"
```

## Config

Edit `config/factory.yaml` to change models, parallelism, budgets.
