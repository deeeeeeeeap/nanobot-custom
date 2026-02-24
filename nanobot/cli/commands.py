"""CLI commands for nanobot."""

import asyncio
import atexit
import os
import signal
from pathlib import Path
import select
import sys

import typer
from loguru import logger
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nanobot import __version__, __logo__

app = typer.Typer(
    name="nanobot",
    help=f"{__logo__} nanobot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}

# ---------------------------------------------------------------------------
# Lightweight CLI input: readline for arrow keys / history, termios for flush
# ---------------------------------------------------------------------------

_READLINE = None
_HISTORY_FILE: Path | None = None
_HISTORY_HOOK_REGISTERED = False
_USING_LIBEDIT = False
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit
_GLOBAL_EXCEPTION_HOOKS_INSTALLED = False
_LOGGING_CONFIGURED = False


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception as e:
        logger.debug(f"Skipping tty input flush: {e}")
        return

    try:
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception as e:
        logger.debug(f"termios flush unavailable, falling back to select loop: {e}")

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception as e:
        logger.debug(f"Best-effort tty flush failed: {e}")
        return


def _save_history() -> None:
    if _READLINE is None or _HISTORY_FILE is None:
        return
    try:
        _READLINE.write_history_file(str(_HISTORY_FILE))
    except Exception as e:
        logger.debug(f"Failed to save readline history: {e}")
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception as e:
        logger.debug(f"Failed to restore terminal settings: {e}")


def _enable_line_editing() -> None:
    """Enable readline for arrow keys, line editing, and persistent history."""
    global _READLINE, _HISTORY_FILE, _HISTORY_HOOK_REGISTERED, _USING_LIBEDIT, _SAVED_TERM_ATTRS

    # Save terminal state before readline touches it
    try:
        import termios
        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception as e:
        logger.debug(f"Could not snapshot terminal settings: {e}")

    history_file = Path.home() / ".nanobot" / "history" / "cli_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_FILE = history_file

    try:
        import readline
    except ImportError:
        return

    _READLINE = readline
    _USING_LIBEDIT = "libedit" in (readline.__doc__ or "").lower()

    try:
        if _USING_LIBEDIT:
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
        readline.parse_and_bind("set editing-mode emacs")
    except Exception as e:
        logger.debug(f"Readline keybind setup failed: {e}")

    try:
        readline.read_history_file(str(history_file))
    except Exception as e:
        logger.debug(f"Readline history load skipped: {e}")

    if not _HISTORY_HOOK_REGISTERED:
        atexit.register(_save_history)
        _HISTORY_HOOK_REGISTERED = True


def _prompt_text() -> str:
    """Build a readline-friendly colored prompt."""
    if _READLINE is None:
        return "You: "
    # libedit on macOS does not honor GNU readline non-printing markers.
    if _USING_LIBEDIT:
        return "\033[1;34mYou:\033[0m "
    return "\001\033[1;34m\002You:\001\033[0m\002 "


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with consistent terminal styling."""
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(
        Panel(
            body,
            title=f"{__logo__} nanobot",
            title_align="left",
            border_style="cyan",
            padding=(0, 1),
        )
    )
    console.print()


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input with arrow keys and history (runs input() in a thread)."""
    try:
        return await asyncio.to_thread(input, _prompt_text())
    except EOFError as exc:
        raise KeyboardInterrupt from exc


def _handle_uncaught_exception(exc_type, exc, exc_tb) -> None:
    """Log uncaught sync exceptions and show a concise CLI notice."""
    if issubclass(exc_type, KeyboardInterrupt):
        return
    logger.opt(exception=(exc_type, exc, exc_tb)).critical("Unhandled exception")
    console.print("[red]Fatal error: unhandled exception. See logs for details.[/red]")


def _handle_asyncio_exception(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Log uncaught async exceptions from the active event loop."""
    exc = context.get("exception")
    msg = context.get("message", "Unhandled asyncio exception")
    if exc:
        logger.opt(exception=exc).error(msg)
    else:
        logger.error(f"{msg}: {context}")
    console.print("[red]Runtime error: async task failed unexpectedly.[/red]")


def _install_global_exception_hooks() -> None:
    global _GLOBAL_EXCEPTION_HOOKS_INSTALLED
    if _GLOBAL_EXCEPTION_HOOKS_INSTALLED:
        return
    sys.excepthook = _handle_uncaught_exception
    _GLOBAL_EXCEPTION_HOOKS_INSTALLED = True


def _configure_logging() -> None:
    """Configure stderr + rotating file logs from config when available."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    logger.remove()
    logger.add(sys.stderr, level="INFO", colorize=True)

    try:
        from nanobot.config.loader import get_data_dir, load_config

        config = load_config()
        log_dir = get_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_dir / "nanobot.log",
            level=config.logging.level,
            rotation=config.logging.max_file_bytes,
            retention=config.logging.max_files,
            encoding="utf-8",
            enqueue=False,
            backtrace=False,
            diagnose=False,
        )
    except Exception as e:
        logger.debug(f"Logging file sink not configured: {e}")

    _LOGGING_CONFIGURED = True


def _run_async(coro):
    """Run async entrypoints with loop-level exception handling."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(_handle_asyncio_exception)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """nanobot - Personal AI Assistant."""
    _install_global_exception_hooks()
    _configure_logging()


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard():
    """Initialize nanobot configuration and workspace."""
    from nanobot.config.loader import get_config_path, save_config
    from nanobot.config.schema import Config
    from nanobot.utils.helpers import get_workspace_path
    
    config_path = get_config_path()
    
    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        if not typer.confirm("Overwrite?"):
            raise typer.Exit()
    
    # Create default config
    config = Config()
    save_config(config)
    console.print(f"[green]OK[/green] Created config at {config_path}")
    
    # Create workspace
    workspace = get_workspace_path()
    console.print(f"[green]OK[/green] Created workspace at {workspace}")
    
    # Create default bootstrap files
    _create_workspace_templates(workspace)
    
    console.print(f"\n{__logo__} nanobot is ready!")
    console.print("\nNext steps:")
    console.print("  1. Add your API key to [cyan]~/.nanobot/config.json[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print("  2. Chat: [cyan]nanobot agent -m \"Hello!\"[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/nanobot#-chat-apps[/dim]")




def _create_workspace_templates(workspace: Path):
    """Create default workspace template files."""
    templates = {
        "AGENTS.md": """# Agent Instructions

You are a helpful AI assistant. Be concise, accurate, and friendly.

## Guidelines

- Always explain what you're doing before taking actions
- Ask for clarification when the request is ambiguous
- Use tools to help accomplish tasks
- Remember important information in your memory files
""",
        "SOUL.md": """# Soul

I am nanobot, a lightweight AI assistant.

## Personality

- Helpful and friendly
- Concise and to the point
- Curious and eager to learn

## Values

- Accuracy over speed
- User privacy and safety
- Transparency in actions
""",
        "USER.md": """# User

Information about the user goes here.

## Preferences

- Communication style: (casual/formal)
- Timezone: (your timezone)
- Language: (your preferred language)
""",
    }
    
    for filename, content in templates.items():
        file_path = workspace / filename
        if not file_path.exists():
            file_path.write_text(content)
            console.print(f"  [dim]Created {filename}[/dim]")
    
    # Create memory directory and MEMORY.md
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)
    memories_dir = memory_dir / "memories"
    memories_dir.mkdir(exist_ok=True)
    for cat in ("preferences", "entities", "events", "cases", "patterns"):
        (memories_dir / cat).mkdir(parents=True, exist_ok=True)
    memory_file = memory_dir / "MEMORY.md"
    if not memory_file.exists():
        memory_file.write_text("""# Long-term Memory

This file stores important information that should persist across sessions.

## User Information

(Important facts about the user)

## Preferences

(User preferences learned over time)

## Important Notes

(Things to remember)
""")
        console.print("  [dim]Created memory/MEMORY.md[/dim]")

    # Create skills directory for custom user skills
    skills_dir = workspace / "skills"
    skills_dir.mkdir(exist_ok=True)


def _make_provider(config):
    """Create LiteLLMProvider from config. Exits if no API key found."""
    from nanobot.providers.litellm_provider import LiteLLMProvider
    p = config.get_provider()
    model = config.agents.defaults.model
    if not (p and p.api_key) and not model.startswith("bedrock/"):
        console.print("[red]Error: No API key configured.[/red]")
        console.print("Set one in ~/.nanobot/config.json under providers section")
        raise typer.Exit(1)
    return LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=config.get_api_base(),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=config.get_provider_name(),
    )


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the nanobot gateway."""
    from nanobot.config.loader import load_config, get_data_dir
    from nanobot.bus.queue import MessageBus
    from nanobot.agent.loop import AgentLoop
    from nanobot.channels.manager import ChannelManager
    from nanobot.session.manager import SessionManager
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronJob
    from nanobot.heartbeat.service import HeartbeatService
    from loguru import logger
    
    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)
    
    console.print(f"{__logo__} Starting nanobot gateway on port {port}...")
    
    config = load_config()
    bus = MessageBus()
    provider = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)
    
    # Create cron service first (callback set after agent creation)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)
    
    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        search_config=config.search,
        memory_config=config.memory,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        idle_intervention=config.agents.defaults.idle_intervention,
        loop_detection_enabled=config.agents.defaults.loop_detection_enabled,
        loop_window=config.agents.defaults.loop_window,
        loop_warn_threshold=config.agents.defaults.loop_warn_threshold,
        loop_critical_threshold=config.agents.defaults.loop_critical_threshold,
        loop_break_threshold=config.agents.defaults.loop_break_threshold,
        model_fallbacks=config.agents.defaults.model_fallbacks,
        failover_retry_once=config.agents.defaults.failover_retry_once,
        context_guard_min_tokens=config.agents.defaults.context_guard_min_tokens,
        context_guard_warn_tokens=config.agents.defaults.context_guard_warn_tokens,
        tool_result_max_chars=config.agents.defaults.tool_result_max_chars,
        compaction_enabled=config.agents.defaults.compaction_enabled,
        compaction_target_ratio=config.agents.defaults.compaction_target_ratio,
    )
    # Setup cron callbacks (agent mode and direct-delivery mode)
    async def on_cron_job(job: CronJob) -> str | None:
        """Agent mode: process scheduled job via AgentLoop."""
        from nanobot.bus.events import OutboundMessage

        response: str | None = None
        try:
            session_manager.delete(f"cron:{job.id}")
            response = await asyncio.wait_for(
                agent.process_direct(
                    job.payload.message,
                    session_key=f"cron:{job.id}",
                    channel=job.payload.channel or "cli",
                    chat_id=job.payload.to or "direct",
                ),
                timeout=120,
            )
            logger.info(f"Cron agent job '{job.name}' response: {len(response or '')} chars")
        except asyncio.TimeoutError:
            response = f"Cron job timed out: {job.name}"
            logger.error(f"Cron agent job '{job.name}' timed out after 120s")
        except (RuntimeError, ValueError, OSError) as e:
            response = f"Cron job failed: {job.name}\nError: {e}"
            logger.error(f"Cron agent job '{job.name}' failed: {e}")

        if not response or not response.strip():
            response = f"Cron job completed with empty output: {job.name}"
            logger.warning(f"Cron agent job '{job.name}' produced empty response")

        if job.payload.deliver and job.payload.to:
            await bus.publish_outbound(OutboundMessage(
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to,
                content=response,
            ))
        return response

    async def on_cron_deliver(job: CronJob) -> None:
        """Remind mode: directly publish static message without agent processing."""
        if job.payload.deliver and job.payload.to and job.payload.message:
            from nanobot.bus.events import OutboundMessage
            await bus.publish_outbound(OutboundMessage(
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to,
                content=job.payload.message,
            ))

    cron.on_job = on_cron_job
    cron.on_deliver = on_cron_deliver
    
    # Create heartbeat service
    async def on_heartbeat(prompt: str) -> str:
        """Execute heartbeat through the agent."""
        return await agent.process_direct(prompt, session_key="heartbeat")
    
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        on_heartbeat=on_heartbeat,
        interval_s=30 * 60,  # 30 minutes
        enabled=True
    )
    
    # Create channel manager
    channels = ChannelManager(config, bus, session_manager=session_manager)
    
    # Optional: connect Telegram status reporter for real-time progress updates.
    if "telegram" in channels.channels:
        from nanobot.channels.telegram_reporter import TelegramStatusReporter
        tg_channel = channels.channels["telegram"]
        
        def _telegram_reporter_factory(channel: str, chat_id: str):
            """Create Telegram status reporter for the current chat when available."""
            if channel == "telegram" and tg_channel._app and tg_channel._app.bot:
                return TelegramStatusReporter(
                    bot=tg_channel._app.bot,
                    chat_id=int(chat_id),
                )
            return None
        
        agent.reporter_factory = _telegram_reporter_factory
    
    if channels.enabled_channels:
        console.print(f"[green]OK[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")
    
    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]OK[/green] Cron: {cron_status['jobs']} scheduled jobs")
    
    console.print("[green]OK[/green] Heartbeat: every 30m")
    
    async def run():
        try:
            await cron.start()
            await heartbeat.start()
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()
    
    _run_async(run())




# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show nanobot runtime logs during chat"),
):
    """Interact with the agent directly."""
    from nanobot.config.loader import load_config
    from nanobot.bus.queue import MessageBus
    from nanobot.agent.loop import AgentLoop
    from loguru import logger
    
    config = load_config()
    
    bus = MessageBus()
    provider = _make_provider(config)

    if logs:
        logger.enable("nanobot")
    else:
        logger.disable("nanobot")
    
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        search_config=config.search,
        memory_config=config.memory,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        idle_intervention=config.agents.defaults.idle_intervention,
        loop_detection_enabled=config.agents.defaults.loop_detection_enabled,
        loop_window=config.agents.defaults.loop_window,
        loop_warn_threshold=config.agents.defaults.loop_warn_threshold,
        loop_critical_threshold=config.agents.defaults.loop_critical_threshold,
        loop_break_threshold=config.agents.defaults.loop_break_threshold,
        model_fallbacks=config.agents.defaults.model_fallbacks,
        failover_retry_once=config.agents.defaults.failover_retry_once,
        context_guard_min_tokens=config.agents.defaults.context_guard_min_tokens,
        context_guard_warn_tokens=config.agents.defaults.context_guard_warn_tokens,
        tool_result_max_chars=config.agents.defaults.tool_result_max_chars,
        compaction_enabled=config.agents.defaults.compaction_enabled,
        compaction_target_ratio=config.agents.defaults.compaction_target_ratio,
    )
    
    # Show spinner when logs are off (no output to miss); skip when logs are on
    def _thinking_ctx():
        if logs:
            from contextlib import nullcontext
            return nullcontext()
        return console.status("[dim]nanobot is thinking...[/dim]", spinner="dots")

    if message:
        # Single message mode
        async def run_once():
            with _thinking_ctx():
                response = await agent_loop.process_direct(message, session_id)
            _print_agent_response(response, render_markdown=markdown)
        
        _run_async(run_once())
    else:
        # Interactive mode
        _enable_line_editing()
        console.print(f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n")

        # input() runs in a worker thread that can't be cancelled.
        # Without this handler, asyncio.run() would hang waiting for it.
        def _exit_on_sigint(signum, frame):
            _save_history()
            _restore_terminal()
            console.print("\nGoodbye!")
            os._exit(0)

        signal.signal(signal.SIGINT, _exit_on_sigint)
        
        async def run_interactive():
            while True:
                try:
                    _flush_pending_tty_input()
                    user_input = await _read_interactive_input_async()
                    command = user_input.strip()
                    if not command:
                        continue

                    if _is_exit_command(command):
                        _save_history()
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    
                    with _thinking_ctx():
                        response = await agent_loop.process_direct(user_input, session_id)
                    _print_agent_response(response, render_markdown=markdown)
                except KeyboardInterrupt:
                    _save_history()
                    _restore_terminal()
                    console.print("\nGoodbye!")
                    break
                except EOFError:
                    _save_history()
                    _restore_terminal()
                    console.print("\nGoodbye!")
                    break
        
        _run_async(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from nanobot.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # WhatsApp
    wa = config.channels.whatsapp
    table.add_row(
        "WhatsApp",
        "yes" if wa.enabled else "no",
        wa.bridge_url
    )

    dc = config.channels.discord
    table.add_row(
        "Discord",
        "yes" if dc.enabled else "no",
        dc.gateway_url
    )

    # Feishu
    fs = config.channels.feishu
    fs_config = f"app_id: {fs.app_id[:10]}..." if fs.app_id else "[dim]not configured[/dim]"
    table.add_row(
        "Feishu",
        "yes" if fs.enabled else "no",
        fs_config
    )

    # Mochat
    mc = config.channels.mochat
    mc_base = mc.base_url or "[dim]not configured[/dim]"
    table.add_row(
        "Mochat",
        "yes" if mc.enabled else "no",
        mc_base
    )
    
    # Telegram
    tg = config.channels.telegram
    tg_config = "configured" if tg.token else "[dim]not configured[/dim]"
    table.add_row(
        "Telegram",
        "yes" if tg.enabled else "no",
        tg_config
    )

    # Slack
    slack = config.channels.slack
    slack_config = "socket" if slack.app_token and slack.bot_token else "[dim]not configured[/dim]"
    table.add_row(
        "Slack",
        "yes" if slack.enabled else "no",
        slack_config
    )

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess
    
    # User's bridge location
    user_bridge = Path.home() / ".nanobot" / "bridge"
    
    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge
    
    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)
    
    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # nanobot/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)
    
    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge
    
    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall nanobot")
        raise typer.Exit(1)
    
    console.print(f"{__logo__} Setting up bridge...")
    
    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))
    
    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)
        
        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)
        
        console.print("[green]OK[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)
    
    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess
    
    bridge_dir = _get_bridge_dir()
    
    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")
    
    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# Cron Commands
# ============================================================================

cron_app = typer.Typer(help="Manage scheduled tasks")
app.add_typer(cron_app, name="cron")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
):
    """List scheduled jobs."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService
    
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)
    
    jobs = service.list_jobs(include_disabled=all)
    
    if not jobs:
        console.print("No scheduled jobs.")
        return
    
    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Schedule")
    table.add_column("Status")
    table.add_column("Next Run")
    
    import time
    for job in jobs:
        # Format schedule
        if job.schedule.kind == "every":
            sched = f"every {(job.schedule.every_ms or 0) // 1000}s"
        elif job.schedule.kind == "cron":
            sched = job.schedule.expr or ""
        else:
            sched = "one-time"
        
        # Format next run
        next_run = ""
        if job.state.next_run_at_ms:
            next_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(job.state.next_run_at_ms / 1000))
            next_run = next_time
        
        status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"
        
        table.add_row(job.id, job.name, sched, status, next_run)
    
    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", "-n", help="Job name"),
    message: str = typer.Option(..., "--message", "-m", help="Message for agent"),
    every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
    at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
    deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
    to: str = typer.Option(None, "--to", help="Recipient for delivery"),
    channel: str = typer.Option(None, "--channel", help="Channel for delivery (e.g. 'telegram', 'whatsapp')"),
):
    """Add a scheduled job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronSchedule
    
    # Determine schedule type
    if every:
        schedule = CronSchedule(kind="every", every_ms=every * 1000)
    elif cron_expr:
        schedule = CronSchedule(kind="cron", expr=cron_expr)
    elif at:
        import datetime
        try:
            dt = datetime.datetime.fromisoformat(at)
        except ValueError:
            console.print("[red]Error: --at must be ISO datetime, e.g. 2026-02-16T09:00:00[/red]")
            raise typer.Exit(1)
        schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
    else:
        console.print("[red]Error: Must specify --every, --cron, or --at[/red]")
        raise typer.Exit(1)
    
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)
    
    try:
        job = service.add_job(
            name=name,
            schedule=schedule,
            message=message,
            deliver=deliver,
            to=to,
            channel=channel,
        )
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
    
    console.print(f"[green]OK[/green] Added job '{job.name}' ({job.id})")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService
    
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)
    
    if service.remove_job(job_id):
        console.print(f"[green]OK[/green] Removed job {job_id}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID"),
    disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
):
    """Enable or disable a job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService
    
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)
    
    job = service.enable_job(job_id, enabled=not disable)
    if job:
        status = "disabled" if disable else "enabled"
        console.print(f"[green]OK[/green] Job '{job.name}' {status}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
    force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
):
    """Manually run a job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService
    
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)
    
    async def run():
        return await service.run_job(job_id, force=force)
    
    if _run_async(run()):
        console.print("[green]OK[/green] Job executed")
    else:
        console.print(f"[red]Failed to run job {job_id}[/red]")


# ============================================================================
# Search Commands
# ============================================================================

search_app = typer.Typer(help="Manage local knowledge search")
app.add_typer(search_app, name="search")


def _search_db_path(config) -> Path:
    if config.search.db_path:
        return Path(config.search.db_path).expanduser()
    return config.workspace_path / "search" / "index.sqlite"


def _open_search_store(config):
    from nanobot.search.store import SearchStore

    db_path = _search_db_path(config)
    try:
        return SearchStore(db_path)
    except OSError as e:
        console.print(f"[red]Failed to open search DB at {db_path}: {e}[/red]")
        raise typer.Exit(1) from e


def _merge_hybrid_results(bm25_results, vector_results, limit: int):
    from nanobot.search.store import SearchResult

    merged: dict[str, dict[str, SearchResult | None]] = {}
    for item in bm25_results:
        merged.setdefault(item.filepath, {"bm25": None, "vector": None})["bm25"] = item
    for item in vector_results:
        merged.setdefault(item.filepath, {"bm25": None, "vector": None})["vector"] = item

    out = []
    for entry in merged.values():
        bm = entry["bm25"]
        vec = entry["vector"]
        if bm and vec:
            score = (bm.score * 0.45) + (vec.score * 0.55)
            base = vec if vec.score >= bm.score else bm
            out.append(
                SearchResult(
                    filepath=base.filepath,
                    display_path=base.display_path,
                    title=base.title,
                    hash=base.hash,
                    docid=base.docid,
                    collection=base.collection,
                    modified_at=base.modified_at,
                    body_length=base.body_length,
                    snippet=base.snippet,
                    score=score,
                    source="hybrid",
                )
            )
        elif bm:
            out.append(bm)
        elif vec:
            out.append(vec)

    out.sort(key=lambda x: x.score, reverse=True)
    return out[:limit]


@search_app.command("status")
def search_status():
    """Show local search index status."""
    from nanobot.config.loader import load_config
    config = load_config()
    if not config.search.enabled:
        console.print("[yellow]Search is disabled in config.search.enabled[/yellow]")
        return

    store = _open_search_store(config)
    try:
        status = store.get_status()
    finally:
        store.close()

    console.print("Search Status\n")
    console.print(f"DB: {status['db_path']}")
    console.print(f"Documents: {status['active_documents']}/{status['total_documents']} active")
    console.print(f"Content blobs: {status['content_blobs']}")
    console.print(f"FTS rows: {status['fts_rows']}")
    console.print(f"Vector enabled: {config.search.vector_enabled}")
    console.print(f"Embedding model: {config.search.embedding_model}")
    console.print(f"Vector chunks: {status['vector_chunks']}")
    if status["collections"]:
        table = Table(title="Collections")
        table.add_column("Name", style="cyan")
        table.add_column("Active", style="green")
        table.add_column("Total", style="yellow")
        for item in status["collections"]:
            table.add_row(item["name"], str(item["active"]), str(item["total"]))
        console.print(table)
    if status["vector_models"]:
        table = Table(title="Vector Models")
        table.add_column("Model", style="cyan")
        table.add_column("Chunks", style="green")
        table.add_column("Documents", style="yellow")
        for item in status["vector_models"]:
            table.add_row(item["model"], str(item["chunks"]), str(item["documents"]))
        console.print(table)


@search_app.command("reindex")
def search_reindex(
    directory: list[str] | None = typer.Option(None, "--dir", "-d", help="Directory to index (repeatable)"),
    collection: str = typer.Option("memory", "--collection", "-c", help="Collection name"),
    pattern: str = typer.Option("**/*.md", "--pattern", "-p", help="Glob pattern"),
):
    """Rebuild local search index."""
    from nanobot.config.loader import load_config
    from nanobot.search.indexer import Indexer
    config = load_config()
    if not config.search.enabled:
        console.print("[yellow]Search is disabled in config.search.enabled[/yellow]")
        raise typer.Exit(1)

    targets = directory if directory else config.search.index_dirs
    dirs = [Path(d).expanduser() if Path(d).is_absolute() else (config.workspace_path / d) for d in targets]

    store = _open_search_store(config)
    try:
        indexer = Indexer(store=store, workspace=config.workspace_path)
        result = indexer.full_index(directories=dirs, collection=collection, pattern=pattern)
    finally:
        store.close()

    console.print(
        "[green]OK[/green] Reindex complete "
        f"(indexed={result['indexed']}, updated={result['updated']}, unchanged={result['unchanged']}, "
        f"removed={result['removed']}, skipped={result['skipped']})"
    )


@search_app.command("query")
def search_query(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(None, "--limit", "-l", help="Max results"),
    collection: str = typer.Option(None, "--collection", "-c", help="Optional collection filter"),
    semantic: bool | None = typer.Option(
        None,
        "--semantic/--no-semantic",
        help="Use semantic/hybrid search when embeddings are available",
    ),
):
    """Query local search index."""
    from nanobot.config.loader import load_config
    from nanobot.search.embedder import SentenceTransformerEmbedder

    config = load_config()
    if not config.search.enabled:
        console.print("[yellow]Search is disabled in config.search.enabled[/yellow]")
        raise typer.Exit(1)

    use_limit = limit or config.search.default_limit
    use_semantic = config.search.vector_enabled if semantic is None else semantic
    store = _open_search_store(config)
    try:
        bm25_results = store.search(
            query=query,
            limit=use_limit,
            min_score=config.search.min_score,
            collection=collection,
        )
        results = bm25_results
        mode = "bm25"
        if use_semantic:
            embedder = None
            try:
                embedder = SentenceTransformerEmbedder(config.search.embedding_model)
            except RuntimeError as e:
                console.print(f"[yellow]Semantic disabled ({e}); using BM25 only.[/yellow]")

            if embedder is not None:
                vector_results = store.search_vector(
                    embedder.embed_query(query),
                    model=config.search.embedding_model,
                    limit=use_limit,
                    min_score=config.search.min_score,
                    collection=collection,
                )
                if bm25_results and vector_results:
                    results = _merge_hybrid_results(bm25_results, vector_results, use_limit)
                    mode = "hybrid"
                elif vector_results:
                    results = vector_results
                    mode = "vector"
    finally:
        store.close()

    if not results:
        console.print("No results.")
        return

    table = Table(title=f"Search Results ({len(results)})")
    table.caption = f"mode={mode}"
    table.add_column("Score", style="green")
    table.add_column("Source", style="magenta")
    table.add_column("Path", style="cyan")
    table.add_column("Title", style="yellow")
    table.add_column("Snippet")
    for item in results:
        table.add_row(
            f"{item.score:.3f}",
            item.source,
            item.display_path,
            item.title,
            item.snippet,
        )
    console.print(table)


@search_app.command("embed")
def search_embed(
    directory: list[str] | None = typer.Option(None, "--dir", "-d", help="Directory to index (repeatable)"),
    collection: str = typer.Option("memory", "--collection", "-c", help="Collection name"),
    pattern: str = typer.Option("**/*.md", "--pattern", "-p", help="Glob pattern"),
    model: str | None = typer.Option(None, "--model", help="Embedding model name"),
    force: bool = typer.Option(False, "--force", help="Re-embed even if vectors already exist"),
    reindex: bool = typer.Option(True, "--reindex/--no-reindex", help="Run full text reindex before embedding"),
    persist: bool = typer.Option(
        True, "--persist/--no-persist", help="Persist vector settings to config"
    ),
    batch_size: int | None = typer.Option(None, "--batch-size", help="Embedding batch size"),
    chunk_size: int | None = typer.Option(None, "--chunk-size", help="Chunk size in words"),
    chunk_overlap: float | None = typer.Option(None, "--chunk-overlap", help="Chunk overlap ratio (0-0.5)"),
):
    """Enable and build semantic vector search embeddings."""
    from nanobot.config.loader import load_config, save_config
    from nanobot.exceptions import ConfigError
    from nanobot.search.embedder import SentenceTransformerEmbedder
    from nanobot.search.indexer import Indexer

    config = load_config()
    if not config.search.enabled:
        console.print("[yellow]Search is disabled in config.search.enabled[/yellow]")
        raise typer.Exit(1)

    embedding_model = model or config.search.embedding_model
    use_batch_size = batch_size or config.search.embedding_batch_size
    use_chunk_size = chunk_size or config.search.embedding_chunk_size
    use_chunk_overlap = (
        config.search.embedding_chunk_overlap if chunk_overlap is None else chunk_overlap
    )
    if use_batch_size < 1:
        console.print("[red]--batch-size must be >= 1[/red]")
        raise typer.Exit(1)
    if use_chunk_size < 100:
        console.print("[red]--chunk-size must be >= 100[/red]")
        raise typer.Exit(1)
    if not (0.0 <= use_chunk_overlap <= 0.5):
        console.print("[red]--chunk-overlap must be between 0.0 and 0.5[/red]")
        raise typer.Exit(1)

    targets = directory if directory else config.search.index_dirs
    dirs = [Path(d).expanduser() if Path(d).is_absolute() else (config.workspace_path / d) for d in targets]

    store = _open_search_store(config)
    try:
        indexer = Indexer(store=store, workspace=config.workspace_path)
        index_stats = None
        if reindex:
            index_stats = indexer.full_index(directories=dirs, collection=collection, pattern=pattern)

        try:
            embedder = SentenceTransformerEmbedder(embedding_model)
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            console.print("[yellow]Install it with: pip install sentence-transformers[/yellow]")
            raise typer.Exit(1) from e

        embed_stats = indexer.embed_documents(
            embedder=embedder,
            collection=collection,
            force=force,
            chunk_size=use_chunk_size,
            chunk_overlap=use_chunk_overlap,
            batch_size=use_batch_size,
        )
        status = store.get_status()
    finally:
        store.close()

    if persist:
        config.search.vector_enabled = True
        config.search.embedding_model = embedding_model
        config.search.embedding_batch_size = use_batch_size
        config.search.embedding_chunk_size = use_chunk_size
        config.search.embedding_chunk_overlap = use_chunk_overlap
        try:
            save_config(config)
        except ConfigError as e:
            console.print(f"[yellow]Embedding built, but failed to persist config: {e}[/yellow]")

    console.print("[green]OK[/green] Semantic embeddings complete")
    console.print(f"Model: {embedding_model}")
    if index_stats is not None:
        console.print(
            "Reindex: "
            f"indexed={index_stats['indexed']}, updated={index_stats['updated']}, "
            f"unchanged={index_stats['unchanged']}, removed={index_stats['removed']}, "
            f"skipped={index_stats['skipped']}"
        )
    console.print(
        "Embed: "
        f"docs={embed_stats['docs_embedded']}/{embed_stats['docs_considered']}, "
        f"chunks={embed_stats['chunks_embedded']}, skipped={embed_stats['docs_skipped']}"
    )
    console.print(f"Vector chunks in DB: {status['vector_chunks']}")


# ============================================================================
# Memory Commands
# ============================================================================

memory_app = typer.Typer(help="Manage structured memories")
app.add_typer(memory_app, name="memory")


def _memory_root(config) -> Path:
    return config.workspace_path / "memory" / "memories"


@memory_app.command("status")
def memory_status():
    """Show structured memory status."""
    from nanobot.config.loader import load_config

    config = load_config()
    root = _memory_root(config)
    profile = root / "profile.md"

    console.print("Memory Status\n")
    console.print(f"Root: {root}")
    console.print(f"Profile: {'exists' if profile.exists() else 'missing'}")

    table = Table(title="Categories")
    table.add_column("Category", style="cyan")
    table.add_column("Count", style="green")
    table.add_column("Last Updated", style="yellow")
    for category in ("preferences", "entities", "events", "cases", "patterns"):
        cat_dir = root / category
        files = sorted((p for p in cat_dir.glob('*.md') if p.is_file()), key=lambda p: p.stat().st_mtime)
        updated = "-" if not files else files[-1].stat().st_mtime
        if updated == "-":
            display = "-"
        else:
            import time

            display = time.strftime("%Y-%m-%d %H:%M", time.localtime(updated))
        table.add_row(category, str(len(files)), display)
    console.print(table)


@memory_app.command("list")
def memory_list(
    category: str | None = typer.Option(None, "--category", "-c", help="Category filter"),
):
    """List memories with L0 abstracts."""
    from nanobot.config.loader import load_config

    config = load_config()
    root = _memory_root(config)
    categories = [category] if category else ["preferences", "entities", "events", "cases", "patterns"]

    rows: list[tuple[str, str, str]] = []
    for cat in categories:
        cat_dir = root / cat
        if not cat_dir.exists():
            continue
        for fp in sorted(cat_dir.glob("*.md")):
            try:
                abstract = fp.read_text(encoding="utf-8").splitlines()[0].strip()
            except (OSError, IndexError):
                abstract = ""
            rel = fp.relative_to(config.workspace_path).as_posix()
            rows.append((cat, rel, abstract))

    if not rows:
        console.print("No memories found.")
        return

    table = Table(title="Memories")
    table.add_column("Category", style="cyan")
    table.add_column("Path", style="green")
    table.add_column("Abstract")
    for row in rows:
        table.add_row(*row)
    console.print(table)


@memory_app.command("show")
def memory_show(path: str = typer.Argument(..., help="Workspace-relative memory path")):
    """Show full memory detail."""
    from nanobot.config.loader import load_config
    from nanobot.agent.memory import MemoryStore

    config = load_config()
    store = MemoryStore(config.workspace_path)
    content = store.get_memory_detail(path)
    if not content:
        console.print(f"[red]Not found or not readable: {path}[/red]")
        raise typer.Exit(1)
    console.print(content)


@memory_app.command("compress")
def memory_compress(
    session: str = typer.Option("cli:default", "--session", "-s", help="Session key"),
):
    """Run memory compression for one session immediately."""
    from nanobot.config.loader import load_config
    from nanobot.session.manager import SessionManager
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    config = load_config()
    sm = SessionManager(config.workspace_path)
    target = sm.get_or_create(session)
    if not target.messages:
        console.print("[yellow]Session is empty, nothing to compress.[/yellow]")
        return

    provider = _make_provider(config)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        search_config=config.search,
        memory_config=config.memory,
        exec_config=config.tools.exec,
        idle_intervention=config.agents.defaults.idle_intervention,
        loop_detection_enabled=config.agents.defaults.loop_detection_enabled,
        loop_window=config.agents.defaults.loop_window,
        loop_warn_threshold=config.agents.defaults.loop_warn_threshold,
        loop_critical_threshold=config.agents.defaults.loop_critical_threshold,
        loop_break_threshold=config.agents.defaults.loop_break_threshold,
        model_fallbacks=config.agents.defaults.model_fallbacks,
        failover_retry_once=config.agents.defaults.failover_retry_once,
        context_guard_min_tokens=config.agents.defaults.context_guard_min_tokens,
        context_guard_warn_tokens=config.agents.defaults.context_guard_warn_tokens,
        tool_result_max_chars=config.agents.defaults.tool_result_max_chars,
        compaction_enabled=config.agents.defaults.compaction_enabled,
        compaction_target_ratio=config.agents.defaults.compaction_target_ratio,
    )

    async def run():
        return await loop._compress_session_for_new(target)

    result = _run_async(run())
    loop.stop()
    if result is None:
        console.print("[red]Compression failed.[/red]")
        raise typer.Exit(1)
    console.print(
        f"[green]OK[/green] created={result.created}, merged={result.merged}, skipped={result.skipped}"
    )


@memory_app.command("clear")
def memory_clear(
    category: str | None = typer.Option(None, "--category", "-c", help="Category to clear"),
):
    """Clear structured memories by category or all categories."""
    from nanobot.config.loader import load_config

    config = load_config()
    root = _memory_root(config)
    categories = (
        [category]
        if category
        else ["preferences", "entities", "events", "cases", "patterns"]
    )

    removed = 0
    for cat in categories:
        cat_dir = root / cat
        if not cat_dir.exists():
            continue
        for fp in cat_dir.glob("*.md"):
            if fp.is_file():
                fp.unlink(missing_ok=True)
                removed += 1
    console.print(f"[green]OK[/green] removed {removed} memory files")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show nanobot status."""
    from nanobot.config.loader import load_config, get_config_path

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} nanobot Status\n")

    console.print(f"Config: {config_path} {'[green]OK[/green]' if config_path.exists() else '[red]X[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]OK[/green]' if workspace.exists() else '[red]X[/red]'}")

    if config_path.exists():
        from nanobot.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")
        
        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]OK {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {'[green]OK[/green]' if has_key else '[dim]not set[/dim]'}")


if __name__ == "__main__":
    app()
