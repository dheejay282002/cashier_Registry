$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $venvPy manage.py runserver $args