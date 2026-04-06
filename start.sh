#!/bin/sh
python3 -m rise.workers.funding_circle.runner &
exec python3 -m gunicorn -k uvicorn.workers.UvicornWorker rise.api.server:app -b 0.0.0.0:8000 --workers 2 --timeout 120