"""未来行业窗口计算 Worker。"""

from datetime import datetime, timezone

from app.analysis.forward import ForwardImpactService
from app.platform.repository import RepositoryProvider


class ForwardImpactWorker:
    def __init__(self, repository: RepositoryProvider) -> None:
        self.repository = repository
        self.service = ForwardImpactService(repository)

    def run_once(self, batch_size: int = 10) -> int:
        now = datetime.now(timezone.utc)
        messages = self.repository.list_pending_outbox_by_event_type(
            "forward_impact.compute.requested.v1", batch_size, now=now
        )
        for message in messages:
            window_id = message.payload.get("window_id")
            if window_id:
                self.service.recompute(window_id)
            self.repository.mark_outbox_published(message.id, now)
        return len(messages)
