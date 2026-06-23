import os
import threading
import time
import requests

import bot


def get_ping_url(port: int) -> str:
    # Prefer an explicit SELF_URL (set in Render), otherwise use Render external URL, fallback to localhost
    explicit = os.getenv('SELF_URL') or os.getenv('RENDER_EXTERNAL_URL','https://vantageexchangebot.onrender.com')
    if explicit:
        return explicit.rstrip('/') + '/health'
    return f'http://127.0.0.1:{port}/health'


def self_ping_loop(url: str, interval_seconds: int = 600):
    session = requests.Session()
    while True:
        try:
            resp = session.get(url, timeout=10)
            bot.logging.info('Self-ping %s -> %s', url, resp.status_code)
        except Exception as exc:
            bot.logging.exception('Self-ping failed for %s: %s', url, exc)
        time.sleep(interval_seconds)


def main():
    port = int(os.getenv('PORT', '5000'))

    # Ensure bot runs in background (import may already have started it)
    try:
        bot.start_bot_background()
    except Exception:
        bot.logging.exception('Failed to start bot background')

    # Start self-ping thread to keep Render from idling
    ping_url = get_ping_url(port)
    t = threading.Thread(target=self_ping_loop, args=(ping_url, 600), daemon=True)
    t.start()

    # Run the Flask app in foreground so Render keeps the service alive
    try:
        bot.run_health_server()
    except Exception:
        bot.logging.exception('Failed to start health server')


if __name__ == '__main__':
    main()
