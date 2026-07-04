# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import re
import json
import logging
from typing import Any

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types
from google.adk import Workflow, Context
from google.adk.workflow import node, Edge, START
from google.adk.events import RequestInput, Event
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

from app.config import config

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("healthsync")

# Initialize MCP Toolset (stdio transport to call our FastMCP server)
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command="uv",
            args=["run", "python", "-m", "app.mcp_server"],
        )
    )
)

# ------------------------------------------------------------------------------
# Specialized Sub-Agents
# ------------------------------------------------------------------------------

vitals_agent = Agent(
    name="vitals_agent",
    model=Gemini(model=config.model),
    instruction="""You are the Vitals Tracker Agent. Your role is to help users manage their health vitals
    (blood pressure, heart rate, blood sugar, etc.).
    You have access to log_vital_sign and get_vitals_history tools through the MCP server.
    Be precise, clinical, supportive, and keep responses concise.
    Always format vital history as a neat markdown list.
    """,
    tools=[mcp_toolset],
)

symptom_agent = Agent(
    name="symptom_agent",
    model=Gemini(model=config.model),
    instruction="""You are the Symptom Checker Agent. Your role is to analyze user-reported symptoms
    and check for drug-drug interactions using check_drug_interactions.
    Do NOT provide medical diagnoses, but provide general guidance, interaction warnings, and prompt the user to consult doctors.
    Be extremely empathetic, cautious, and brief.
    """,
    tools=[mcp_toolset],
)

scheduler_agent = Agent(
    name="scheduler_agent",
    model=Gemini(model=config.model),
    instruction="""You are the Appointment Scheduler Agent. Your role is to help schedule doctor appointments
    and check existing appointments using get_appointments and schedule_appointment.
    
    CRITICAL RULE:
    Before calling schedule_appointment, check if 'appointment_status' in the session state is 'confirmed'.
    If it is NOT confirmed, do NOT call schedule_appointment. Instead, write the details of the request
    (doctor name, date/time, and reason) into the session state under 'pending_appointment', and tell the orchestrator
    what you are planning to schedule so that confirmation can be requested.
    If 'appointment_status' is 'confirmed', you must proceed to run schedule_appointment to log it, then inform the user it is fully booked.
    """,
    tools=[mcp_toolset],
)

# Wrap sub-agents as tools for orchestrator delegation
vitals_tool = AgentTool(vitals_agent)
symptom_tool = AgentTool(symptom_agent)
scheduler_tool = AgentTool(scheduler_agent)

# ------------------------------------------------------------------------------
# Orchestrator Agent
# ------------------------------------------------------------------------------

def check_orchestrator_route(ctx: Context) -> None:
    """Orchestrator callback that sets the downstream route based on context state."""
    if "pending_appointment" in ctx.state:
        status = ctx.state.get("appointment_status")
        if status in ("confirmed", "cancelled"):
            ctx.route = "final"
        else:
            ctx.route = "needs_approval"
    else:
        ctx.route = "final"

orchestrator = Agent(
    name="orchestrator",
    model=Gemini(model=config.model),
    instruction="""You are the HealthSync Orchestrator. You help users manage their health vitals, appointments, and symptom checks.
    You coordinate with three sub-agents: vitals_agent, symptom_agent, and scheduler_agent.
    
    Delegation Rules:
    1. If the query is about logging vitals or checking vitals history, delegate to vitals_agent.
    2. If the query is about medical symptoms, medications, or checking drug interactions, delegate to symptom_agent.
    3. If the query is about scheduling a doctor's appointment or viewing appointments, delegate to scheduler_agent.
    4. If the scheduler_agent tells you it requires approval or sets a pending appointment, tell the user you need to seek confirmation.
    
    Post-Approval Instruction:
    If the user has just confirmed or cancelled an appointment (i.e., 'appointment_status' in state is 'confirmed' or 'cancelled'):
    - If confirmed: Delegate to scheduler_agent to finally schedule the appointment, then confirm to the user.
    - If cancelled: Inform the user the appointment request has been discarded.
    - Once handled, remove 'pending_appointment' from the state.
    
    Be helpful, brief, and ensure privacy.
    """,
    tools=[vitals_tool, symptom_tool, scheduler_tool],
    after_agent_callback=check_orchestrator_route,
)

# ------------------------------------------------------------------------------
# Workflow Graph Nodes
# ------------------------------------------------------------------------------

@node
async def security_checkpoint(ctx: Context, query: str) -> Any:
    """Performs PII redaction, prompt injection checks, and logs structured audits."""
    audit_log = {
        "event": "security_checkpoint",
        "raw_query": query,
        "pii_redacted": False,
        "injection_detected": False,
        "domain_checks_passed": True,
        "severity": "INFO",
    }
    
    # 1. PII Redaction
    redacted_query = query
    ssn_pattern = r"\b\d{3}-\d{2}-\d{4}\b"
    mrn_pattern = r"\bMRN\d{6,8}\b"
    phone_pattern = r"\b\d{3}-\d{3}-\d{4}\b"
    
    if re.search(ssn_pattern, query):
        redacted_query = re.sub(ssn_pattern, "[SSN_REDACTED]", redacted_query)
        audit_log["pii_redacted"] = True
        audit_log["severity"] = "WARNING"
    if re.search(mrn_pattern, query):
        redacted_query = re.sub(mrn_pattern, "[MRN_REDACTED]", redacted_query)
        audit_log["pii_redacted"] = True
    if re.search(phone_pattern, query):
        redacted_query = re.sub(phone_pattern, "[PHONE_REDACTED]", redacted_query)
        audit_log["pii_redacted"] = True

    # 2. Prompt Injection Check
    injection_keywords = ["ignore previous instruction", "ignore instructions", "system prompt", "override instruction", "bypass safety"]
    for keyword in injection_keywords:
        if keyword in query.lower():
            audit_log["injection_detected"] = True
            audit_log["severity"] = "CRITICAL"
            logger.error(f"Security Audit Log: {json.dumps(audit_log)}")
            ctx.route = "security_block"
            return "Security Alert: Prompt injection attempt detected. Operation blocked."

    # 3. Domain-Specific Consent Check
    # If exporting or sharing health data, ensure user consent is logged
    if any(k in query.lower() for k in ["share my health data", "export my vitals", "send vitals"]):
        if not ctx.state.get("health_data_consent", False):
            audit_log["domain_checks_passed"] = False
            audit_log["severity"] = "WARNING"
            logger.warning(f"Security Audit Log: {json.dumps(audit_log)}")
            ctx.route = "security_block"
            return "Consent Required: You must explicitly state 'I consent to sharing my health data' before exporting/sharing health info."

    logger.info(f"Security Audit Log: {json.dumps(audit_log)}")
    
    # Forward the sanitized query
    return redacted_query


@node
async def appointment_approval(ctx: Context, query: str) -> Any:
    """HITL node requesting user confirmation before scheduling an appointment."""
    interrupt_id = "appointment_confirm"
    
    if interrupt_id in ctx.resume_inputs:
        user_response = ctx.resume_inputs[interrupt_id]
        if "yes" in user_response.lower() or "confirm" in user_response.lower():
            ctx.state["appointment_status"] = "confirmed"
        else:
            ctx.state["appointment_status"] = "cancelled"
        
        # Go back to orchestrator to finalize
        return user_response
    else:
        pending = ctx.state.get("pending_appointment", "Requested doctor appointment")
        return RequestInput(
            interrupt_id=interrupt_id,
            message=f"Please confirm: Do you want to schedule this appointment? ({pending}). Reply 'yes' or 'no'.",
        )


@node
async def final_output(ctx: Context, query: str) -> Any:
    """Terminal node in the workflow that returns the final result."""
    return query

# ------------------------------------------------------------------------------
# Workflow Definitions
# ------------------------------------------------------------------------------

# Define graph edges (strictly adhering to the single-edge source-target rule)
workflow_edges = [
    Edge(from_node=START, to_node=security_checkpoint),
    Edge(from_node=security_checkpoint, to_node=orchestrator),
    Edge(from_node=security_checkpoint, to_node=final_output, route="security_block"),
    Edge(from_node=orchestrator, to_node=appointment_approval, route="needs_approval"),
    Edge(from_node=orchestrator, to_node=final_output, route="final"),
    Edge(from_node=appointment_approval, to_node=orchestrator),
]

healthsync_workflow = Workflow(
    name="healthsync_workflow",
    description="Multi-agent workflow graph for managing personal health and scheduling.",
    edges=workflow_edges,
)

# Export app as required by fast_api_app.py
app = App(
    root_agent=healthsync_workflow,
    name="healthsync",
)
