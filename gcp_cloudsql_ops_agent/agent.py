"""GCP Cloud SQL Operations Agent with Cloud SQL Admin, Observability, and GCP Knowledge MCP endpoints."""

import logging
import os
from typing import List, Optional

import google.auth
import google.auth.transport.requests
from google.adk.agents.llm_agent import Agent
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StreamableHTTPConnectionParams,
)
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

logger = logging.getLogger(__name__)

MODEL = os.getenv("ADK_MODEL", "gemini-2.5-flash")

# Managed Google Cloud MCP Endpoints (routed via Agent Egress Gateway)
SQLADMIN_MCP_URL = os.getenv("SQLADMIN_MCP_URL", "https://sqladmin.googleapis.com/mcp")
LOGGING_MCP_URL = os.getenv("LOGGING_MCP_URL", "https://logging.googleapis.com/mcp")
MONITORING_MCP_URL = os.getenv("MONITORING_MCP_URL", "https://monitoring.googleapis.com/mcp")
KNOWLEDGE_MCP_URL = os.getenv("KNOWLEDGE_MCP_URL", "https://developerknowledge.googleapis.com/mcp")


def _gcp_agent_identity_headers(_readonly_context=None) -> dict[str, str]:
    """Attach AGENT_IDENTITY OAuth Bearer token and resolved string project ID header to remote MCP calls."""
    headers: dict[str, str] = {}
    try:
        credentials, default_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(google.auth.transport.requests.Request())
        if credentials.token:
            headers["Authorization"] = f"Bearer {credentials.token}"
        raw_proj = (
            os.getenv("GOOGLE_CLOUD_QUOTA_PROJECT")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or default_project
            or "svcprjprd"
        )
        quota_project = "svcprjprd" if str(raw_proj).isdigit() else str(raw_proj)
        headers["X-Goog-User-Project"] = quota_project
    except Exception as exc:
        logger.warning("Failed to refresh AGENT_IDENTITY credentials for MCP call: %s", exc)
    return headers


class BestEffortMcpToolset(McpToolset):
    """Loads filtered MCP tools with AGENT_IDENTITY auth and keeps the agent running if egress denies a server."""

    def __init__(
        self,
        *,
        server_name: str,
        url: str,
        tool_name_prefix: str,
        tool_filter: Optional[List[str]] = None,
    ):
        self._server_name = server_name
        super().__init__(
            connection_params=StreamableHTTPConnectionParams(
                url=url,
                timeout=30.0,
            ),
            tool_filter=tool_filter,
            tool_name_prefix=tool_name_prefix,
            errlog=None,
            header_provider=_gcp_agent_identity_headers,
        )

    async def get_tools(self, readonly_context=None):
        try:
            return await super().get_tools(readonly_context)
        except Exception as exc:
            logger.warning("MCP server %s unavailable: %s", self._server_name, exc)
            return []


# ---------------------------------------------------------------------------
# 1. Google Cloud SQL Admin MCP (Read-Only Inspection Active; Mutating Commented)
# ---------------------------------------------------------------------------
cloudsql_mcp_toolset = BestEffortMcpToolset(
    server_name="google-cloud-sql",
    url=SQLADMIN_MCP_URL,
    tool_name_prefix="cloudsql_",
    tool_filter=[
        # --- ACTIVE: Cloud SQL Instance, User, Operation & Read-Only Query Inspection ---
        "list_instances",
        "get_instance",
        "list_users",
        "get_operation",
        "execute_sql_readonly",
        "postgres_upgrade_precheck",
        # --- FUTURE / DISABLED CLOUD SQL TOOLS (Uncomment to enable) ---
        # "create_instance",
        # "update_instance",
        # "clone_instance",
        # "execute_sql",
        # "create_user",
        # "update_user",
        # "create_backup",
        # "restore_backup",
        # "import_data",
    ],
)

# ---------------------------------------------------------------------------
# 2. Google Cloud Observability MCPs (Cloud Logging + Cloud Monitoring)
# ---------------------------------------------------------------------------
logging_mcp_toolset = BestEffortMcpToolset(
    server_name="google-cloud-logging",
    url=LOGGING_MCP_URL,
    tool_name_prefix="logging_",
    tool_filter=[
        # --- ACTIVE: Read-Only Log Inspection ---
        "list_log_entries",
        "list_log_names",
        # --- FUTURE / DISABLED LOGGING TOOLS (Uncomment to enable) ---
        # "get_bucket",
        # "list_buckets",
        # "get_view",
        # "list_views",
    ],
)

monitoring_mcp_toolset = BestEffortMcpToolset(
    server_name="google-cloud-monitoring",
    url=MONITORING_MCP_URL,
    tool_name_prefix="monitoring_",
    tool_filter=[
        # --- ACTIVE: Read-Only Metrics & Alerts Inspection ---
        "list_timeseries",
        "query_range",
        "list_alert_policies",
        "list_alerts",
        # --- FUTURE / DISABLED MONITORING TOOLS (Uncomment to enable) ---
        # "get_alert_policy",
        # "get_alert",
        # "list_metric_descriptors",
        # "list_dashboards",
        # "get_dashboard",
    ],
)

# ---------------------------------------------------------------------------
# 3. Google Cloud Knowledge / Documentation MCP (Best Practices & Docs)
# ---------------------------------------------------------------------------
knowledge_mcp_toolset = BestEffortMcpToolset(
    server_name="google-developer-knowledge",
    url=KNOWLEDGE_MCP_URL,
    tool_name_prefix="knowledge_",
    tool_filter=[
        # --- ACTIVE: GCP Documentation & Best Practices Lookup ---
        "search_documents",
        "get_documents",
        # --- FUTURE / DISABLED KNOWLEDGE TOOLS (Uncomment to enable) ---
        # "answer_query",
    ],
)


root_agent = Agent(
    model=MODEL,
    name="gcp_cloudsql_ops_agent",
    description=(
        "Google Cloud SQL Operations Agent providing Cloud SQL instance inspection, "
        "read-only SQL diagnostics, Cloud Logging and Monitoring analysis, and official GCP documentation best practices."
    ),
    instruction=(
        "You are the Google Cloud SQL Operations Specialist (`gcp_cloudsql_ops_agent`).\n"
        "Follow an Observe -> Consult Best Practices -> Recommend workflow:\n"
        "1. OBSERVE: Use the Cloud SQL (`cloudsql_*`), Cloud Logging (`logging_*`), and Cloud Monitoring "
        "(`monitoring_*`) tools to inspect Cloud SQL instances, users, operations, read-only SQL diagnostics, "
        "database CPU/memory/connection metrics, active alerts, and database logs.\n"
        "2. CONSULT: Use the GCP Knowledge tools (`knowledge_*`) to look up official Google Cloud SQL documentation, "
        "high-availability/backup guides, and performance tuning best practices.\n"
        "Never claim an action was executed unless a tool result proves it. If an MCP server is blocked by an "
        "Agent Egress Gateway policy, report which server was denied and continue with the available tools."
    ),
    tools=[
        cloudsql_mcp_toolset,
        logging_mcp_toolset,
        monitoring_mcp_toolset,
        knowledge_mcp_toolset,
    ],
)
