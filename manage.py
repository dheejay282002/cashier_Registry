#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'registry.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        # If global Python is used and Django is not found, dynamically inject
        # the local venv's site-packages into sys.path to resolve imports.
        from pathlib import Path
        base_dir = Path(__file__).resolve().parent
        for folder in ('.venv', 'venv'):
            venv_site = base_dir / folder / 'Lib' / 'site-packages'
            if venv_site.exists():
                sys.path.insert(0, str(venv_site))
                break

        try:
            from django.core.management import execute_from_command_line
        except ImportError:
            raise ImportError(
                "Couldn't import Django. Are you sure it's installed and "
                "available on your PYTHONPATH environment variable? Did you "
                "forget to activate a virtual environment?"
            ) from exc

    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
