"""离线评估、M4 质量度量与市场验证契约。"""

from app.evaluation.assessor import Assessor, EvaluationReport, MetricReport, wilson_interval
from app.evaluation.market import (
    DeterministicMarketDataProvider,
    FutureDataLeakError,
    HorizonReturn,
    MarketBar,
    MarketDataProvider,
    MarketEvaluation,
    evaluate_market_returns,
)
from app.evaluation.quality import (
    FrozenSetMetadata,
    MvpEvaluationReport,
    MvpEvaluator,
    QualityMetric,
)

__all__ = [
    "Assessor",
    "DeterministicMarketDataProvider",
    "EvaluationReport",
    "FrozenSetMetadata",
    "FutureDataLeakError",
    "HorizonReturn",
    "MarketBar",
    "MarketDataProvider",
    "MarketEvaluation",
    "MetricReport",
    "MvpEvaluationReport",
    "MvpEvaluator",
    "QualityMetric",
    "evaluate_market_returns",
    "wilson_interval",
]
