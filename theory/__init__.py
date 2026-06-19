"""Unified game-theory tooling — CFR+, neural value net, and stack-depth charts."""

from theory.charts import (
    CHART_DEPTHS,
    CHART_POSITIONS,
    build_coach_theory_block,
    get_chart,
    list_chart_depths,
    validate_chart_vs_cfr,
)
from theory.cfr_solver import (
    SOLVABLE_GAMES,
    pot_odds_with_ante,
    run_cfr_for_game,
    run_cfr_plus,
    solve_kuhn,
    solve_leduc,
    solve_push_fold,
    solve_tournament_push_fold,
)
from theory.value_net import (
    TORCH_AVAILABLE,
    encode_spot,
    predict_value,
    train_value_net,
    theory_context_block,
)

__all__ = [
    "CHART_DEPTHS",
    "CHART_POSITIONS",
    "SOLVABLE_GAMES",
    "TORCH_AVAILABLE",
    "build_coach_theory_block",
    "encode_spot",
    "get_chart",
    "list_chart_depths",
    "pot_odds_with_ante",
    "predict_value",
    "run_cfr_for_game",
    "run_cfr_plus",
    "solve_kuhn",
    "solve_leduc",
    "solve_push_fold",
    "solve_tournament_push_fold",
    "train_value_net",
    "theory_context_block",
    "validate_chart_vs_cfr",
]
