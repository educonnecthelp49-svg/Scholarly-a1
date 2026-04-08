#!/usr/bin/env python3

import os
import subprocess
import sys
from pathlib import Path
os.environ["PIP_USER"] = "0"
# For TUI and beautiful output
try:
    import questionary
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.progress import Progress, SpinnerColumn, TextColumn
except ImportError:
    print("Installing required packages for TUI...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "questionary", "rich"])
    import questionary
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

def run_command(cmd, cwd=None, show_output=False):
    """Run a command in the shell with error handling."""
    try:
        result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
        if result.returncode != 0:
            console.print(f"[red]Error executing command: {cmd}[/red]")
            console.print(f"[red]{result.stderr}[/red]")
            sys.exit(1)
        if show_output:
            console.print(result.stdout)
        return result.stdout
    except Exception as e:
        console.print(f"[red]Failed to run command: {e}[/red]")
        sys.exit(1)

def create_venv():
    """Create virtual environment if not exists."""
    venv_path = Path("venv")
    if not venv_path.exists():
        with console.status("[bold green]Creating virtual environment...[/bold green]"):
            run_command("python -m venv venv")
        console.print("[green]✓ Virtual environment created successfully![/green]")
    else:
        console.print("[yellow]⚠ Virtual environment already exists.[/yellow]")

def activate_venv():
    """Get paths to Python and pip in the virtual environment."""
    venv_path = Path("venv")
    if os.name == 'nt':  # Windows
        python_exe = venv_path / "Scripts" / "python.exe"
        pip_exe = venv_path / "Scripts" / "pip.exe"
    else:
        python_exe = venv_path / "bin" / "python"
        pip_exe = venv_path / "bin" / "pip"
    return str(python_exe), str(pip_exe)

def install_requirements(pip_exe):
    """Install requirements.txt."""
    with console.status("[bold green]Installing requirements...[/bold green]"):
        run_command(f"{pip_exe} install -r requirements.txt")
    console.print("[green]✓ Requirements installed successfully![/green]")

def setup_env():
    """Setup .env file with secret key."""
    env_file = Path(".env")
    if env_file.exists():
        overwrite = questionary.confirm(
            "A .env file already exists. Do you want to overwrite it?",
            default=False
        ).ask()
        if not overwrite:
            console.print("[yellow]Skipping .env setup.[/yellow]")
            exit()

    console.print("\n[bold blue]🔐 Secret Key Configuration[/bold blue]")
    secret_choice = questionary.select(
        "How would you like to set the SECRET_KEY?",
        choices=[
            {"name": "Generate a secure random key (recommended)", "value": "random"},
            {"name": "Enter a custom key", "value": "custom"}
        ]
    ).ask()

    if secret_choice == "random":
        secret_key = os.urandom(256).hex()
        console.print("[green]✓ Generated secure random key![/green]")
    else:
        secret_key = questionary.password("Enter your custom secret key:").ask()

    # Write initial .env
    with open(".env", "w") as f:
        f.write(f"SECRET_KEY={secret_key}\n")
    console.print("[green]✓ .env file created with SECRET_KEY![/green]")

def choose_db():
    """Choose database from list."""
    console.print("\n[bold blue]🗄️  Database Selection[/bold blue]")
    dbs = [
        "SQLite (file-based, no server required)",
        "PostgreSQL",
        "MySQL",
        "MariaDB",
        "Oracle",
        "SQL Server",
        "Firebird",
        "Sybase",
        "DB2",
        "Teradata"
    ]
    db_choice = questionary.select("Choose your database:", choices=dbs).ask()
    # Extract the DB name without description
    db_name = db_choice.split(" ")[0]
    return db_name

def install_db_driver(db, pip_exe):
    """Install the correct driver for the chosen DB."""
    drivers = {
        "SQLite": [],  # built-in
        "PostgreSQL": ["psycopg2-binary"],
        "MySQL": ["pymysql"],
        "MariaDB": ["pymysql"],  # same as MySQL
        "Oracle": ["cx_Oracle"],
        "SQL Server": ["pyodbc"],
        "Firebird": ["fdb"],
        "Sybase": ["pymssql"],
        "DB2": ["ibm_db"],
        "Teradata": ["teradatasql"]
    }
    packages = drivers.get(db, [])
    if packages:
        console.print(f"\n[bold green]Installing driver for {db}...[/bold green]")
        for pkg in packages:
            with console.status(f"[bold green]Installing {pkg}...[/bold green]"):
                run_command(f"{pip_exe} install {pkg}")
        console.print(f"[green]✓ {db} driver installed![/green]")
    else:
        console.print(f"[green]✓ {db} uses built-in driver, no installation needed![/green]")

def get_db_config(db):
    """Get DB configuration from user."""
    console.print(f"\n[bold blue]⚙️  {db} Configuration[/bold blue]")
    config = {}
    config['username'] = questionary.text("Database username:").ask()
    config['password'] = questionary.password("Database password:").ask()
    config['host'] = questionary.text("Database host:").ask()
    config['port'] = questionary.text("Database port:").ask()
    sslmode = questionary.text("SSL Mode (optional, press Enter to skip):").ask()
    if sslmode:
        config['sslmode'] = sslmode
    config['db'] = questionary.text("Database name:").ask()
    ssl_cert = questionary.text("SSL Certificate path (only root CA certificate, optional, press Enter to skip):").ask()
    if ssl_cert:
        config['ssl_cert'] = ssl_cert
    return config

def build_db_uri(db, config):
    """Build SQLAlchemy DATABASE_URI."""
    if db == "SQLite":
        return f"sqlite:///{config['db']}.db"
    else:
        uri = f"{db.lower()}://{config['username']}:{config['password']}@{config['host']}:{config['port']}/{config['db']}"
        params = []
        if 'sslmode' in config:
            params.append(f"sslrootcert={config['sslmode']}")
        if 'ssl_cert' in config:
            params.append(f"sslrootcert={config['ssl_cert']}")
        if params:
            uri += "?" + "&".join(params)
        return uri

def update_env_with_db(uri):
    """Update .env with DB URI."""
    with open(".env", "a") as f:
        f.write(f"SQLALCHEMY_DATABASE_URI={uri}\n")
    console.print("[green]✓ Database configuration added to .env![/green]")

def main():
    # Welcome message
    title = Text("🚀 EduConnect Deployment Setup", style="bold magenta")
    panel = Panel(title, title_align="center", border_style="blue")
    console.print(panel)
    console.print("[dim]This script will set up your EduConnect application environment.[/dim]\n")

    create_venv()
    python_exe, pip_exe = activate_venv()

    # Ensure questionary and rich are available in venv
    try:
        import questionary
        from rich.console import Console
    except ImportError:
        with console.status("[bold green]Installing TUI packages...[/bold green]"):
            run_command(f"{pip_exe} install questionary rich")
        import questionary
        from rich.console import Console

    install_requirements(pip_exe)
    setup_env()

    db = choose_db()
    install_db_driver(db, pip_exe)

    if db != "SQLite":
        config = get_db_config(db)
        uri = build_db_uri(db, config)
    else:
        console.print("\n[bold blue]📁 SQLite Configuration[/bold blue]")
        db_name = questionary.text("Database file name (without .db extension):").ask()
        uri = f"sqlite:///{db_name}.db"

    update_env_with_db(uri)

    # Success message
    success_panel = Panel(
        "[green]🎉 Deployment setup complete![/green]\n\n"
        "[bold]To run the application:[/bold]\n"
        "1. Activate the virtual environment:\n"
        "   [cyan]venv\\Scripts\\activate[/cyan] (Windows)\n"
        "   [cyan]source venv/bin/activate[/cyan] (Linux/Mac)\n"
        "2. Run the app:\n"
        "   [cyan]python EduConnect_Source/app.py[/cyan]",
        title="✅ Setup Complete",
        border_style="green"
    )
    console.print(success_panel)

if __name__ == "__main__":
    main()