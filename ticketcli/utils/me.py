from ticketcli.utils.user_mapping import resolve_user


def resolve_me(target_name: str, target_config: dict):
    me_key = target_config.get("me")

    resolved, mapping = resolve_user(me_key, target_name)

    if not me_key or not resolved:
        print("Invalid or missing 'me' in target config.")
        print("Available users:")
        for k in sorted(mapping.keys()):
            print(f"  {k}")
        raise SystemExit(1)

    return resolved