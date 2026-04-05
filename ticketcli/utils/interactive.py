from typing import List, Sequence


def choose_indices_interactively(
    items: Sequence[str],
    prompt: str = "Select items by number (comma-separated, blank to cancel): "
) -> List[int]:
    if not items:
        return []

    for i, item in enumerate(items, 1):
        print(f"  {i}. {item}")

    while True:
        raw = input(prompt).strip()
        if not raw:
            return []

        try:
            indices = []
            for part in raw.split(","):
                n = int(part.strip())
                if n < 1 or n > len(items):
                    raise ValueError
                indices.append(n - 1)
            return sorted(set(indices))
        except ValueError:
            print("Invalid selection. Use numbers like: 1,3,4")