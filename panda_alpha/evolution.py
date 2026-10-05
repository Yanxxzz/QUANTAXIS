"""Hypothesis-led factor research, explicit reflection and bounded trajectories.

LLM use is opt-in through a caller supplied by the application. No credentials,
network client or paid backtest are opened by this module. Generated definitions
are research candidates, never a statement of verified investment performance.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from .diversity import canonical_signature, expression_features, select_diverse


@dataclass
class Candidate:
    hypothesis: str
    mechanism: str
    data_requirements: tuple[str, ...] = ()
    source: str = "quantaxis_daily"
    code: str = ""
    formula: str = ""
    direction: int = 1
    parents: tuple[str, ...] = ()
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    fields: tuple[str, ...] = ()
    operators: tuple[str, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)
    candidate_id: str = ""
    generation: int = 0
    attempts: int = 0
    validation_status: str = "pending"
    economic_status: str = "pending"
    falsification_plan: tuple[str, ...] = ()
    provenance: str = "deterministic_unvalidated"

    def __post_init__(self) -> None:
        if not self.hypothesis.strip() or not self.mechanism.strip():
            raise ValueError("A candidate needs a hypothesis and economic mechanism")
        if not self.formula.strip() and not self.code.strip():
            raise ValueError("A candidate needs a formula or code")
        if self.direction not in (0, 1):
            raise ValueError("PandaAI direction must be 0 (lower values) or 1 (higher values)")
        for name in ("data_requirements", "parents", "fields", "operators", "falsification_plan"):
            items = getattr(self, name)
            if isinstance(items, str) or not isinstance(items, (list, tuple)):
                raise ValueError(f"{name} must be a list or tuple")
            setattr(self, name, tuple(items))
        inferred_fields, inferred_operators = expression_features(self.formula)
        if not self.fields:
            self.fields = inferred_fields
        if not self.operators:
            self.operators = inferred_operators
        if not self.candidate_id:
            identity = (canonical_signature(self.formula or self.code), self.direction,
                        self.mechanism, self.source)
            self.candidate_id = "F-" + hashlib.sha256(repr(identity).encode()).hexdigest()[:14]

    @property
    def family_id(self) -> str:
        return canonical_signature(self.formula or self.code, ignore_numeric_parameters=True)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["family_id"] = self.family_id
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Candidate":
        allowed = cls.__dataclass_fields__
        data = {key: item for key, item in value.items() if key in allowed}
        if "hypothesis" not in data and "name" in value:
            data["hypothesis"] = str(value["name"])
        if "mechanism" not in data and "family" in value:
            data["mechanism"] = str(value["family"])
        return cls(**data)


@dataclass
class FailureEvidence:
    stage: str = "exploration"
    failure_type: str = "unknown"
    observations: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()
    correlated_with: tuple[str, ...] = ()
    data_complete: bool | None = None
    falsified: bool = False
    reproducible_signal: bool = False
    economic_validation: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureEvidence":
        return cls(**{k: v for k, v in value.items() if k in cls.__dataclass_fields__})


@dataclass
class EvolutionDecision:
    candidate_id: str
    action: str
    failure_type: str
    explanation: str
    required_tests: list[str]
    family_attempts: int
    economic_status: str = "pending"
    evidence_artifacts: tuple[str, ...] = ()
    reflection_source: str = "deterministic"
    family_source_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PlanningBackend(Protocol):
    def complete_json(self, stage: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
        """Return {'candidates': [...]} or a structured reflection decision."""


_PLANNING_INSTRUCTION = """You are a factor research planner. Return only a JSON object.
Generate falsifiable economic hypotheses, not window grids. Diversify mechanisms,
fields, operator families and data sources. Each candidate includes hypothesis,
mechanism, data_requirements, source, formula and/or Python code, direction (0/1),
parents, fields, operators, parameters and falsification_plan. Python definitions
may contain arbitrary multistep/stateful calculations; do not restrict research to
a catalogue of arithmetic templates. Declare missing data sources explicitly.
Never use future observations or infer factor-value correlation from IC/return
summary metrics. Never claim economic acceptance; generated candidates are pending.
When mutating/crossing trajectories explain which causal hypothesis changed, why
the parents complement one another and which result would disprove the child.
Respect abandoned families, failed mechanisms and parameter-grid attempt limits.
Only supplied, permitted research evidence may be used; the application owns any
sealed dates, holdout selection, credentials and external computation approval.
For reflection return action (abandon/repair/orthogonalize/new_source/escalate),
failure_type, explanation and required_tests. Do not override economic validation.
"""


class OpenAICompatibleBackend:
    """Adapt an explicitly supplied OpenAI-compatible completion callable.

    Example: ``OpenAICompatibleBackend(client.chat.completions.create, model)``.
    Supplying this adapter opts into calls; callers enforce model/API budgets,
    holdout redaction and service permissions. The engine never constructs a client.
    """
    def __init__(self, caller: Callable[..., Any], model: str) -> None:
        if not callable(caller) or not model:
            raise ValueError("An explicit completion caller and model are required")
        self.caller, self.model = caller, model

    def complete_json(self, stage: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
        response = self.caller(
            model=self.model, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": _PLANNING_INSTRUCTION},
                      {"role": "user", "content": json.dumps(
                          {"stage": stage, "context": context}, ensure_ascii=False)}])
        if isinstance(response, str):
            payload = json.loads(response)
        elif isinstance(response, Mapping) and "choices" not in response:
            payload = response
        elif isinstance(response, Mapping):
            payload = json.loads(response["choices"][0]["message"]["content"])
        else:
            payload = json.loads(response.choices[0].message.content)
        if not isinstance(payload, Mapping):
            raise ValueError("Planner must return a JSON object")
        return payload


def _platform_code(formula: str, fields: Sequence[str]) -> str:
    bindings = "\n".join(f"        {name} = factors['{name.lower()}']" for name in fields)
    return ("class ResearchFactor(Factor):\n"
            "    def calculate(self, factors):\n" + bindings + "\n"
            f"        value = {formula}\n"
            "        return value.rename('value')\n")


def deterministic_candidates() -> list[Candidate]:
    """Independent daily OHLCV mechanisms using documented PandaAI field names.

    Formulas and generated Python mirror the documented platform contract. They
    need a real platform syntax/data probe before either has verified status.
    """
    specs = [
        ("Temporary selling pressure reverses after liquidity recovers", "short_reversal",
         "-(CLOSE/DELAY(CLOSE,5)-1)", ("CLOSE",)),
        ("Persistent trends dominate noisy paths of the same cumulative return", "trend_efficiency",
         "(CLOSE/DELAY(CLOSE,20)-1)/(SUM(ABS(CLOSE/DELAY(CLOSE,1)-1),20)+0.00000001)", ("CLOSE",)),
        ("Intraday recovery after adverse overnight repricing reflects demand resilience", "gap_recovery",
         "MA((CLOSE-OPEN)/(HIGH-LOW+0.00000001),10)-MA(OPEN/DELAY(CLOSE,1)-1,10)",
         ("CLOSE", "OPEN", "HIGH", "LOW")),
        ("High price impact per traded currency captures constrained liquidity", "liquidity_impact",
         "MA(ABS(CLOSE/DELAY(CLOSE,1)-1)/(AMOUNT+0.00000001),20)", ("CLOSE", "AMOUNT")),
        ("Compression of realized daily range anticipates a distinct volatility regime", "range_compression",
         "-TS_ZSCORE((HIGH-LOW)/(CLOSE+0.00000001),20)", ("HIGH", "LOW", "CLOSE")),
        ("Price increases accompanied by shrinking activity suggest weak participation", "participation_divergence",
         "-CORR(CLOSE/DELAY(CLOSE,1)-1,VOLUME/(MA(VOLUME,20)+0.00000001),20)", ("CLOSE", "VOLUME")),
        ("Lower downside share at equal total variation signals asymmetric resilience", "downside_asymmetry",
         "-MA(IF(CLOSE<DELAY(CLOSE,1),ABS(CLOSE/DELAY(CLOSE,1)-1),0),20)/(MA(ABS(CLOSE/DELAY(CLOSE,1)-1),20)+0.00000001)",
         ("CLOSE",)),
        ("Heavy-volume closes near the session high indicate sustained demand", "closing_pressure",
         "MA((2*CLOSE-HIGH-LOW)/(HIGH-LOW+0.00000001)*VOLUME/(MA(VOLUME,20)+0.00000001),10)",
         ("CLOSE", "HIGH", "LOW", "VOLUME")),
        ("A volatility surge relative to a long baseline marks unstable positioning", "volatility_term_structure",
         "-STDDEV(CLOSE/DELAY(CLOSE,1)-1,5)/(STDDEV(CLOSE/DELAY(CLOSE,1)-1,60)+0.00000001)", ("CLOSE",)),
        ("Repeated new highs reveal gradual information diffusion", "breakout_persistence",
         "COUNT(CLOSE>DELAY(TS_MAX(HIGH,20),1),10)/10", ("CLOSE", "HIGH")),
    ]
    candidates: list[Candidate] = []
    for hypothesis, mechanism, formula, fields in specs:
        candidates.append(Candidate(
            hypothesis=hypothesis, mechanism=mechanism, formula=formula,
            code=_platform_code(formula, fields), fields=fields,
            data_requirements=("point_in_time_adjusted_daily_ohlcv", "tradable_universe_at_signal_date"),
            falsification_plan=("Verify field/operator semantics on a short platform probe",
                                "Compare daily factor-value ranks against the existing pool",
                                "Test temporal stability and turnover-adjusted held-side returns",
                                "Validate on caller-reserved data before economic acceptance")))
    return candidates


class ResearchEngine:
    def __init__(self, memory_path: str | Path | None = None,
                 backend: PlanningBackend | None = None,
                 max_family_attempts: int = 3, state_path: str | Path | None = None) -> None:
        if max_family_attempts < 1:
            raise ValueError("max_family_attempts must be positive")
        self.backend = backend
        self.max_family_attempts = max_family_attempts
        self.memory: dict[str, Any] = {}
        if memory_path is not None:
            self.memory = json.loads(Path(memory_path).read_text(encoding="utf-8-sig"))
            if not isinstance(self.memory, dict):
                raise ValueError("Research memory must be a JSON object")
        self.attempts: Counter[str] = Counter()
        self.source_failures: Counter[str] = Counter()
        self.state_migrations: list[dict[str, Any]] = []
        self.abandoned_families: set[str] = set()
        self.decisions: list[EvolutionDecision] = []
        self.candidates: dict[str, Candidate] = {}
        self.memory_blocked_mechanisms: set[str] = set()
        self.historical_blocks: dict[str, set[str]] = {}
        self.reopening_events: list[dict[str, Any]] = []
        self.reopened_families: dict[str, dict[str, Any]] = {}
        self._import_memory_constraints()
        if state_path is not None and Path(state_path).exists():
            self.load_state(state_path)

    def _context(self, parents: Sequence[Candidate], count: int) -> dict[str, Any]:
        return {"count": count, "parents": [p.to_dict() for p in parents],
                "research_memory": self.memory,
                "family_attempts": dict(self.attempts),
                "family_attempts_scope": "non_source_investigations",
                "family_source_failures": dict(self.source_failures),
                "state_migrations": self.state_migrations,
                "abandoned_families": sorted(self.abandoned_families),
                "reopening_events": self.reopening_events,
                "max_family_attempts": self.max_family_attempts,
                "economic_status": "pending"}

    def _import_memory_constraints(self) -> None:
        rules = self.memory.get("no_repeat_rules", [])
        if not isinstance(rules, list):
            return
        blocked_families = {str(r.get("family")) for r in rules if isinstance(r, Mapping)}
        self.memory_blocked_mechanisms.update(blocked_families)
        for item in self.memory.get("hypotheses", []):
            if not isinstance(item, Mapping) or str(item.get("family")) not in blocked_families:
                continue
            if item.get("mechanism"):
                self.memory_blocked_mechanisms.add(str(item["mechanism"]))
            records = [item] + list(item.get("evidence", []))
            for record in records:
                if not isinstance(record, Mapping):
                    continue
                expression = record.get("formula") or record.get("code")
                if expression:
                    family_id = canonical_signature(str(expression), True)
                    self.abandoned_families.add(family_id)
                    self.historical_blocks.setdefault(family_id, set()).update(
                        str(item[k]) for k in ("family", "mechanism") if item.get(k))

    def _memory_seeds(self) -> list[Candidate]:
        records = self.memory.get("seeds", self.memory.get("directions", []))
        if not isinstance(records, list):
            return []
        records = list(records)
        wanted = {str(item.get("family")) for item in self.memory.get("starting_directions", [])
                  if isinstance(item, Mapping)}
        for item in self.memory.get("hypotheses", []):
            if not isinstance(item, Mapping) or str(item.get("family")) not in wanted:
                continue
            if item.get("formula") or item.get("code"):
                records.append({**item,
                                "source": item.get("data_source", "historical_requires_axis_revalidation"),
                                "hypothesis": item.get("hypothesis", item.get("mechanism", item.get("family")))})
            for evidence in item.get("evidence", []):
                if isinstance(evidence, Mapping) and (evidence.get("formula") or evidence.get("code")):
                    records.append({**item, **evidence,
                                    "source": evidence.get("data_source", "historical_requires_axis_revalidation"),
                                    "hypothesis": item.get("hypothesis", item.get("mechanism", item.get("family")))})
        result: list[Candidate] = []
        for record in records:
            if not isinstance(record, Mapping) or not (record.get("formula") or record.get("code")):
                continue
            try:
                seed = Candidate.from_dict(record)
            except (TypeError, ValueError):
                continue
            seed.validation_status = seed.economic_status = "pending"
            seed.provenance = "historical_memory_requires_revalidation"
            local_fields = {"CLOSE", "OPEN", "HIGH", "LOW", "VOLUME", "AMOUNT"}
            missing = sorted({name.upper() for name in seed.fields} - local_fields)
            seed.data_requirements = tuple(dict.fromkeys(seed.data_requirements + (
                "revalidate_legacy_formula_and_operator_semantics",
                "point_in_time_adjusted_daily_ohlcv",
            ) + tuple(f"requires_source:{name}" for name in missing)))
            seed.trajectory.append({"event": "memory_seed", "evidence": "compact research memory",
                                    "historical_hypothesis_id": record.get("hypothesis_id"),
                                    "historical_family": record.get("family"),
                                    "evidence_refs": record.get("evidence_refs", [])})
            result.append(seed)
        return result

    def _admissible(self, candidate: Candidate) -> bool:
        if candidate.family_id in self.abandoned_families:
            return False
        if self.attempts[candidate.family_id] >= self.max_family_attempts:
            return False
        blocked = set(self.memory.get("abandoned_mechanisms", [])) | self.memory_blocked_mechanisms
        return candidate.mechanism not in blocked or candidate.family_id in self.reopened_families

    @staticmethod
    def _verify_reopening_evidence(evidence: Mapping[str, Any], falsifier: str) -> list[dict[str, str]]:
        if not isinstance(falsifier, str) or not falsifier.strip():
            raise ValueError("Reopening requires an explicit falsifier")
        kind = evidence.get("change_type")
        if kind not in {"verified_source_repair", "material_information_change"}:
            raise ValueError("Only verified source repair or material information change can reopen a family")
        if not isinstance(evidence.get("description"), str) or not evidence["description"].strip():
            raise ValueError("Describe the new evidence; renaming a candidate is not a change")
        required = "verification_summary" if kind == "verified_source_repair" else "changed_information"
        if not isinstance(evidence.get(required), str) or not evidence[required].strip():
            raise ValueError(f"Reopening requires {required}")
        artifacts = evidence.get("artifacts", [])
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError("Reopening needs verifiable artifact paths and SHA256 digests")
        verified: list[dict[str, str]] = []
        for artifact in artifacts:
            if not isinstance(artifact, Mapping) or not artifact.get("path"):
                raise ValueError("Every reopening artifact needs a path and SHA256")
            expected = str(artifact.get("sha256", "")).lower()
            if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
                raise ValueError("Every reopening artifact needs a SHA256 digest")
            path = Path(artifact["path"]).resolve()
            if not path.is_file():
                raise ValueError(f"Reopening evidence artifact is missing: {path}")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError(f"Reopening evidence SHA256 mismatch: {path}")
            verified.append({"path": str(path), "sha256": actual})
        return verified

    def reopen_family(self, candidate: Candidate, evidence: Mapping[str, Any], falsifier: str) -> dict[str, Any]:
        """Start a new evidence epoch for one previously blocked formula family.

        Artifact hashes verify the supplied evidence's identity and availability;
        the source verification/change explanation remains attributable to its
        producer. This is exploratory authorization, not economic acceptance.
        Mechanism-name changes alone never invoke or satisfy this operation.
        """
        verified = self._verify_reopening_evidence(evidence, falsifier)
        if self._admissible(candidate):
            raise ValueError("The candidate family is not currently blocked")
        family_id = candidate.family_id
        hashes = sorted(item["sha256"] for item in verified)
        if any(event["family_id"] == family_id and sorted(item["sha256"] for item in event["artifacts"]) == hashes
               for event in self.reopening_events):
            raise ValueError("The same evidence cannot repeatedly reopen an exhausted family")
        event = {"event": "family_reopening", "family_id": family_id,
                 "candidate_id": candidate.candidate_id,
                 "mechanisms": sorted(self.historical_blocks.get(family_id, set()) | {candidate.mechanism}),
                 "change_type": evidence["change_type"], "description": evidence["description"],
                 "verification_summary": evidence.get("verification_summary"),
                 "changed_information": evidence.get("changed_information"),
                 "artifacts": verified, "falsifier": falsifier.strip(),
                 "previous_family_attempts": self.attempts[family_id], "economic_status": "pending"}
        event["previous_source_failures"] = self.source_failures[family_id]
        self.abandoned_families.discard(family_id)
        self.attempts[family_id] = 0
        self.source_failures[family_id] = 0
        self.reopening_events.append(event)
        self.reopened_families[family_id] = event
        candidate.trajectory.append(dict(event))
        candidate.validation_status = candidate.economic_status = "pending"
        self.candidates[candidate.candidate_id] = candidate
        return event

    def _register(self, candidates: Iterable[Candidate], parents: Sequence[Candidate], event: str) -> list[Candidate]:
        result: list[Candidate] = []
        for parent in parents:
            self.candidates[parent.candidate_id] = parent
        for candidate in candidates:
            candidate.validation_status = candidate.economic_status = "pending"
            if parents:
                allowed = {p.candidate_id for p in parents}
                supplied = set(candidate.parents)
                if supplied - allowed:
                    raise ValueError("Planner references a parent outside the supplied trajectories")
                candidate.parents = candidate.parents or tuple(p.candidate_id for p in parents)
                candidate.generation = max(p.generation for p in parents) + 1
            candidate.trajectory = list(candidate.trajectory) + [{
                "event": event, "parents": list(candidate.parents),
                "family_attempts": self.attempts[candidate.family_id],
                "hypothesis": candidate.hypothesis, "validation": "pending"}]
            self.candidates[candidate.candidate_id] = candidate
            result.append(candidate)
        return result

    def propose(self, count: int = 10, parents: Sequence[Candidate] = ()) -> list[Candidate]:
        if count < 0:
            raise ValueError("count must be nonnegative")
        if count == 0:
            return []
        options: list[Candidate] = []
        if self.backend is not None:
            payload = self.backend.complete_json("plan" if not parents else "mutate_cross_trajectories",
                                                 self._context(parents, count))
            records = payload.get("candidates", [])
            if not isinstance(records, list):
                raise ValueError("Planner candidates must be a list")
            for record in records:
                candidate = Candidate.from_dict(record)
                candidate.provenance = "llm_unvalidated"
                options.append(candidate)
        else:
            options = self._memory_seeds() + deterministic_candidates()
        options = [candidate for candidate in options if self._admissible(candidate)]
        # Stored candidates and supplied parents rule out duplicate/window-only paths.
        prior = list(self.candidates.values()) + list(parents)
        selected = select_diverse(options, count, prior_candidates=prior)
        return self._register(selected, parents, "llm_plan" if self.backend else "deterministic_plan")

    @staticmethod
    def _is_source_failure(evidence: FailureEvidence) -> bool:
        failure = evidence.failure_type.lower()
        hard_falsification = evidence.falsified or failure in {"lookahead", "leakage", "economic_falsification"}
        return not hard_falsification and (evidence.data_complete is False or
                                          failure in {"missing_data", "source_gap", "pit_unavailable"})

    def reflect(self, candidate: Candidate, evidence: FailureEvidence | Mapping[str, Any]) -> EvolutionDecision:
        if isinstance(evidence, Mapping):
            evidence = FailureEvidence.from_dict(evidence)
        self.candidates[candidate.candidate_id] = candidate
        source_failure = self._is_source_failure(evidence)
        if source_failure:
            self.source_failures[candidate.family_id] += 1
        else:
            self.attempts[candidate.family_id] += 1
        # Candidate attempts retain the total reflection-event count for provenance.
        candidate.attempts += 1
        attempts = self.attempts[candidate.family_id]
        failure = evidence.failure_type.lower()
        if evidence.falsified or failure in {"lookahead", "leakage", "economic_falsification"}:
            action, why = "abandon", "The causal hypothesis was falsified or uses future information"
        elif source_failure:
            action, why = "new_source", "Required point-in-time observations are missing; repair the evidence source"
        elif attempts >= self.max_family_attempts and not evidence.reproducible_signal:
            action, why = "abandon", "The family exhausted its investigation limit; stop numeric-grid retries"
        elif failure == "economic_failure":
            action, why = "abandon", "Measured after-cost held-side economics failed; retire the unchanged definition until new evidence"
        elif evidence.correlated_with or failure in {"redundancy", "high_correlation", "duplicate"}:
            action, why = "orthogonalize", "Observed factor-value ranks duplicate an existing research axis"
        elif failure in {"syntax", "runtime", "alignment", "coverage", "constant", "operator_semantics"}:
            action, why = "repair", "The implementation or observation contract needs a targeted repair"
        elif evidence.reproducible_signal:
            action, why = "escalate", "Replicated exploratory evidence warrants broader falsification and cost tests"
        else:
            action, why = "repair", "Evidence is inconclusive; specify a discriminating test before another run"
        tests = {
            "abandon": ["Record the falsification and prevent automatic family re-entry"],
            "new_source": ["Verify source availability, point-in-time timing, adjustment and universe coverage"],
            "orthogonalize": ["Change the economic mechanism or residualize against the named factor-value panel",
                              "Recompute daily cross-sectional rank correlation with sufficient shared coverage"],
            "repair": ["Reproduce the failure on a small panel and verify date/security alignment",
                       "Vary mechanism or source after repair; avoid another window grid"],
            "escalate": ["Test held-side returns after trading costs and turnover",
                         "Test regime stability, multiple-testing controls and caller-reserved validation data"],
        }[action]
        reflection_source = "deterministic"
        if self.backend is not None:
            payload = self.backend.complete_json("reflect", {
                **self._context([candidate], 0), "evidence": evidence.to_dict(),
                "deterministic_action": action, "required_tests": tests})
            proposed = payload.get("action", action)
            if proposed not in {"abandon", "repair", "orthogonalize", "new_source", "escalate"}:
                raise ValueError("Planner returned an unknown reflection action")
            # Data gaps, falsification, redundancy and loop caps are factual guards.
            if action == "repair" and evidence.reproducible_signal is False:
                if proposed != "escalate":
                    action = proposed
            if action == proposed:
                why = str(payload.get("explanation", why))
            additional = payload.get("required_tests", [])
            if not isinstance(additional, list) or not all(isinstance(t, str) for t in additional):
                raise ValueError("required_tests must be a list of strings")
            tests = list(dict.fromkeys(tests + additional))
            reflection_source = "llm_with_evidence_guards"
        # Economic validation belongs to the caller's independent acceptance gate.
        economics = str(evidence.economic_validation.get("status", "pending"))
        if economics not in {"pending", "accepted", "rejected"}:
            economics = "pending"
        decision = EvolutionDecision(candidate.candidate_id, action, failure, why, tests,
                                     attempts, economics, evidence.artifacts, reflection_source,
                                     self.source_failures[candidate.family_id])
        candidate.trajectory.append({"event": "reflection", "decision": decision.to_dict(),
                                     "evidence": evidence.to_dict()})
        if action == "abandon":
            self.abandoned_families.add(candidate.family_id)
        self.decisions.append(decision)
        return decision

    def evolve(self, parents: Sequence[Candidate],
               evidence: Mapping[str, FailureEvidence | Mapping[str, Any]],
               count: int = 10) -> list[Candidate]:
        for parent in parents:
            if parent.candidate_id in evidence:
                self.reflect(parent, evidence[parent.candidate_id])
        if self.backend is not None:
            return self.propose(count, parents=parents)
        options: list[Candidate] = []
        eligible = [p for p in parents if self._admissible(p) and p.formula]
        # Cross *mechanisms*, with no parameter tuning or claim of residualization.
        for i, left in enumerate(eligible):
            for right in eligible[i + 1:]:
                if left.mechanism == right.mechanism:
                    continue
                fields = tuple(sorted(set(left.fields) | set(right.fields)))
                formula = f"RANK({left.formula})*RANK({right.formula})"
                options.append(Candidate(
                    hypothesis=f"The interaction of {left.mechanism} and {right.mechanism} separates joint regimes",
                    mechanism=f"interaction:{'+'.join(sorted((left.mechanism, right.mechanism)))}",
                    data_requirements=tuple(sorted(set(left.data_requirements) | set(right.data_requirements))),
                    source=left.source if left.source == right.source else f"{left.source}+{right.source}",
                    formula=formula, code=_platform_code(formula, fields), fields=fields,
                    parents=(left.candidate_id, right.candidate_id),
                    falsification_plan=("Compare with each parent on actual factor-value ranks",
                                        "Test whether the interaction adds information beyond both parents",
                                        "Reject if costs or independent validation erase the gain"),
                    provenance="deterministic_crossover_unvalidated"))
        options += deterministic_candidates()
        options = [c for c in options if self._admissible(c)]
        prior = list(self.candidates.values()) + list(parents)
        selected = select_diverse(options, count, prior_candidates=prior)
        # Fresh mechanisms are fresh roots; only explicit crosses inherit parents.
        result: list[Candidate] = []
        for candidate in selected:
            selected_parents = [p for p in parents if p.candidate_id in candidate.parents]
            result += self._register([candidate], selected_parents, "cross_mechanism" if selected_parents else "new_mechanism")
        return result

    def save_state(self, path: str | Path) -> None:
        state = {"schema_version": 2, "attempts": dict(self.attempts),
                 "attempts_scope": "non_source_investigations",
                 "source_failures": dict(self.source_failures),
                 "state_migrations": self.state_migrations,
                 "abandoned_families": sorted(self.abandoned_families),
                 "candidates": [c.to_dict() for c in self.candidates.values()],
                 "reopening_events": self.reopening_events,
                 "decisions": [d.to_dict() for d in self.decisions]}
        Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_state(self, path: str | Path) -> None:
        state = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        version = state.get("schema_version")
        if version not in {1, 2}:
            raise ValueError("Unsupported evolution state version")
        self.attempts = Counter(state.get("attempts", {}))
        self.source_failures = Counter(state.get("source_failures", {})) if version == 2 else Counter()
        self.state_migrations = list(state.get("state_migrations", []))
        self.reopening_events = list(state.get("reopening_events", []))
        self.reopened_families = {}
        for event in self.reopening_events:
            self._verify_reopening_evidence(event, event.get("falsifier", ""))
            self.reopened_families[event["family_id"]] = event
            self.abandoned_families.discard(event["family_id"])
        self.abandoned_families.update(state.get("abandoned_families", []))
        candidates = [Candidate.from_dict(item) for item in state.get("candidates", [])]
        self.candidates = {candidate.candidate_id: candidate for candidate in candidates}
        self.decisions = [EvolutionDecision(**item) for item in state.get("decisions", [])]
        if version == 1:
            self._migrate_legacy_source_budget(state)

    def _migrate_legacy_source_budget(self, state: Mapping[str, Any]) -> None:
        """Repair only old source-only counters with complete attributable history.

        Older states can have more global decisions than retained trajectories.
        Mixed, incomplete, historical or reopened histories keep their blocks;
        verified evidence reopening remains available for those families.
        """
        records = list(state.get("decisions", []))
        ownership_complete = all(row.get("candidate_id") in self.candidates for row in records)
        cap_reason = "The family exhausted its investigation limit; stop numeric-grid retries"
        key = lambda row: json.dumps(row, sort_keys=True, ensure_ascii=False)
        for family, previous in list(self.attempts.items()):
            members = [c for c in self.candidates.values() if c.family_id == family]
            blocked_mechanisms = set(self.memory.get("abandoned_mechanisms", [])) | self.memory_blocked_mechanisms
            protected = (family in self.historical_blocks or family in self.reopened_families or
                         any(c.mechanism in blocked_mechanisms for c in members))
            ids = {c.candidate_id for c in members}
            decisions = [row for row in records if row.get("candidate_id") in ids]
            proof: dict[str, Mapping[str, Any]] = {}
            conflicting = False
            for candidate in self.candidates.values():
                for event in candidate.trajectory:
                    if event.get("event") != "reflection" or not isinstance(event.get("decision"), Mapping):
                        continue
                    identity = key(event["decision"])
                    evidence = event.get("evidence")
                    if not isinstance(evidence, Mapping):
                        continue
                    if identity in proof and proof[identity] != evidence:
                        conflicting = True
                    proof[identity] = evidence
            complete = (ownership_complete and not protected and previous > 0 and
                        len(decisions) == previous and not conflicting and
                        [d.get("family_attempts") for d in decisions] == list(range(1, previous + 1)) and
                        len({key(d) for d in decisions}) == previous and
                        all(key(d) in proof for d in decisions))
            pure_source = complete and all(
                self._is_source_failure(FailureEvidence.from_dict(proof[key(d)])) and
                d.get("economic_status", "pending") == "pending" and
                proof[key(d)].get("economic_validation", {}).get("status", "pending") == "pending" and
                d.get("failure_type") == proof[key(d)].get("failure_type", "unknown").lower() and
                d.get("failure_type") not in {"economic_failure", "syntax", "runtime", "alignment", "constant"} and
                (d.get("action") == "new_source" or
                 d.get("action") == "abandon" and d.get("explanation") == cap_reason)
                for d in decisions)
            if pure_source:
                was_blocked = family in self.abandoned_families
                self.attempts[family] = 0
                self.source_failures[family] = previous
                self.abandoned_families.discard(family)
                self.state_migrations.append({"event": "legacy_source_budget_repair", "family_id": family,
                    "previous_attempts": previous, "source_failures": previous,
                    "incorrect_family_block_removed": was_blocked, "economic_status": "pending"})
            elif previous > 0:
                self.state_migrations.append({"event": "legacy_budget_preserved", "family_id": family,
                    "reason": "history incomplete, mixed, historically blocked or in a reopened evidence epoch",
                    "automatic_unblocking": False})
