# The hosted household's own Featherframe server (W-844): the box's server,
# unchanged, in a Container that sleeps between runs. Build context: the repo
# root (hosted/wrangler.jsonc sets it).
FROM python:3.12-slim

WORKDIR /app
COPY server/requirements.txt server/requirements-optional.txt ./
# numba is optional on a Pi; here it is what makes a colour render ~0.1 s.
RUN pip install --no-cache-dir -r requirements.txt -r requirements-optional.txt

COPY server/featherframe ./featherframe
# The colour dither is compiled here, once, into the image (W-948): compiled
# at a start, it cost every wake that draws for a colour kit ~3 s. A cache
# built for "generic" carries no host CPU features, so it matches whatever
# machine the Container lands on.
ENV NUMBA_CACHE_DIR=/app/.numba-cache \
    NUMBA_CPU_NAME=generic
RUN python -c "from featherframe.render import spectra; assert spectra.warm_kernel()"
COPY server/templates ./templates
COPY server/static ./static
COPY server/scripts ./scripts
# The rest of what a start used to compile, compiled here once (W-987): the
# page's templates, and Python's own library and the server as bytecode. The
# base image ships no bytecode, so every start compiled ~100 modules again,
# a third of the CPU its imports took.
ENV FEATHERFRAME_TEMPLATE_CACHE=/app/.jinja-cache
RUN python -c "from featherframe import app; assert app.warm_templates()" \
 && python -m compileall -q -j0 /usr/local/lib/python3.12 /app/featherframe

# One BLAS thread (W-987): numpy otherwise starts a pool sized to the host's
# cores when it is imported, which a quarter of a vCPU pays for at every
# start. The pixels are the same.
ENV OPENBLAS_NUM_THREADS=1

# No scans here: plates come from the shared library (W-842), cached in /data.
# State lives in /data and is synced with the front door (featherframe/hosted.py).
# Nothing on this network to advertise to (W-827).
ENV FEATHERFRAME_DATA_DIR=/data \
    FEATHERFRAME_PLATES_DIR=/app/plates \
    FEATHERFRAME_PLATE_LIBRARY=https://plates.featherframe.app \
    FEATHERFRAME_NO_MDNS=1 \
    FEATHERFRAME_KEEP_ALIVE=75 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["python", "-m", "featherframe", "--port", "8080"]
