"""
satellite.py — deprecated entry point. The edge/satellite client now lives at
``vocascade/edge/__main__.py`` (US8). Run it with::

    .venv/bin/python -m vocascade.edge

This shim preserves the old ``python satellite.py`` invocation by delegating to
the new module so existing scripts keep working.
"""

from vocascade.edge.__main__ import main, SatelliteClient, perform_client_handshake  # noqa: F401

if __name__ == "__main__":
    main()
