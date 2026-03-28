"""Load configuration from AWS Systems Manager Parameter Store by prefix.

When AWS_SSM_PREFIX is set (e.g. /foji/dev/ai-api/), fetches all parameters
under that path and injects them as environment variables so pydantic-settings
picks them up automatically.

Parameter name mapping:
  /foji/dev/ai-api/DATABASE_URL  →  DATABASE_URL
  /foji/dev/ai-api/OPENAI_API_KEY  →  OPENAI_API_KEY
"""

import logging
import os

logger = logging.getLogger(__name__)


def load_ssm_params() -> None:
    prefix = os.environ.get("AWS_SSM_PREFIX", "")
    if not prefix:
        logger.info("[Config] AWS_SSM_PREFIX not set — using local env/.env")
        return

    import boto3

    region = os.environ.get("AWS_REGION", "us-east-1")
    client = boto3.client("ssm", region_name=region)

    loaded = 0
    next_token = None

    while True:
        kwargs = {
            "Path": prefix,
            "Recursive": True,
            "WithDecryption": True,
            "MaxResults": 10,
        }
        if next_token:
            kwargs["NextToken"] = next_token

        response = client.get_parameters_by_path(**kwargs)

        for param in response.get("Parameters", []):
            # /foji/dev/ai-api/DATABASE_URL → DATABASE_URL
            name = param["Name"].removeprefix(prefix).lstrip("/")
            value = param["Value"]
            os.environ[name] = value
            loaded += 1

        next_token = response.get("NextToken")
        if not next_token:
            break

    logger.info("[Config] Loaded %d parameters from AWS SSM: %s", loaded, prefix)
