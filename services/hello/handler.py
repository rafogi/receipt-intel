"""Phase 0 smoke-test Lambda: confirms the deploy path works end to end."""

import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    logger.info("hello invoked", extra={"request_id": context.aws_request_id})
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "hello from receipt-intel",
                "environment": os.environ.get("APP_ENV", "unknown"),
            }
        ),
    }
