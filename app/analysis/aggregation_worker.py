"""组合影响快照 Outbox worker。"""

from datetime import datetime, timezone

from app.analysis.aggregation import ImpactAggregationService
from app.platform.repository import RepositoryProvider


class ImpactAggregationWorker:
    def __init__(self, repository: RepositoryProvider) -> None:
        self.repository = repository
        self.service = ImpactAggregationService(repository)

    def run_once(self, batch_size: int = 20) -> int:
        now = datetime.now(timezone.utc)
        messages = self.repository.list_pending_outbox_by_event_type(
            "target_impact.recompute.requested.v1", batch_size, now=now
        )
        processed = 0
        for message in messages:
            event_id = message.payload.get("event_id")
            if event_id:
                analysis = self.repository.get_latest_impact_analysis_for_event(event_id)
                if analysis and analysis.status == "approved":
                    contributions = self.service.project_analysis(analysis)
                    for target_id in {item.target_id for item in contributions}:
                        self.service.recompute_target(target_id, as_of=now)
            self.repository.mark_outbox_published(message.id, now)
            processed += 1
        return processed
