"""Running as an Antigravity sidecar (UI plugin in the app's side pane).

The app starts `python study.py serve --sidecar` and passes, via the environment:
  ANTIGRAVITY_SIDECAR_WEB_PORT   the port to listen on (loopback only; the app's gateway forwards to it,
                                 including for remote control)
  ANTIGRAVITY_SIDECAR_UI_TOKEN   requests must carry it (X-Sidecar-Token header or ?token=); the panel URL has it
  ANTIGRAVITY_AGENTAPI_EXE       language-server executable; `<exe> agentapi ...` talks to conversations
  ANTIGRAVITY_PROJECT_ID         the sidecar's project (used only when starting a new conversation)
Mirrors sidecar_sdk (index.mjs / preload.js shipped with the app), reimplemented in Python.
"""
import json
import os
import subprocess

SIDECAR_ID = "study-engine"
PLUGIN_NAME = "study"             # ~/.gemini/config/plugins/study/
SIDECAR_FOLDER = "panel"          # sidecar id in the app: "study/panel"

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor"
 stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
<circle cx="12" cy="19" r="2.2"/><circle cx="6" cy="11" r="2.2"/><circle cx="18" cy="11" r="2.2"/>
<circle cx="12" cy="4" r="2.2"/><path d="M12 16.8 7.3 12.9M12 16.8l4.7-3.9M7.4 9.3 10.6 5.6M16.6 9.3l-3.2-3.7"/>
</svg>
"""


def plugin_manifest():
    return {"name": PLUGIN_NAME, "displayName": "Study", "version": "0.1.0",
            "description": "Study engine side panel: prerequisite graph coloured by what you know, inbox for new "
                           "material, Anki review, and 'Teach me this' for any concept.",
            "logo": "assets/logo.svg"}


def env():
    return {
        "port": os.environ.get("ANTIGRAVITY_SIDECAR_WEB_PORT"),
        "token": os.environ.get("ANTIGRAVITY_SIDECAR_UI_TOKEN"),
        "agentapi": os.environ.get("ANTIGRAVITY_AGENTAPI_EXE"),
        "project": os.environ.get("ANTIGRAVITY_PROJECT_ID"),
    }


class AgentApiError(Exception):
    pass


def agentapi(args, project_id=None, timeout=60):
    e = env()
    exe = e["agentapi"]
    if not exe:
        raise AgentApiError("not running inside the Antigravity app (no ANTIGRAVITY_AGENTAPI_EXE)")
    environ = dict(os.environ)
    if project_id or e["project"]:
        environ["ANTIGRAVITY_PROJECT_ID"] = project_id or e["project"]
    try:
        p = subprocess.run([exe, "agentapi", *args], capture_output=True, text=True, encoding="utf-8",
                           env=environ, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise AgentApiError(f"agentapi failed: {err}")
    if p.returncode != 0:
        raise AgentApiError(f"agentapi exited {p.returncode}: {(p.stderr or p.stdout).strip()[:300]}")
    out = (p.stdout or "").strip()
    try:
        return json.loads(out) if out else {}
    except json.JSONDecodeError:
        return {"output": out}


def send_message(conversation_id, message, title=None):
    args = ["send-message"] + (["--title", title] if title else []) + [conversation_id, message]
    return agentapi(args)


def new_conversation(message, model="flash", project_id=None, title=None):
    """project_id: the Antigravity project to create it in (the app's own sidecar SDK does the same: it sets
    ANTIGRAVITY_PROJECT_ID for the agentapi call). None = the sidecar's project."""
    args = ["new-conversation", "--model", model] + (["--title", title] if title else []) + [message]
    return agentapi(args, project_id=project_id)


def title_of(message):
    """'Topic: **Word embeddings** …' -> 'Study · Word embeddings' (conversation title in the app)."""
    import re
    for line in message.splitlines():
        if line.startswith("Topic:"):
            t = re.sub(r"[*`]", "", line[len("Topic:"):]).strip()
            t = t.split(" (", 1)[0]                              # drop "(graph node …)", "(22 concepts …)"
            t = re.sub(r"^the (concept cluster|course|folder|file)\s+", "", t).rstrip(". ")
            return ("Study · " + t)[:80]
    return "Study session"


def manifest(cfg, python_exe, study_py):
    """sidecar.json for ~/.gemini/config/sidecars/study-engine/."""
    return {
        "command": python_exe,
        "args": [str(study_py), "serve", "--sidecar"],
        "restart_policy": "on-failure",
        "display_name": "Study",
        "description": "Study engine: prerequisite graph of your concepts coloured by what you know, inbox for "
                       "new material, Anki review. 'Teach me this' hands a concept to the conversation.",
        "has_web_ui": True,
        "agent_permissions": {"allows_user_interactions": False},
        "ui_config": {"display_name": "Study",
                      "views": [{"path": "/", "entrypoint": "SIDECAR_UI_ENTRYPOINT_AUX_PANE", "title": "Study"}]},
    }
