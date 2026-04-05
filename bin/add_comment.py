#!/usr/bin/env python3
from ticketcli.commands import add_comment_main, main_guard

if __name__ == "__main__":
    main_guard(add_comment_main)
