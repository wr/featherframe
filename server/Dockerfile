# A self-hosted Featherframe server in a container (W-866): for a NAS or home
# server that is not the BirdNET device. The same server install.sh sets up,
# published as ghcr.io/wr/featherframe by the release workflow; see
# docker-compose.yml at the repo root. Build context: the repo root.
#
#     docker build -f server/Dockerfile -t featherframe .
FROM python:3.12-slim

# Quiet hours and the collage run on local time: TZ needs the zone files.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/requirements.txt server/requirements-optional.txt ./
# numba is what makes a colour frame's render fast; the image can afford it.
RUN pip install --no-cache-dir -r requirements.txt -r requirements-optional.txt

COPY server/featherframe ./featherframe
COPY server/templates ./templates
COPY server/static ./static
COPY server/scripts ./scripts

# All state is in /data (a volume). No scans in the image: Audubon's
# illustrations come from the shared library (W-842), each fetched on first
# use and cached under /data/plate-library.
ENV FEATHERFRAME_DATA_DIR=/data \
    FEATHERFRAME_PLATES_DIR=/data/plates \
    FEATHERFRAME_PLATE_LIBRARY=https://plates.featherframe.app \
    FEATHERFRAME_PORT=8181 \
    PYTHONUNBUFFERED=1

VOLUME /data
EXPOSE 8181
CMD ["python", "-m", "featherframe"]
