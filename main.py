"""
MCP-Server für das taskapp-backend (mit Authentifizierung).
Verwendet das mcp-python-sdk (STDIO).
"""

import os
import sys
from typing import Any, Dict, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# --- 1. Konfiguration ---
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8080/api")
TASKAPP_USER = os.getenv("TASKAPP_USER", "max.power")
TASKAPP_PASSWORD = os.getenv("TASKAPP_PASSWORD", "password456")
AGENT_USER_ID: Optional[int] = None


# --- 2. Authentifizierungs-Logik ---
def create_authenticated_client() -> httpx.Client:
    """
    Diese Funktion wird EINMAL beim Start des Servers aufgerufen.
    Sie meldet sich beim Backend an, erstellt einen Client, der den Token
    automatisch für alle Anfragen verwendet, und ruft die ID des Agenten ab.
    """
    global AGENT_USER_ID
    print(
        f"Versuche Login beim Backend ({BACKEND_URL}) als Benutzer '{TASKAPP_USER}'...",
        file=sys.stderr,
    )

    try:
        # Temporärer Client nur für den Login
        with httpx.Client(base_url=BACKEND_URL) as login_client:
            response = login_client.post(
                "/auth/login",
                json={"username": TASKAPP_USER, "password": TASKAPP_PASSWORD},
            )

            # WICHTIG: Wenn der Login fehlschlägt, MUSS der Server abstürzen.
            response.raise_for_status()

            # Token aus der Antwort extrahieren (Annahme: Standard-OAuth2-Antwort)
            token = response.json().get("token")
            if not token:
                raise ValueError("Can not find 'access_token' in the login response.")

            print("Login successful. Got Bearer token.", file=sys.stderr)

            # --- Permanenten, authentifizierten Client erstellen ---
            headers = {"Authorization": f"Bearer {token}"}
            client = httpx.Client(base_url=BACKEND_URL, headers=headers)

            # --- Eigene User-ID abrufen ---
            print("Rufe eigene User-ID von /api/auth/me ab...", file=sys.stderr)
            me_response = client.get("/auth/me")
            me_response.raise_for_status()
            AGENT_USER_ID = me_response.json().get("id")
            if not AGENT_USER_ID:
                raise ValueError("Can not find 'id' in the /api/auth/me response.")
            print(f"Eigene User-ID ist: {AGENT_USER_ID}", file=sys.stderr)

            return client

    except httpx.HTTPStatusError as e:
        print(
            f"ERROR: Login failed! Status: {e.response.status_code}, Response: {e.response.text}",
            file=sys.stderr,
        )
        raise  # Beendet das Skript
    except httpx.ConnectError as e:
        print(f"ERROR: Backend {BACKEND_URL} unreachable.", file=sys.stderr)
        raise
    except Exception as e:
        print(f"ERROR: Login failed: {e}", file=sys.stderr)
        raise


# --- 3. MCP Server Initialisierung ---
mcp = FastMCP("TaskApp Backend MCP Server")
# Der Client wird hier, beim Laden des Skripts, erstellt und authentifiziert.
# Wenn dies fehlschlägt, startet der MCP-Server gar nicht erst.
client = create_authenticated_client()


# --- 4. Pydantic-Modell für Parameter ---
class GetTasksParams(BaseModel):
    project_id: Optional[int] = Field(
        None, description="Optional: ID of the project to filter by."
    )
    assigned_user_id: Optional[int] = Field(
        None, description="Optional: ID of the user to whom the tasks are assigned."
    )


# --- 5. Tool-Definition ---
@mcp.tool()
def get_tasks(params: GetTasksParams) -> Dict[str, Any]:
    """
    Ruft Aufgaben für ein bestimmtes Projekt ab.
    Wenn keine assigned_user_id angegeben wird, werden die Aufgaben des aktuellen Benutzers (Agent) abgerufen.
    Die Authentifizierung erfolgt automatisch.
    """
    print("Tool called: get_tasks", file=sys.stderr)
    try:
        query_params = {}
        if params.project_id is not None:
            query_params["projectId"] = params.project_id

        user_id_to_query = params.assigned_user_id
        if user_id_to_query is None:
            user_id_to_query = AGENT_USER_ID
            print(
                f"Keine assigned_user_id angegeben, verwende eigene ID: {AGENT_USER_ID}",
                file=sys.stderr,
            )

        if user_id_to_query is not None:
            query_params["assignedToUserId"] = user_id_to_query

        # Abfrage der Tasks erfolgt z.B. mit: GET /api/tasks?projectId=1&assignedToUser=3
        # Der 'client' hat bereits den Bearer-Token im Header.
        response = client.get("/tasks", params=query_params)
        response.raise_for_status()
        return response.json()

    except httpx.HTTPStatusError as e:
        # Meldet einen Backend-Fehler an die KI
        return {
            "error": f"Backend-Fehler: {e.response.status_code}",
            "details": e.response.text,
        }
    except Exception as e:
        return {"error": f"Interner MCP-Fehler: {str(e)}"}


# Add an addition tool
@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b


@mcp.tool()
def get_weather(city: str, unit: str = "celsius") -> str:
    """Get weather for a city."""
    # This would normally call a weather API
    return f"Weather in {city}: 22degrees{unit[0].upper()}"


# Add a dynamic greeting resource
@mcp.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get a personalized greeting"""
    return f"Hello, {name}!"


# Add a prompt
@mcp.prompt()
def greet_user(name: str, style: str = "friendly") -> str:
    """Generate a greeting prompt"""
    styles = {
        "friendly": "Please write a warm, friendly greeting",
        "formal": "Please write a formal, professional greeting",
        "casual": "Please write a casual, relaxed greeting",
    }

    return f"{styles.get(style, styles['friendly'])} for someone named {name}."
