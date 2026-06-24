"""
Azure Function App - Chatbot Engine
Maintains a sliding window of the last K conversation turns per session.
"""

import json
import logging
import os
import sys
import yaml
import azure.functions as func
from langchain_nvidia import ChatNVIDIA
from langchain_core.messages import AIMessage, HumanMessage

from src.db import DBHandler
from src.utils import _error
from src.graph import get_graph

logging.basicConfig(
    level=logging.INFO,
    format='\x1b[32m%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)
with open('config/config.yaml', encoding="utf-8") as f:
    config = yaml.safe_load(f)
AUTHORIZED_DOMAINS = config['authorized-websites']

WINDOW_K = int(os.getenv("CONVERSATION_WINDOW_K", "10"))  # last K messages to keep
MAX_MEMORY_TURNS = int(os.getenv("MAX_MEMORY_TURNS", "100"))  # last K messages to keep

### Init DB
_DB_DIR = os.getenv("DB_DIR", ".")
_DB_NAME = "uc-105-chatbot.db"
 
db_handler = DBHandler(db_path=_DB_DIR, db_name=_DB_NAME, max_memory_turns=MAX_MEMORY_TURNS)

### Select LLM
llm = ChatNVIDIA(
    model="meta/llama-3.1-8b-instruct"
    #model="deepseek-ai/deepseek-v3.2"
    )

graph = get_graph(llm=llm, logger=logger, domains=AUTHORIZED_DOMAINS)

@app.route(route="chat", methods=["POST"])
def chat(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/chat
    Body: { "session_id": "...", "message": "user text" }
    Response: { "reply": "...", "history_length": N }
    """
    logger.info("Chat endpoint called.")

    try:
        body = req.get_json()
    except ValueError:
        return _error(400, "Invalid JSON body")

    session_id = body.get("session_id", "default")
    user_message = body.get("message", "").strip()

    if not user_message:
        return _error(400, "message field is required")

    history = db_handler._load_history(session_id)
    logger.info(f'Current history {history}')
    window = history[-WINDOW_K:] if len(history) > WINDOW_K else history
    window = window + [HumanMessage(user_message)]

    logger.info(f"session={session_id}  db_msgs={len(history)}  llm_window={len(window)}")

    try:
        #response = llm.invoke([SystemMessage(SYSTEM_PROMPT)] + window)
        results = graph.invoke({'query': window})
    except Exception as exc:
        logger.error("API error: %s", exc)
        return _error(502, "Upstream API error", str(exc))

    ai_msg: AIMessage = results['query_result']
    db_handler._save_messages(session_id, [HumanMessage(user_message), ai_msg])
    stats = db_handler._session_stats(session_id)
 
    return func.HttpResponse(
        json.dumps({
            "reply": ai_msg.content,
            "session_id": session_id,
            "history_length": stats["total_messages"],
            "window_k": WINDOW_K,
            "max_memory_turns": MAX_MEMORY_TURNS,
        }),
        status_code=200,
        mimetype="application/json",
    )

@app.route(route="session/{session_id}", methods=["DELETE"])
def clear_session(req: func.HttpRequest) -> func.HttpResponse:
    """DELETE /api/session/{session_id} — erase all stored messages for a session."""
    session_id = req.route_params.get("session_id", "")
    db_handler._delete_session(session_id)
    logger.info("Cleared session %s", session_id)
    return func.HttpResponse(
        json.dumps({"cleared": session_id}),
        status_code=200,
        mimetype="application/json",
    )


@app.route(route="session/{session_id}/history", methods=["GET"])
def get_history(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/session/{session_id}/history — return stored messages + stats."""
    session_id = req.route_params.get("session_id", "")
    history = db_handler._load_history(session_id)
    stats = db_handler._session_stats(session_id)
    return func.HttpResponse(
        json.dumps({
            "session_id": session_id,
            "window_k": WINDOW_K,
            "max_memory_turns": MAX_MEMORY_TURNS,
            "stats": stats,
            "messages": [
                {
                    "role": "human" if isinstance(m, HumanMessage) else "ai",
                    "content": m.content,
                }
                for m in history
            ],
        }),
        status_code=200,
        mimetype="application/json",
    )

