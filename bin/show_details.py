#!/usr/bin/env python3
from ticketcli.commands import show_details_main, main_guard

if __name__ == "__main__":
    main_guard(show_details_main)
