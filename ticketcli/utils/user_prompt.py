from typing import Dict


def print_available_users(mapping: Dict[str, str]) -> None:
    if not mapping:
        print("No user mappings configured.")
        return

    print("Available users:")
    for k in sorted(mapping.keys()):
        print(f"  {k}")