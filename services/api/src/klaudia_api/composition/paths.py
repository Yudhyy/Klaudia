"""Resolve launch files without using the installed source location."""

from pathlib import Path

from dotenv import find_dotenv


def runtime_directory() -> Path:
    """Return the local environment directory or the current launch directory.

    Returns:
        Parent of the discovered .env file, or the current working directory.
    """
    environment_file = find_dotenv(usecwd=True)
    return Path(environment_file).parent if environment_file else Path.cwd()
