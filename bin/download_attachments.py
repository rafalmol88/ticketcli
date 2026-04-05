#!/usr/bin/env python3
from ticketcli.commands import download_attachments_main, main_guard

if __name__ == "__main__":
    main_guard(download_attachments_main)
