"""Local dispatch adapter backed by PostgreSQL polling."""

from galaxy_frog.application.ingestion.dispatch import DispatchMessage, DispatchReceipt


class LocalJobDispatcher:
    """Acknowledge jobs already visible to the local PostgreSQL polling worker."""

    async def dispatch(self, message: DispatchMessage) -> DispatchReceipt:
        del message
        return DispatchReceipt(dispatcher="postgres_polling")
