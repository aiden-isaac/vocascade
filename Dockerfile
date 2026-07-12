# vocascade host server — the flagship install (deployment spec).
# The host touches no audio hardware (the edge client owns mic/speaker), so it
# containerizes cleanly. CPU-only; works on x86_64 and aarch64.
#
#   docker compose up          (see docker-compose.yaml)
#
# The edge client is NOT containerized — install it natively:
#   pip install "vocascade[edge] @ git+https://github.com/aiden-isaac/vocascade"

FROM python:3.11-slim

# CPU-only torch (pulled in by silero-vad): the default PyPI wheel bundles
# multi-GB CUDA libraries that a CPU-only host never uses.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

# Everything stateful (piper voices, whisper model cache, ~/.vocascade) keys
# off HOME — one volume persists it all across container recreation.
ENV HOME=/data
RUN useradd --create-home --home-dir /data --uid 1000 vocascade \
    && chown -R vocascade:vocascade /data
USER vocascade
VOLUME /data

EXPOSE 8005
CMD ["vocascade"]
