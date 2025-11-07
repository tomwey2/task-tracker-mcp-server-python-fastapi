"""
MCP-Server für das taskapp-backend (mit Authentifizierung).
Verwendet das mcp-python-sdk (STDIO).
"""

import os
import sys
from typing import Any, Dict, Optional

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

load_dotenv()

# --- 1. Konfiguration ---
BACKEND_URL = os.getenv("BACKEND_URL", "")
TASKAPP_USER = os.getenv("TASKAPP_USER", "")
TASKAPP_PASSWORD = os.getenv("TASKAPP_PASSWORD", "")
AGENT_USER_ID: int = 0


# --- Validierung der Konfiguration ---
if not BACKEND_URL:
    print("ERROR: Environment variable BACKEND_URL is not set.", file=sys.stderr)
    sys.exit(1)
if not TASKAPP_USER:
    print("ERROR: Environment variable TASKAPP_USER is not set.", file=sys.stderr)
    sys.exit(1)
if not TASKAPP_PASSWORD:
    print("ERROR: Environment variable TASKAPP_PASSWORD is not set.", file=sys.stderr)
    sys.exit(1)


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
            user_id = me_response.json().get("id")
            if not user_id:
                raise ValueError("Can not find 'id' in the /api/auth/me response.")
            AGENT_USER_ID = user_id
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


def _get_tasks(project_id: int, assigned_user_id: int) -> Dict[str, Any]:
    """
    Core logic to retrieve tasks for a specific project and user from the backend.
    This function is not a tool and can be called from other tools.
    """
    try:
        query_params = {
            "projectId": project_id,
            "assignedToUserId": assigned_user_id,
        }

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


def _get_project_id_by_name(project_name: str) -> int:
    """Helper to find a project ID by its name. Raises ValueError or httpx.HTTPStatusError on failure."""
    project_response = client.get("/projects", params={"name": project_name})
    project_response.raise_for_status()
    projects = project_response.json()
    if not projects:
        raise ValueError(f"Project with name '{project_name}' not found.")
    return projects[0]["id"]


# --- 4. Pydantic-Modell für Parameter ---
class GetTasksParams(BaseModel):
    project_id: int = Field(..., description="The ID of the project to filter by.")
    assigned_user_id: int = Field(
        ..., description="The ID of the user to whom the tasks are assigned."
    )


class GetTasksOfProjectParams(BaseModel):
    project_name: str = Field(
        ..., description="The name of the project for which to retrieve tasks."
    )
    assigned_user_id: int = Field(
        ..., description="The ID of the user to whom the tasks are assigned."
    )


class GetMyTasksParams(BaseModel):
    project_id: int = Field(
        ..., description="The ID of the project to get my tasks from."
    )


class GetMyTasksOfProjectParams(BaseModel):
    project_name: str = Field(
        ..., description="The name of the project to get my tasks from."
    )


# --- 5. Tool-Definition ---


@mcp.tool()
def get_tasks(params: GetTasksParams) -> Dict[str, Any]:
    """
    Ruft die Aufgaben für einen bestimmten Benutzer innerhalb eines bestimmten Projekts ab.
    """
    print("Tool called: get_tasks", file=sys.stderr)
    return _get_tasks(
        project_id=params.project_id, assigned_user_id=params.assigned_user_id
    )


@mcp.tool()
def get_tasks_of_project(params: GetTasksOfProjectParams) -> Dict[str, Any]:
    """
    Ruft die Aufgaben für einen bestimmten Benutzer ab, indem nach dem Projektnamen gesucht wird.
    """
    print("Tool called: get_tasks_of_project", file=sys.stderr)
    try:
        project_id = _get_project_id_by_name(params.project_name)
        return _get_tasks(
            project_id=project_id, assigned_user_id=params.assigned_user_id
        )
    except (ValueError, httpx.HTTPStatusError) as e:
        return {
            "error": f"Failed to get tasks for project '{params.project_name}'",
            "details": str(e),
        }


@mcp.tool()
def get_my_tasks(params: GetMyTasksParams) -> Dict[str, Any]:
    """
    Ruft MEINE Aufgaben für ein bestimmtes Projekt ab.
    Verwendet die ID des authentifizierten Agenten.
    """
    print("Tool called: get_my_tasks", file=sys.stderr)
    return _get_tasks(project_id=params.project_id, assigned_user_id=AGENT_USER_ID)


@mcp.tool()
def get_my_tasks_of_project(params: GetMyTasksOfProjectParams) -> Dict[str, Any]:
    """
    Ruft MEINE Aufgaben für ein bestimmtes Projekt ab, indem nach dem Projektnamen gesucht wird.
    Verwendet die ID des authentifizierten Agenten.
    """
    print("Tool called: get_my_tasks_of_project", file=sys.stderr)
    try:
        project_id = _get_project_id_by_name(params.project_name)
        return _get_tasks(project_id=project_id, assigned_user_id=AGENT_USER_ID)
    except (ValueError, httpx.HTTPStatusError) as e:
        return {
            "error": f"Failed to get my tasks for project '{params.project_name}'",
            "details": str(e),
        }
