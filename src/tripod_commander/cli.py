import click
from .tripod import Tripod

@click.group()
def cli():
    """Tripod Commander CLI"""
    pass

@cli.command()
@click.option(
    '--serial-port', '-p',
    default='auto',
    help='Serial port for tripod (e.g. /dev/ttyUSB0 or "auto" to autodetect).'
)
def test_connection(serial_port):
    """
    Test connection by querying the tripod firmware version.
    Sends a 'V' command and prints whatever the ESP8266 replies.
    """
    try:
        # Initialize with dummy angles/frames since we only want to connect
        tripod = Tripod(
            pan_start=0.0, pan_end=0.0,
            tilt_start=0.0, tilt_end=0.0,
            frames=1,
            port=serial_port,
            movement_mode='incremental'
        )
        tripod.connect()

        # Send the version request
        click.echo("[Tripod] Requesting firmware version…")
        tripod.serial.write(b"V\n")
        tripod.serial.flush()

        # Wait up to 2 seconds for a line of response
        line = tripod.serial.readline().decode('ascii', errors='ignore').strip()
        if line:
            click.echo(f"✅ Tripod firmware responded: {line}")
        else:
            click.echo("❌ No response received (timeout).")

        tripod.disconnect()
    except Exception as e:
        click.echo(f"❌ Connection test failed: {e}")

@cli.command()
@click.option(
    '--serial-port', '-p',
    default='auto',
    help='Serial port for tripod (e.g. /dev/ttyUSB0 or "auto" to autodetect).'
)
@click.option(
    '--pan', '-a',
    type=float,
    required=True,
    help='Relative pan delta in degrees (positive or negative).'
)
@click.option(
    '--tilt', '-t',
    type=float,
    required=True,
    help='Relative tilt delta in degrees (positive or negative).'
)
def move(serial_port, pan, tilt):
    """
    Move the tripod by the given pan and tilt angles (one-shot move).
    """
    try:
        tripod = Tripod(
            pan_start=0.0, pan_end=0.0,
            tilt_start=0.0, tilt_end=0.0,
            frames=10,
            port=serial_port,
            movement_mode='incremental',
            settle_time=0.5
        )
        tripod.connect()

        cmd = f"M {pan:.3f} {tilt:.3f}"
        click.echo(f"[Tripod] Sending move command: {cmd}")
        tripod._send_command(cmd, wait_for_ack=True)

        click.echo(f"✅ Move to pan={pan}°, tilt={tilt}° completed.")
        tripod.disconnect()
    except Exception as e:
        click.echo(f"❌ Movement failed: {e}")

if __name__ == '__main__':
    cli()
