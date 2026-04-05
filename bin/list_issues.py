#!/usr/bin/env python3
from ticketcli.commands import list_issues_main, main_guard

if __name__ == "__main__":
    main_guard(list_issues_main)
