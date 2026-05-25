import json
import sys
import subprocess
from typing import Dict, Any, List


# -----------------------------
# LOADERS
# -----------------------------

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)


# -----------------------------
# HELPERS
# -----------------------------

def get_nested_value(obj: dict, path: str):
    """
    Supports dotted paths like: tags.env
    """
    if not obj:
        return None

    keys = path.split(".")
    current = obj

    for k in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(k)

    return current


# -----------------------------
# ARCHITECTURE / REPO LAYER (NEW)
# -----------------------------

def get_changed_files() -> List[str]:
    """
    Returns files changed in PR vs main.
    """
    try:
        result = subprocess.check_output(
            ["git", "diff", "--name-only", "origin/main...HEAD"]
        )
        return result.decode().splitlines()
    except Exception:
        return []


def classify_layer(file_path: str) -> str:
    """
    Maps file to abstraction layer.
    """
    if file_path.endswith(".tf"):
        return "terraform"
    if file_path.endswith(".yaml") or file_path.endswith(".yml"):
        return "config"
    if file_path.startswith(".github/"):
        return "ci"
    if file_path.endswith(".json"):
        return "json"
    return "other"


def check_architecture_layers(scenario: Dict[str, Any]) -> List[str]:
    """
    Ensures AI used correct abstraction layer.
    """
    violations = []

    intent = scenario.get("intent", {})
    allowed_layers = intent.get("allowed_modification_layers", [])
    forbidden_layers = intent.get("forbidden_modification_layers", [])

    changed_files = get_changed_files()

    for file in changed_files:
        layer = classify_layer(file)

        # check forbidden layers
        for rule in forbidden_layers:
            if rule in file or rule == layer:
                violations.append(f"forbidden_layer_change:{file}")

        # check allowed-only constraint (if defined)
        if allowed_layers:
            allowed = any(rule in file or rule == layer for rule in allowed_layers)
            if not allowed:
                violations.append(f"unexpected_layer_change:{file}")

    return violations


# -----------------------------
# SCENARIO CHECKS
# -----------------------------

def check_required_changes(plan: Dict[str, Any], scenario: Dict[str, Any]) -> List[str]:
    violations = []

    required = scenario.get("required_changes", [])

    for req in required:
        req_type = req.get("type")
        req_resource_type = req.get("resource_type")

        matched = False

        for rc in plan.get("resource_changes", []):
            actions = rc.get("change", {}).get("actions", [])
            r_type = rc.get("type")

            action_ok = req_type in actions if req_type else True
            type_ok = (req_resource_type == r_type) if req_resource_type else True

            if action_ok and type_ok:
                matched = True
                break

        if not matched:
            violations.append(
                f"missing_required_change:{req_type}:{req_resource_type}"
            )

    return violations


def check_forbidden_changes(plan: Dict[str, Any], scenario: Dict[str, Any]) -> List[str]:
    violations = []

    forbidden = scenario.get("forbidden_changes", [])

    for rc in plan.get("resource_changes", []):
        actions = rc.get("change", {}).get("actions", [])
        r_type = rc.get("type")

        for f in forbidden:
            f_type = f.get("type")
            f_resource = f.get("resource_type")

            if f_type and f_type in actions:
                violations.append(f"forbidden_action:{f_type}")

            if f_resource and f_resource == r_type:
                violations.append(f"forbidden_resource_type:{f_resource}")

    return violations


def check_scope(plan: Dict[str, Any], scenario: Dict[str, Any]) -> List[str]:
    violations = []

    allowed_scope = scenario.get("allowed_scope", None)

    if not allowed_scope:
        return violations

    for rc in plan.get("resource_changes", []):
        address = rc.get("address", "")

        allowed = any(scope in address for scope in allowed_scope)

        if not allowed:
            violations.append(f"out_of_scope_change:{address}")

    return violations


def check_intent(plan: Dict[str, Any], scenario: Dict[str, Any]) -> List[str]:
    violations = []

    intent = scenario.get("intent")
    if not intent:
        return violations

    resource_type = intent.get("resource_type")
    changes = intent.get("changes", [])

    for rc in plan.get("resource_changes", []):
        if rc.get("type") != resource_type:
            continue

        change = rc.get("change", {})
        before = change.get("before", {})
        after = change.get("after", {})

        for ch in changes:
            attr = ch.get("attribute")
            expected_from = ch.get("from")
            expected_to = ch.get("to")

            before_val = get_nested_value(before, attr)
            after_val = get_nested_value(after, attr)

            if expected_from is not None and before_val != expected_from:
                violations.append(
                    f"intent_from_mismatch:{attr}:expected={expected_from}:got={before_val}"
                )

            if expected_to is not None and after_val != expected_to:
                violations.append(
                    f"intent_to_mismatch:{attr}:expected={expected_to}:got={after_val}"
                )

    return violations


# -----------------------------
# METRICS
# -----------------------------

def compute_blast_radius(plan: Dict[str, Any]) -> int:
    touched = set()

    for rc in plan.get("resource_changes", []):
        if rc.get("change", {}).get("actions"):
            touched.add(rc["address"])

    return len(touched)


def compute_semantic_score(violations: List[str], blast_radius: int) -> float:
    score = 1.0
    score -= len(violations) * 0.25
    score -= blast_radius * 0.05
    return max(0.0, min(1.0, score))


def compute_resource_summary(plan: Dict[str, Any]) -> Dict[str, int]:
    created = 0
    updated = 0
    deleted = 0

    for rc in plan.get("resource_changes", []):
        actions = rc.get("change", {}).get("actions", [])

        if "create" in actions:
            created += 1
        if "update" in actions:
            updated += 1
        if "delete" in actions:
            deleted += 1

    return {
        "created": created,
        "updated": updated,
        "deleted": deleted
    }


def compute_intent_score(intent: Dict[str, Any], intent_violations: List[str]) -> float:
    if not intent:
        return 1.0

    changes = intent.get("changes", [])
    total = len(changes)

    if total == 0:
        return 1.0

    failed = len(intent_violations)

    return max(0.0, min(1.0, 1.0 - (failed / total)))


def compute_architecture_score(arch_violations: List[str], changed_files: List[str]) -> float:
    if not changed_files:
        return 1.0

    return max(0.0, min(1.0, 1.0 - (len(arch_violations) / len(changed_files))))


# -----------------------------
# MAIN EVALUATION
# -----------------------------

def evaluate(plan_path: str, scenario_path: str) -> Dict[str, Any]:

    plan = load_json(plan_path)
    scenario = load_json(scenario_path)

    violations = []

    # infra layer
    violations += check_required_changes(plan, scenario)
    violations += check_forbidden_changes(plan, scenario)
    violations += check_scope(plan, scenario)

    # intent layer
    intent_violations = check_intent(plan, scenario)
    violations += intent_violations

    # architecture layer (NEW)
    arch_violations = check_architecture_layers(scenario)
    violations += arch_violations

    changed_files = get_changed_files()

    # metrics
    blast_radius = compute_blast_radius(plan)
    semantic_score = compute_semantic_score(violations, blast_radius)
    intent_score = compute_intent_score(scenario.get("intent"), intent_violations)
    architecture_score = compute_architecture_score(arch_violations, changed_files)

    return {
        "semantic_score": semantic_score,
        "intent_score": intent_score,
        "architecture_score": architecture_score,
        "blast_radius": blast_radius,
        "resource_summary": compute_resource_summary(plan),
        "violations": violations,
        "summary": {
            "total_violations": len(violations),
            "status": "PASS" if semantic_score > 0.7 and architecture_score > 0.7 else "FAIL"
        }
    }


# -----------------------------
# CLI
# -----------------------------

if __name__ == "__main__":

    if len(sys.argv) != 3:
        print("Usage: python evaluator.py plan.json scenario.json")
        sys.exit(1)

    result = evaluate(sys.argv[1], sys.argv[2])

    print(json.dumps(result, indent=2))