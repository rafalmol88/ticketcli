#!/usr/bin/env python3
from ticketcli.commands import create_issue_main, main_guard

if __name__ == "__main__":
    main_guard(create_issue_main)
