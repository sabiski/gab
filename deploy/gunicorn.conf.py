"""Configuration Gunicorn — Gab'Pharma."""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8001")
workers = int(os.environ.get("GUNICORN_WORKERS", max(2, multiprocessing.cpu_count() * 2 + 1)))
threads = int(os.environ.get("GUNICORN_THREADS", "2"))
worker_class = "gthread"
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
keepalive = 5
max_requests = 1000
max_requests_jitter = 50
accesslog = os.environ.get("GUNICORN_ACCESS_LOG", "-")
errorlog = os.environ.get("GUNICORN_ERROR_LOG", "-")
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
capture_output = True
proc_name = "gabpharma"
