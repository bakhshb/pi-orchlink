"""Neutral broker-API client package.

Single home for the broker HTTP transport surface, broker lifecycle helpers,
and lead→worker talk/task envelopes. Domain-layer modules may import this
package without pulling CLI/Typer dependencies into their import graph.
"""

from orchlink.client.messages import (
    THINKING_LEVELS,
    WorkerBridge,
    build_chat_envelope,
    build_task_envelope,
    close_talk,
    close_talk_sync,
    normalize_thinking_level,
    post_envelope,
    say_talk,
    say_talk_sync,
    send_worker,
    send_worker_sync,
    start_talk,
    start_talk_sync,
    summarize_chat_topic,
)
from orchlink.client.broker_client import BrokerClient
from orchlink.client.monitor import fetch_events, fetch_status, format_event
from orchlink.client.sync import (
    broker_get_sync,
    broker_post_sync,
    ensure_broker_running,
    fetch_events_sync,
    fetch_status_sync,
)

__all__ = [
    "BrokerClient",
    "THINKING_LEVELS",
    "WorkerBridge",
    "broker_get_sync",
    "broker_post_sync",
    "build_chat_envelope",
    "build_task_envelope",
    "close_talk",
    "close_talk_sync",
    "ensure_broker_running",
    "fetch_events",
    "fetch_events_sync",
    "fetch_status",
    "fetch_status_sync",
    "format_event",
    "normalize_thinking_level",
    "post_envelope",
    "say_talk",
    "say_talk_sync",
    "send_worker",
    "send_worker_sync",
    "start_talk",
    "start_talk_sync",
    "summarize_chat_topic",
]
