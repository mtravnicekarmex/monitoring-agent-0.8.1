from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Callable

from .delivery import (
    DELIVERY_ADAPTER_VERSION,
    DELIVERY_MODE_DISABLED,
    DELIVERY_MODE_TEST,
    DELIVERY_STATUS_CONFIGURATION_ERROR,
    DELIVERY_STATUS_DISABLED,
    DELIVERY_STATUS_FAILED,
    DELIVERY_STATUS_NO_DUE_ITEMS,
    DELIVERY_STATUS_SENT,
    DeliveryAttemptResult,
    DeliveryTransport,
    OutlookEmailTransport,
    TestDeliveryPolicy,
    deliver_due_test_delivery_intents,
    hash_delivery_recipient,
    validate_outlook_email_environment,
    validate_test_delivery_policy,
)
from .incident_store import (
    OUTBOX_DEAD_LETTER,
    OUTBOX_IN_PROGRESS,
    OUTBOX_PENDING,
    OUTBOX_SENT,
    IncidentStateStore,
    IncidentStoreSnapshot,
    OutboxItem,
)
from .settings import RuntimeSettings


RUNTIME_DELIVERY_CONTRACT_VERSION = 1
RUNTIME_DELIVERY_MAX_PER_CYCLE = 1
DELIVERY_AUTOMATION_ENABLED_ENV = "DELIVERY_AUTOMATION_ENABLED"
DELIVERY_TEST_RECIPIENT_ENV = "DELIVERY_TEST_RECIPIENT"
DELIVERY_TEST_SENDER_ALIAS_ENV = "DELIVERY_TEST_SENDER_ALIAS"
RUNTIME_DELIVERY_ENV_KEYS = {
    DELIVERY_AUTOMATION_ENABLED_ENV,
    DELIVERY_TEST_RECIPIENT_ENV,
    DELIVERY_TEST_SENDER_ALIAS_ENV,
    "O_EMAIL",
    "O_APP",
    "EMAIL",
    "APP",
}
RUNTIME_DELIVERY_CLAIM_PREFIX = "runtime-auto-test"


@dataclass(frozen=True)
class RuntimeDeliverySummary:
    enabled: bool
    mode: str
    status: str
    results: tuple[DeliveryAttemptResult, ...] = ()
    error_code: str | None = None
    state_changed: bool = False
    contract_version: int = RUNTIME_DELIVERY_CONTRACT_VERSION
    delivery_adapter_version: int = DELIVERY_ADAPTER_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("runtime delivery enabled flag must be boolean")
        if self.mode not in {DELIVERY_MODE_DISABLED, DELIVERY_MODE_TEST}:
            raise ValueError("runtime delivery mode is invalid")
        if not self.status.strip():
            raise ValueError("runtime delivery status is required")
        if self.contract_version != RUNTIME_DELIVERY_CONTRACT_VERSION:
            raise ValueError("runtime delivery contract is unsupported")
        if self.delivery_adapter_version != DELIVERY_ADAPTER_VERSION:
            raise ValueError("delivery adapter version is unsupported")
        object.__setattr__(self, "results", tuple(self.results))

    def to_dict(self) -> dict[str, object]:
        result_payloads = [item.to_dict() for item in self.results]
        status_counts = Counter(item.status for item in self.results)
        return {
            "attempted_count": sum(
                1
                for item in self.results
                if item.status in {DELIVERY_STATUS_FAILED, DELIVERY_STATUS_SENT}
            ),
            "configuration_error_count": status_counts[
                DELIVERY_STATUS_CONFIGURATION_ERROR
            ],
            "contract_version": self.contract_version,
            "delivery_adapter_version": self.delivery_adapter_version,
            "enabled": self.enabled,
            "error_code": self.error_code,
            "failed_count": status_counts[DELIVERY_STATUS_FAILED],
            "mode": self.mode,
            "no_due_count": status_counts[DELIVERY_STATUS_NO_DUE_ITEMS],
            "result_count": len(self.results),
            "results": result_payloads,
            "sent_count": status_counts[DELIVERY_STATUS_SENT],
            "status": self.status,
        }


TransportFactory = Callable[..., DeliveryTransport]


def run_runtime_delivery(
    *,
    settings: RuntimeSettings,
    env_file: Path,
    store: IncidentStateStore,
    now: datetime | None = None,
    transport_factory: TransportFactory = OutlookEmailTransport,
) -> RuntimeDeliverySummary:
    delivered_at = datetime.now(timezone.utc) if now is None else now
    _require_aware_datetime(delivered_at, context="delivered_at")
    if not settings.delivery_automation_enabled:
        return RuntimeDeliverySummary(
            enabled=False,
            mode=DELIVERY_MODE_DISABLED,
            status=DELIVERY_STATUS_DISABLED,
        )

    delivery_env = _read_runtime_delivery_env(env_file)
    policy = _policy_from_env(delivery_env)
    policy_error = validate_test_delivery_policy(policy)
    if policy_error is not None:
        return RuntimeDeliverySummary(
            enabled=True,
            mode=DELIVERY_MODE_TEST,
            status=DELIVERY_STATUS_CONFIGURATION_ERROR,
            error_code=policy_error,
        )
    email_env_error = validate_outlook_email_environment(delivery_env)
    if email_env_error is not None:
        return RuntimeDeliverySummary(
            enabled=True,
            mode=DELIVERY_MODE_TEST,
            status=DELIVERY_STATUS_CONFIGURATION_ERROR,
            error_code=email_env_error,
        )

    snapshot = store.load()
    due_items = _due_pending_items(snapshot, now=delivered_at)
    if not due_items:
        return RuntimeDeliverySummary(
            enabled=True,
            mode=DELIVERY_MODE_TEST,
            status=DELIVERY_STATUS_NO_DUE_ITEMS,
            results=(DeliveryAttemptResult(status=DELIVERY_STATUS_NO_DUE_ITEMS),),
        )
    reports_by_reference = {
        item.report_reference: _render_runtime_delivery_report(
            item,
            snapshot=snapshot,
            generated_at=delivered_at,
        )
        for item in due_items
    }
    transport = transport_factory(
        sender_alias=_optional_env(delivery_env, DELIVERY_TEST_SENDER_ALIAS_ENV),
        env=delivery_env,
    )
    claim_id = f"{RUNTIME_DELIVERY_CLAIM_PREFIX}:{delivered_at.isoformat()}"
    results = deliver_due_test_delivery_intents(
        store=store,
        reports_by_reference=reports_by_reference,
        policy=policy,
        transport=transport,
        claim_id=claim_id,
        now=delivered_at,
        limit=RUNTIME_DELIVERY_MAX_PER_CYCLE,
    )
    return RuntimeDeliverySummary(
        enabled=True,
        mode=DELIVERY_MODE_TEST,
        status=_aggregate_result_status(results),
        results=results,
        state_changed=any(
            item.status in {DELIVERY_STATUS_FAILED, DELIVERY_STATUS_SENT}
            for item in results
        ),
    )


def _read_runtime_delivery_env(path: Path) -> dict[str, str]:
    values = {
        key: value
        for key in RUNTIME_DELIVERY_ENV_KEYS
        if (value := os.environ.get(key)) is not None
    }
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in RUNTIME_DELIVERY_ENV_KEYS or key in values:
            continue
        values[key] = _normalize_env_file_value(value)
    return values


def _normalize_env_file_value(value: str) -> str:
    normalized = value.strip()
    if (
        len(normalized) >= 2
        and normalized[0] == normalized[-1]
        and normalized[0] in {"'", '"'}
    ):
        return normalized[1:-1]
    return normalized


def _policy_from_env(env: Mapping[str, str]) -> TestDeliveryPolicy:
    recipient = _optional_env(env, DELIVERY_TEST_RECIPIENT_ENV)
    recipient_hashes = (
        (hash_delivery_recipient(recipient),) if recipient is not None else ()
    )
    return TestDeliveryPolicy(
        enabled=True,
        mode=DELIVERY_MODE_TEST,
        test_recipient=recipient,
        allowed_recipient_hashes=recipient_hashes,
    )


def _optional_env(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None or not str(value).strip():
        return None
    return str(value).strip()


def _due_pending_items(
    snapshot: IncidentStoreSnapshot,
    *,
    now: datetime,
) -> tuple[OutboxItem, ...]:
    return tuple(
        sorted(
            (
                item
                for item in snapshot.outbox_items
                if item.status == OUTBOX_PENDING and item.next_attempt_at <= now
            ),
            key=lambda item: (
                item.next_attempt_at,
                item.created_at,
                item.incident_key,
                item.idempotency_key,
            ),
        )[:RUNTIME_DELIVERY_MAX_PER_CYCLE]
    )


def _render_runtime_delivery_report(
    item: OutboxItem,
    *,
    snapshot: IncidentStoreSnapshot,
    generated_at: datetime,
) -> str:
    state = snapshot.state_by_key().get(item.incident_key)
    diagnosis = _incident_operator_diagnosis(item, state)
    state_lines = [
        "Current incident state: not present in current state snapshot",
    ]
    if state is not None:
        state_lines = [
            f"Current incident state: {state.status}",
            f"Severity: {state.severity}",
            f"Subject: {state.subject}",
            f"Kind: {state.kind}",
            f"Last reason: {state.last_reason or '-'}",
            f"Opened at: {_format_runtime_datetime(state.opened_at)}",
            f"Recovered at: {_format_runtime_datetime(state.recovered_at)}",
            f"Last observed at: {_format_runtime_datetime(state.last_observed_at)}",
            f"Last cycle sequence: {state.last_cycle_sequence or '-'}",
            f"Failure count: {state.failure_count}",
            f"Recovery count: {state.recovery_count}",
            f"Occurrence count: {state.occurrence_count}",
        ]
    outbox_counts = _outbox_counts(snapshot)
    return "\n".join(
        [
            "Monitoring agent event report",
            "=============================",
            "",
            f"Generated at: {generated_at.astimezone(timezone.utc).isoformat()}",
            f"Incident key: {item.incident_key}",
            f"Action: {item.action}",
            f"Report reference: {item.report_reference}",
            "",
            "Co se stalo",
            "------------",
            diagnosis["what_happened"],
            "",
            "Co je potreba opravit",
            "----------------------",
            diagnosis["what_to_fix"],
            "",
            "Proc agent poslal alert",
            "-----------------------",
            diagnosis["why_alerted"],
            "",
            "Current incident facts",
            "----------------------",
            *state_lines,
            "",
            "Outbox counts before delivery",
            "-----------------------------",
            (
                "pending={pending}, in_progress={in_progress}, sent={sent}, "
                "dead_letter={dead_letter}"
            ).format(**outbox_counts),
            "",
        ]
    )


def _incident_operator_diagnosis(
    item: OutboxItem,
    state: object | None,
) -> dict[str, str]:
    subject = getattr(state, "subject", None) or _subject_from_incident_key(
        item.incident_key
    )
    kind = getattr(state, "kind", None) or _kind_from_incident_key(item.incident_key)
    reason = getattr(state, "last_reason", None) or "not_available"
    action = item.action

    return {
        "what_happened": _incident_what_happened(
            action=action,
            kind=str(kind),
            subject=str(subject),
            reason=str(reason),
        ),
        "what_to_fix": _incident_what_to_fix(
            kind=str(kind),
            subject=str(subject),
            reason=str(reason),
        ),
        "why_alerted": _incident_why_alerted(action=action, reason=str(reason)),
    }


def _incident_what_happened(
    *,
    action: str,
    kind: str,
    subject: str,
    reason: str,
) -> str:
    action_text = {
        "opened": "Agent otevrel novy potvrzeny incident.",
        "reopened": "Agent znovu otevrel drive vyreseny incident.",
        "recovered": "Agent potvrdil obnovu sluzby.",
    }.get(action, f"Agent zaznamenal akci incidentu: {action}.")
    if action == "recovered":
        return f"{action_text} Dotcena oblast: {subject}."
    if kind == "endpoint":
        return (
            f"{action_text} Kontrola '{subject}' opakovane nevysla v poradku. "
            f"Posledni duvod: {_human_reason(reason)}."
        )
    if kind == "target_wide":
        return (
            f"{action_text} Vice sledovanych facade endpointu ma transportni "
            f"selhani. Posledni duvod: {_human_reason(reason)}."
        )
    if kind == "observer":
        return (
            f"{action_text} Problem je na strane dohledoveho agenta nebo jeho "
            f"pristupu k monitoring facade. Posledni duvod: {_human_reason(reason)}."
        )
    if kind == "blind_spot":
        return (
            f"{action_text} Agent nema cerstvy dokonceny kontrolni cyklus. "
            f"Posledni duvod: {_human_reason(reason)}."
        )
    return f"{action_text} Dotcena oblast: {subject}. Posledni duvod: {_human_reason(reason)}."


def _incident_what_to_fix(*, kind: str, subject: str, reason: str) -> str:
    if kind == "endpoint":
        if subject == "system_scheduler":
            return (
                "Zkontroluj Scheduler Health v hlavnim dashboardu a oprav konkretni "
                "job nebo scheduler krok, ktery drzi endpoint ve stavu degraded/error."
            )
        if subject == "system_database":
            return (
                "Zkontroluj dostupnost PostgreSQL/MSSQL a stav databazove health "
                "facade na hlavni stanici."
            )
        if subject == "system_runtime":
            return (
                "Zkontroluj runtime hlavni aplikace, proces scheduleru/API a jejich "
                "health endpointy."
            )
        if subject == "system_proxy":
            return "Zkontroluj proxy/Caddy vrstvu a pruchod requestu na backend."
        if subject == "external_web":
            return "Zkontroluj verejnou dostupnost webu z dohledove stanice."
        if subject in {"live", "ready"}:
            return "Zkontroluj FastAPI health endpoint a dostupnost aplikace."
        return f"Zkontroluj sledovany endpoint '{subject}' a jeho health detail."
    if kind == "target_wide":
        return (
            "Zkontroluj sitovou dostupnost hlavni stanice, proxy/Caddy, FastAPI a "
            "monitoring facade; selhani vypada sirsi nez jeden endpoint."
        )
    if kind == "observer":
        return (
            "Zkontroluj konfiguraci dohledoveho agenta, bearer pristup k facade, "
            "cas systemu a dostupnost hlavni stanice z dohledove stanice."
        )
    if kind == "blind_spot":
        return (
            "Zkontroluj, ze Scheduled Task MonitoringAgentTest bezi a agent zapisuje "
            "dokoncene cykly do sveho state store."
        )
    if reason.startswith("endpoint_payload_status:"):
        return "Oprav stav sluzby, ktera vraci varovny nebo chybovy payload status."
    return "Zkontroluj detail incidentu ve state store a navazujici dashboard health detail."


def _incident_why_alerted(*, action: str, reason: str) -> str:
    if action == "recovered":
        return "Incident splnil recovery prah a agent posila informaci o obnoveni."
    if reason == "recovery_confirmation_pending":
        return "Agent ceka na dostatek zdravych cyklu pro potvrzeni obnovy."
    return (
        "Incident splnil potvrzovaci pravidla stinoveho incident enginu. "
        f"Normalizovany duvod: {reason}."
    )


def _human_reason(reason: str) -> str:
    if reason.startswith("endpoint_payload_status:"):
        status = reason.split(":", 1)[1] or "unknown"
        return f"endpoint vratil payload status '{status}'"
    if reason == "endpoint_retryable_transport_failure":
        return "HTTP kontrola selhala transportne i po retry"
    if reason == "all_facade_endpoints_retryable_transport_failure":
        return "vsechny facade endpointy mely retryable transportni selhani"
    if reason.startswith("facade_contract_or_authentication_failure:"):
        return "facade vratila neplatny kontrakt nebo autentizace selhala"
    if reason == "latest_cycle_stale":
        return "posledni dokonceny cyklus je prilis stary"
    if reason == "no_complete_cycle_observed":
        return "agent zatim nema dokonceny cyklus"
    if reason == "recovery_confirmed":
        return "obnova byla potvrzena"
    if reason == "not_available":
        return "detailni duvod neni v aktualnim snapshotu dostupny"
    return reason


def _subject_from_incident_key(incident_key: str) -> str:
    if ":" not in incident_key:
        return incident_key
    return incident_key.split(":", 1)[1]


def _kind_from_incident_key(incident_key: str) -> str:
    if ":" not in incident_key:
        return "unknown"
    return incident_key.split(":", 1)[0]


def _outbox_counts(snapshot: IncidentStoreSnapshot) -> dict[str, int]:
    counts = Counter(item.status for item in snapshot.outbox_items)
    return {
        "pending": counts[OUTBOX_PENDING],
        "in_progress": counts[OUTBOX_IN_PROGRESS],
        "sent": counts[OUTBOX_SENT],
        "dead_letter": counts[OUTBOX_DEAD_LETTER],
    }


def _format_runtime_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(timezone.utc).isoformat()


def _aggregate_result_status(results: tuple[DeliveryAttemptResult, ...]) -> str:
    statuses = {item.status for item in results}
    if len(statuses) == 1:
        return next(iter(statuses))
    if DELIVERY_STATUS_SENT in statuses and DELIVERY_STATUS_FAILED not in statuses:
        return DELIVERY_STATUS_SENT
    return "mixed"


def _require_aware_datetime(value: datetime, *, context: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{context} must include a timezone")
