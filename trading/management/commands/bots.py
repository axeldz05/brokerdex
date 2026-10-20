import time
from django.core.management.base import BaseCommand
from django.utils import timezone

from trading.simulation.state import (
    is_simulation_active,
    set_simulation_active,
    get_simulation_stats,
    reset_simulation_stats,
)
from trading.simulation.engine import SimulationEngine


# ANSI color codes for rich terminal streaming
CYAN = '\033[96m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
MAGENTA = '\033[95m'
BLUE = '\033[94m'
BOLD = '\033[1m'
RESET = '\033[0m'

STRATEGY_COLORS = {
    'MOMENTUM': CYAN,
    'VALUE': GREEN,
    'MARKET_MAKER': BLUE,
    'CASUAL': MAGENTA,
    'BATTLE': YELLOW,
}


class Command(BaseCommand):
    help = 'Manage and monitor the autonomous trading bots.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--start',
            action='store_true',
            help='Activate autonomous trading bots.',
        )
        parser.add_argument(
            '--stop',
            action='store_true',
            help='Pause autonomous trading bots.',
        )
        parser.add_argument(
            '--status',
            action='store_true',
            help='Show current bot autonomy status and metrics.',
        )
        parser.add_argument(
            '--reset-stats',
            action='store_true',
            help='Reset simulation statistics counter.',
        )
        parser.add_argument(
            '--stream',
            '--run',
            action='store_true',
            dest='stream',
            help='Run the bot simulation loop in the terminal with live streaming logs.',
        )
        parser.add_argument(
            '--interval',
            type=float,
            default=2.5,
            help='Seconds between simulation ticks in stream mode (default: 2.5).',
        )
        parser.add_argument(
            '--once',
            action='store_true',
            help='Execute a single tick and exit.',
        )
        parser.add_argument(
            '--max-ticks',
            type=int,
            default=None,
            help='Maximum number of ticks to run before stopping.',
        )

    def handle(self, *args, **options):
        if options.get('reset_stats'):
            reset_simulation_stats()
            self.stdout.write(self.style.SUCCESS('Simulation statistics have been reset.'))
            return

        if options.get('stop'):
            set_simulation_active(False)
            self.stdout.write(self.style.WARNING('Bot autonomy has been PAUSED.'))
            return

        if options.get('start'):
            set_simulation_active(True)
            self.stdout.write(self.style.SUCCESS('Bot autonomy is now ACTIVE.'))
            return

        if options.get('status'):
            self._print_status()
            return

        if options.get('once'):
            engine = SimulationEngine()
            events = engine.tick()
            self._print_events(events)
            return

        if options.get('stream'):
            self._run_stream(
                interval=options.get('interval', 2.5),
                max_ticks=options.get('max_ticks'),
            )
            return

        # Default action when no flags given: show status
        self._print_status()

    def _print_status(self):
        active = is_simulation_active()
        stats = get_simulation_stats()
        status_text = self.style.SUCCESS('ACTIVE (RUNNING)') if active else self.style.WARNING('PAUSED')

        self.stdout.write("=" * 60)
        self.stdout.write(f"  BROKERDEX BOT SIMULATION STATUS: {status_text}")
        self.stdout.write("=" * 60)
        self.stdout.write(f"  Total bot events recorded: {stats.get('total_events', 0)}")
        self.stdout.write(f"  Last event at: {stats.get('last_event_at') or 'None'}")
        self.stdout.write("-" * 60)
        self.stdout.write("  Events by Strategy Archetype:")
        for strat, count in stats.get('strategies', {}).items():
            color = STRATEGY_COLORS.get(strat, '')
            self.stdout.write(f"    {color}[{strat:<12}]{RESET} : {count} actions")
        self.stdout.write("=" * 60)
        self.stdout.write("  Tips:")
        self.stdout.write("    manage.py bots --start     (activate bots)")
        self.stdout.write("    manage.py bots --stop      (pause bots)")
        self.stdout.write("    manage.py bots --stream    (live stream feed in terminal)")

    def _print_events(self, events):
        for event in events:
            strat = event.get('strategy', 'INFO')
            color = STRATEGY_COLORS.get(strat, '')
            ts = timezone.now().strftime('%H:%M:%S')
            action = event.get('action', '')
            user = event.get('username', 'bot')
            creature = event.get('creature', '')
            amount = event.get('amount', '')
            details = event.get('details', '')

            self.stdout.write(
                f"[{ts}] {color}{BOLD}[{strat:<12}]{RESET} "
                f"{BOLD}{user:<14}{RESET} -> {action} {amount}x {creature} | {details}"
            )

    def _run_stream(self, interval: float, max_ticks: int | None = None):
        self.stdout.write(self.style.SUCCESS("Starting Brokerdex Bot Simulation Stream..."))
        self.stdout.write(f"Interval: {interval}s per tick. Press Ctrl+C to pause/exit.")
        self.stdout.write("=" * 80)

        engine = SimulationEngine()
        ticks = 0

        try:
            while True:
                if not is_simulation_active():
                    self.stdout.write(self.style.WARNING(
                        f"[{timezone.now().strftime('%H:%M:%S')}] Simulation is currently PAUSED. "
                        "Run 'manage.py bots --start' to resume."
                    ))
                    time.sleep(interval)
                    continue

                events = engine.tick()
                if events:
                    self._print_events(events)

                ticks += 1
                if max_ticks and ticks >= max_ticks:
                    break

                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("\n" + self.style.WARNING("Simulation stream interrupted by user."))
