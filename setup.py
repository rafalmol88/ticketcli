from setuptools import setup, find_packages

setup(
    name="ticketcli",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "argcomplete",
        "questionary",
        "requests",
    ],
    entry_points={
        "console_scripts": [
            "ticketcli=ticketcli.cli:main",
        ],
    },
)