"""Independent read-only monitoring observer."""

from .audit import StateAuditError, build_state_audit
from .client import APPROVED_ENDPOINTS, HealthClient
from .delivery import (
    DeliveryAttemptResult,
    DeliveryEnvelope,
    OutlookEmailTransport,
    TestDeliveryPolicy,
    build_test_delivery_envelope,
    deliver_due_test_delivery_intents,
    hash_delivery_recipient,
    normalize_delivery_recipient,
    send_email_outlook,
    validate_outlook_email_environment,
    validate_test_delivery_policy,
)
from .incidents import (
    DEFAULT_INCIDENT_RULES,
    CycleSnapshot,
    EndpointObservationFact,
    IncidentEvaluation,
    IncidentRules,
    evaluate_incident_lifecycle,
)
from .incident_store import (
    IncidentStateStore,
    IncidentStoreError,
    IncidentStoreLimits,
)
from .interpretation import (
    InterpretationPolicy,
    InterpretationProviderOutput,
    InterpretationRequest,
    InterpretationResult,
    build_interpretation_prompt,
    interpret_confirmed_incidents,
)
from .observer import run_observation_cycle
from .reporting import (
    MonitoringReportSnapshot,
    ReportFact,
    build_monitoring_report_snapshot,
    redact_monitoring_text,
    render_monitoring_report,
    render_programming_agent_prompt,
)
from .runtime_shadow import (
    ShadowRuntimeSummary,
    apply_shadow_incident_cycle,
    build_incident_store,
    build_incident_store_limits,
    summarize_shadow_incident_snapshot,
)
from .settings import RuntimeSettings
from .shadow_pilot import (
    ShadowPilotBlindSpot,
    ShadowPilotComparison,
    ShadowPilotEvent,
    build_shadow_pilot_comparison,
    events_from_incident_evaluation,
    render_shadow_pilot_comparison,
)
from .store import ObserverStore, StateRetentionError

__all__ = [
    "APPROVED_ENDPOINTS",
    "DEFAULT_INCIDENT_RULES",
    "CycleSnapshot",
    "DeliveryAttemptResult",
    "DeliveryEnvelope",
    "EndpointObservationFact",
    "HealthClient",
    "IncidentEvaluation",
    "IncidentStateStore",
    "IncidentStoreError",
    "IncidentStoreLimits",
    "InterpretationPolicy",
    "InterpretationProviderOutput",
    "InterpretationRequest",
    "InterpretationResult",
    "IncidentRules",
    "MonitoringReportSnapshot",
    "ObserverStore",
    "OutlookEmailTransport",
    "ReportFact",
    "RuntimeSettings",
    "StateRetentionError",
    "StateAuditError",
    "ShadowRuntimeSummary",
    "ShadowPilotBlindSpot",
    "ShadowPilotComparison",
    "ShadowPilotEvent",
    "apply_shadow_incident_cycle",
    "build_incident_store",
    "build_incident_store_limits",
    "TestDeliveryPolicy",
    "build_test_delivery_envelope",
    "deliver_due_test_delivery_intents",
    "build_state_audit",
    "build_monitoring_report_snapshot",
    "build_shadow_pilot_comparison",
    "build_interpretation_prompt",
    "events_from_incident_evaluation",
    "evaluate_incident_lifecycle",
    "hash_delivery_recipient",
    "normalize_delivery_recipient",
    "redact_monitoring_text",
    "render_monitoring_report",
    "render_programming_agent_prompt",
    "render_shadow_pilot_comparison",
    "interpret_confirmed_incidents",
    "send_email_outlook",
    "summarize_shadow_incident_snapshot",
    "validate_outlook_email_environment",
    "validate_test_delivery_policy",
    "run_observation_cycle",
]
