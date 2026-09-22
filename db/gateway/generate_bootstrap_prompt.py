r"""
Build the initial SERIES_OUTLINE prompt: the project bible, the full cast,
the full location list, and a lightweight per-episode outline covering the
WHOLE series — without generating any episode's detailed shots yet.

This is the proper replacement for hand-editing GLM_Test_Prompt.txt: counts
are arguments, not something buried in a text file you have to remember to
edit correctly every time.

Usage:
    python generate_bootstrap_prompt.py "CONCEPT TEXT" NUM_CHARACTERS NUM_LOCATIONS NUM_EPISODES

Example:
    python generate_bootstrap_prompt.py "A young Nigerian mechanic discovers a machine left behind by his missing father that can replay fragments of the past." 2 2 10

Writes GLM_Bootstrap_Prompt.txt, ready for:
    python run_glm_prompt.py GLM_Bootstrap_Prompt.txt
    python load_story_to_db.py outputs/GLM_Test_Output.json
"""

import sys
from pathlib import Path


def build_prompt(concept: str, num_characters: int, num_locations: int, num_episodes: int) -> str:
    parts = [
        'Use stage "SERIES_OUTLINE".',
        "",
        "Concept:",
        f'"{concept}"',
        "",
        "Create:",
        "1. project (the project bible)",
        f"2. characters - exactly {num_characters} main characters",
        f"3. locations - exactly {num_locations} locations",
        f"4. episode_outline - exactly {num_episodes} entries, one per episode of the series "
        f"(episode_id following the EP_NNN convention, numbered 1 through {num_episodes})",
        "",
        "Do NOT generate the detailed \"episodes\" array (scenes/shots) in this call — only the "
        "lightweight episode_outline. Detailed shot-by-shot content for each episode is generated "
        "separately, one episode at a time, later.",
        "",
        "Plan character_ids/location_ids per episode_outline entry with real narrative judgment — "
        "not every character needs to appear in every episode.",
        "",
        "Do not introduce a cybernetic eye, scar, tattoo, weapon, wardrobe item, or other persistent "
        "feature unless you establish it first in the character bible.",
        "",
        "Return only the JSON object as defined by your output contract. No prose, no markdown fences.",
    ]
    return "\n".join(parts)


def main():
    if len(sys.argv) != 5:
        print('Usage: python generate_bootstrap_prompt.py "CONCEPT TEXT" NUM_CHARACTERS NUM_LOCATIONS NUM_EPISODES')
        sys.exit(1)

    concept = sys.argv[1]
    num_characters = int(sys.argv[2])
    num_locations = int(sys.argv[3])
    num_episodes = int(sys.argv[4])

    prompt = build_prompt(concept, num_characters, num_locations, num_episodes)

    out_path = Path("GLM_Bootstrap_Prompt.txt")
    out_path.write_text(prompt, encoding="utf-8")
    print(f"Wrote {out_path} — requesting {num_characters} characters, {num_locations} locations, "
          f"{num_episodes}-episode outline.")


if __name__ == "__main__":
    main()
