"""Cycle detection validator for computation chains.

NOT registered in the pipeline -- this function raises exceptions
rather than transforming the spec.
"""

from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
from typing import Any

from amil_utils.preprocessors.relationships import _resolve_comodel


def _validate_no_cycles(spec: dict[str, Any]) -> None:
    """Validate that computation_chains contain no circular dependencies.

    Builds a directed graph where nodes are "model.field" identifiers
    and edges represent "depends on" relationships. Uses graphlib to
    detect cycles.

    Raises ValueError with actionable message naming cycle participants.
    """
    chains = spec.get("computation_chains", [])
    if not chains:
        return

    # ModuleSpec stores chains as ``{chain_id, steps: [...]}``, while older
    # callers still pass one flat ``{field, depends_on}`` entry per field.
    # Normalize both shapes before building the graph.
    entries: list[dict[str, Any]] = []
    for chain in chains:
        steps = chain.get("steps")
        if steps is None:
            entries.append(chain)
            continue
        entries.extend(
            {
                "field": f"{step['model']}.{step['field']}",
                "depends_on": step.get("depends", []),
            }
            for step in steps
        )

    # Build dependency graph: node = "model.field", edges = depends_on
    graph: dict[str, set[str]] = {}
    for chain in entries:
        node = chain["field"]  # e.g., "university.student.gpa"
        model_name = node.rsplit(".", 1)[0]
        deps: set[str] = set()
        for dep in chain.get("depends_on", []):
            if "." in dep:
                # Cross-model: "enrollment_ids.weighted_grade"
                rel_field, target_field = dep.split(".", 1)
                target_model = _resolve_comodel(spec, model_name, rel_field)
                if target_model:
                    deps.add(f"{target_model}.{target_field}")
            else:
                # Local field -- only add if it's also a chain node
                local_node = f"{model_name}.{dep}"
                if any(c["field"] == local_node for c in entries):
                    deps.add(local_node)
        graph[node] = deps

    try:
        ts = TopologicalSorter(graph)
        list(ts.static_order())
    except CycleError as exc:
        cycle_nodes = exc.args[1]
        cycle_str = " -> ".join(str(n) for n in cycle_nodes)
        msg = (
            f"Circular dependency detected in computation_chains: "
            f"{cycle_str}. Break the cycle by removing one dependency."
        )
        raise ValueError(msg) from None
