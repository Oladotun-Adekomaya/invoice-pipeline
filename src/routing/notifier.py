import httpx

from src.config import settings
from src.normalization.schema import Invoice
from src.observability.logger import get_logger
from src.routing.approver import RoutingAction, RoutingOutcome
from src.validation.engine import ValidationReport

logger = get_logger(__name__)


def _post_to_slack(payload: dict) -> None:
    if settings.slack_webhook_url == "placeholder":
        logger.warning("slack webhook not configured, skipping notification")
        return

    try:
        response = httpx.post(
            settings.slack_webhook_url,
            json=payload,
            timeout=10,
        )
        response.raise_for_status()
        logger.info("slack notification sent")
    except Exception as e:
        logger.error("slack notification failed", error=str(e))


def notify(
    invoice: Invoice,
    report: ValidationReport,
    outcome: RoutingOutcome,
) -> None:
    if outcome.action == RoutingAction.AUTO_APPROVED:
        return

    failed_rules = report.failed_rules()
    failed_text = "\n".join(
        f"• *{r.rule_name}*: {r.message}" for r in failed_rules
    ) or "none"

    if outcome.action == RoutingAction.SENT_TO_REVIEW:
        header = f":eyes: Invoice needs review"
        color = "#FFA500"
    else:
        header = f":x: Invoice dead-lettered"
        color = "#FF0000"

    payload = {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": header},
                    },
                    {
                        "type": "section",
                        "fields": [
                            {
                                "type": "mrkdwn",
                                "text": f"*Vendor*\n{invoice.vendor_name}",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Amount*\n${invoice.total_amount}",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Invoice date*\n{invoice.invoice_date}",
                            },
                            {
                                "type": "mrkdwn",
                                "text": f"*Action*\n{outcome.action.value}",
                            },
                        ],
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Failed rules*\n{failed_text}",
                        },
                    },
                ],
            }
        ]
    }

    _post_to_slack(payload)