"""
benchmark blueprint — /benchmark 페이지.
§3-3: @login_required 적용.
"""

from flask import Blueprint, render_template

from .. import store
from ..auth_utils import login_required


bp = Blueprint("benchmark", __name__)


@bp.route("/benchmark")
@login_required
def benchmark_page():
    methods_order = []
    by_method = {}
    for row in store.get_benchmark_rows():
        method = row["method"]
        target = row["target"]
        if method not in by_method:
            by_method[method] = {"method": method}
            methods_order.append(method)
        by_method[method][target] = {
            "auroc": float(row["auroc"]),
            "fpr": float(row["fpr_at_95tpr"]),
            "n_defect": int(row["n_defect"]),
        }

    benchmark_table = []
    for method_name in methods_order:
        benchmark_table.append(by_method[method_name])

    best_method = None
    best_auroc = -1.0
    for entry in benchmark_table:
        drying = entry.get("drying_defect")
        if drying is None:
            continue
        if drying["auroc"] > best_auroc:
            best_auroc = drying["auroc"]
            best_method = entry["method"]

    robustness_table = []
    for row in store.get_robustness_rows():
        robustness_table.append({
            "perturbation": row["perturbation"],
            "a3_auroc": float(row["a3_auroc"]),
            "a3_fpr_fixed": float(row["a3_fixed_threshold_fpr"]),
            "b2_auroc": float(row["b2_auroc"]),
            "b2_fpr_fixed": float(row["b2_fixed_threshold_fpr"]),
        })

    targets_order = ["surface_crack", "delamination", "pinhole", "drying_defect"]

    return render_template(
        "benchmark.html",
        benchmark=benchmark_table,
        robustness=robustness_table,
        targets_order=targets_order,
        best_method=best_method,
    )
