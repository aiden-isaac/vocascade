# Quickstart: Hermes Gateway Integration

This guide assumes you already have the Voice Satellite Core running and want to switch to the Hermes Agent backend.

## Prerequisites

- Local installation of Hermes Agent running on port 8642.

## Configuration

Update your `.env` file to switch the backend to Hermes:

```env
GATEWAY_BACKEND=hermes
HERMES_BASE_URL=http://localhost:8642/v1
```

If you ever need to fallback to OpenClaw:

```env
GATEWAY_BACKEND=openclaw
```

## Running

Start the FastAPI server as usual:
```bash
python -m voice_satellite.server
```

The server will automatically instantiate the correct gateway client based on your configuration.
