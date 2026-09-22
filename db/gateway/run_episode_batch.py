r"""
Generate episodes ONE AT A TIME, in order, from START_EPISODE through
END_EPISODE, stopping automatically once it reaches the target count.

This wraps the exact same three commands you'd otherwise run manually per
episode (generate_episode_prompt.py -> run_glm_prompt.py -> load_story_to_db.py),
in sequence, with retries on hard failures and a hard stop (not a skip) if
an episode can't be produced — continuity depends on the previous episode
having actually loaded correctly, so silently skipping a broken episode
and moving on would corrupt every episode after it.

Usage:
    python run_episode_batch.py PROJECT_ID START_EPISODE END_EPISODE MINUTES_PER_EPISODE [MAX_RETRIES]

Example — generate episodes 2 through 10, 2 minutes each:
    python run_episode_batch.py ECHO_ENGINE_01 2 10 2

MODEL_NAME / OLLAMA_HOST are read from this process's own environment (set
them the normal way, e.g. via .env defaults or a one-off
`docker compose exec -e MODEL_NAME=... -e OLLAMA_HOST=... gateway python run_episode_batch.py ...`)
— every subprocess call below inherits them automatically.
"""

import subprocess
import sys
import time

EPISODE_PROMPT_FILE = "GLM_Episode_Prompt.txt"
OUTPUT_FILE = "outputs/GLM_Test_Output.json"


def run_step(description: str, args: list) -> bool:
    print(f"  -> {description} ...")
    result = subprocess.run([sys.executable] + args, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"     FAILED (exit {result.returncode})")
        if result.stdout.strip():
            print("     stdout:", result.stdout.strip()[-2000:])
        if result.stderr.strip():
            print("     stderr:", result.stderr.strip()[-2000:])
        return False
    # Surface the useful summary lines (shot counts, schema check, warnings)
    # without dumping the whole prompt/JSON back at the terminal.
    for line in result.stdout.splitlines():
        if any(k in line for k in ("Schema check", "Stage returned", "Characters returned",
                                    "Locations returned", "Episodes returned", "shots,",
                                    "WARNING", "Loaded project", "Created project", "Wrote")):
            print(f"     {line}")
    return True


def generate_one_episode(project_id: str, episode_number: int, minutes: float, max_retries: int) -> bool:
    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            print(f"  (retry {attempt}/{max_retries})")

        if not run_step(
            f"Building prompt for episode {episode_number}",
            ["generate_episode_prompt.py", project_id, str(episode_number), str(minutes)],
        ):
            continue  # a DB/connection error here is worth retrying

        if not run_step(
            f"Sending episode {episode_number} to GLM",
            ["run_glm_prompt.py", EPISODE_PROMPT_FILE],
        ):
            continue  # generation/parse failure — worth retrying

        if not run_step(
            f"Loading episode {episode_number} into MySQL",
            ["load_story_to_db.py", OUTPUT_FILE, project_id],
        ):
            continue  # DB error — worth retrying

        return True

    return False


def main():
    if len(sys.argv) not in (5, 6):
        print("Usage: python run_episode_batch.py PROJECT_ID START_EPISODE END_EPISODE MINUTES_PER_EPISODE [MAX_RETRIES]")
        sys.exit(1)

    project_id = sys.argv[1]
    start_ep = int(sys.argv[2])
    end_ep = int(sys.argv[3])
    minutes = float(sys.argv[4])
    max_retries = int(sys.argv[5]) if len(sys.argv) == 6 else 3

    if end_ep < start_ep:
        print(f"END_EPISODE ({end_ep}) must be >= START_EPISODE ({start_ep}).")
        sys.exit(1)

    total = end_ep - start_ep + 1
    print(f"Generating episodes {start_ep}..{end_ep} ({total} total) for project '{project_id}', "
          f"~{minutes} min each, up to {max_retries} retries per episode.\n")

    completed = []
    for i, ep_num in enumerate(range(start_ep, end_ep + 1), start=1):
        print(f"[{i}/{total}] Episode {ep_num}")
        start_time = time.time()

        ok = generate_one_episode(project_id, ep_num, minutes, max_retries)

        elapsed = time.time() - start_time
        if ok:
            completed.append(ep_num)
            print(f"  Episode {ep_num} done in {elapsed:.0f}s.\n")
        else:
            print(f"\nSTOPPED: episode {ep_num} failed after {max_retries} attempts.")
            print(f"Completed before stopping: {completed if completed else 'none'}")
            print(f"Fix the issue, then resume with:")
            print(f"  python run_episode_batch.py {project_id} {ep_num} {end_ep} {minutes}")
            sys.exit(1)

    print(f"All {total} episodes ({start_ep}..{end_ep}) completed for '{project_id}'.")


if __name__ == "__main__":
    main()
