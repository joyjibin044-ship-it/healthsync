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

import datetime
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server named "healthsync"
mcp = FastMCP("healthsync")

# Mock database
vitals_db = [
    {"timestamp": "2026-07-03 09:00:00", "type": "blood pressure", "value": 120.0, "unit": "mmHg"},
    {"timestamp": "2026-07-03 09:05:00", "type": "heart rate", "value": 72.0, "unit": "bpm"},
    {"timestamp": "2026-07-03 14:00:00", "type": "blood sugar", "value": 95.0, "unit": "mg/dL"},
]

appointments_db = [
    {"doctor_name": "Dr. Davis", "date_time": "2026-07-08 11:30 AM", "reason": "Annual wellness physical exam"},
]

DRUG_INTERACTIONS = {
    ("aspirin", "ibuprofen"): "Increased risk of gastrointestinal bleeding when taken together.",
    ("warfarin", "aspirin"): "Severe interaction! Highly increased risk of bleeding. Use only under close medical supervision.",
    ("lisinopril", "spironolactone"): "Risk of high potassium levels (hyperkalemia) in the blood. Close monitoring of electrolytes is advised.",
    ("sildenafil", "nitroglycerin"): "CRITICAL! Co-administration can cause a severe, life-threatening drop in blood pressure.",
}


@mcp.tool()
def log_vital_sign(vital_type: str, value: float, unit: str) -> str:
    """Logs a new vital sign measurement (e.g., blood pressure, blood sugar, heart rate, temperature).

    Args:
        vital_type: The name of the vital sign (e.g., 'blood pressure', 'blood sugar').
        value: The numeric measurement value.
        unit: The unit of measurement (e.g., 'mmHg', 'mg/dL', 'bpm', 'F').

    Returns:
        A success confirmation message with the logged data.
    """
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = {
        "timestamp": timestamp,
        "type": vital_type.lower().strip(),
        "value": value,
        "unit": unit.strip(),
    }
    vitals_db.append(entry)
    return f"Successfully logged vital sign: {vital_type} = {value} {unit} at {timestamp}."


@mcp.tool()
def get_vitals_history(vital_type: str, limit: int = 5) -> str:
    """Retrieves the history of logged vital sign measurements for a specific vital type.

    Args:
        vital_type: The type of vital sign to retrieve history for (e.g., 'blood pressure').
        limit: The maximum number of entries to return (default is 5).

    Returns:
        A list of formatted vital measurements or a message indicating no history is found.
    """
    target = vital_type.lower().strip()
    matches = [v for v in vitals_db if v["type"] == target]
    matches = sorted(matches, key=lambda x: x["timestamp"], reverse=True)[:limit]

    if not matches:
        return f"No logged history found for vital sign: '{vital_type}'."

    history_lines = []
    for m in matches:
        history_lines.append(f"- [{m['timestamp']}] {m['value']} {m['unit']}")
    return f"History for '{vital_type}':\n" + "\n".join(history_lines)


@mcp.tool()
def schedule_appointment(doctor_name: str, date_time: str, reason: str) -> str:
    """Schedules a new doctor appointment.

    Args:
        doctor_name: The name of the physician/doctor.
        date_time: The requested date and time (e.g. '2026-07-05 10:00 AM').
        reason: The reason for the visit.

    Returns:
        A confirmation message detailing the scheduled appointment.
    """
    entry = {
        "doctor_name": doctor_name.strip(),
        "date_time": date_time.strip(),
        "reason": reason.strip(),
    }
    appointments_db.append(entry)
    return f"Successfully scheduled appointment with {doctor_name} on {date_time} for: '{reason}'."


@mcp.tool()
def get_appointments() -> str:
    """Retrieves the list of scheduled doctor appointments.

    Returns:
        A formatted list of scheduled doctor appointments.
    """
    if not appointments_db:
        return "No appointments scheduled."

    sorted_appts = sorted(appointments_db, key=lambda x: x["date_time"])
    lines = []
    for idx, a in enumerate(sorted_appts, start=1):
        lines.append(f"{idx}. {a['doctor_name']} on {a['date_time']} (Reason: {a['reason']})")
    return "Scheduled Appointments:\n" + "\n".join(lines)


@mcp.tool()
def check_drug_interactions(drugs_list: list[str]) -> str:
    """Checks for potential drug-drug interactions or contraindications among a list of medications.

    Args:
        drugs_list: A list of names of drugs/medications.

    Returns:
        A string describing any identified interactions and safety advice.
    """
    cleaned_drugs = [d.lower().strip() for d in drugs_list if d.strip()]
    found_warnings = []

    # Check pairs
    for i in range(len(cleaned_drugs)):
        for j in range(i + 1, len(cleaned_drugs)):
            d1, d2 = cleaned_drugs[i], cleaned_drugs[j]
            # Try both permutations
            key = (d1, d2) if (d1, d2) in DRUG_INTERACTIONS else ((d2, d1) if (d2, d1) in DRUG_INTERACTIONS else None)
            if key:
                found_warnings.append(f"- Warning ({d1} + {d2}): {DRUG_INTERACTIONS[key]}")

    if not found_warnings:
        return f"No known interactions found for {', '.join(drugs_list)} in this local database. Note: Always consult with a healthcare professional before combining medications."

    return "Potential Drug Interactions Found:\n" + "\n".join(found_warnings) + "\n\nCRITICAL NOTE: These interaction checks are mock databases. Please consult a licensed medical professional or clinical pharmacist."


if __name__ == "__main__":
    mcp.run()
