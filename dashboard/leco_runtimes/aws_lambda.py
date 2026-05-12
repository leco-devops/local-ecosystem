"""AWS Lambda local runtime adapter — roadmap stub.

Will host an SAM CLI / LocalStack-backed function runner so an app's
``template.yaml`` / ``serverless.yml`` boots locally behind Traefik.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import AdapterNotReady, RuntimeAdapter, RuntimeBuildContext, RuntimeDetection


class AwsLambdaAdapter(RuntimeAdapter):
    type = "aws-lambda"
    label = "AWS Lambda (SAM / LocalStack)"
    roadmap = (
        "Planned image: leco/runtime-aws-lambda. Will use AWS SAM CLI `sam local start-api` "
        "or LocalStack lambda for compose-driven local invocations."
    )

    def detect(self, app_root: Path) -> RuntimeDetection | None:
        return None

    def compose_service(self, spec: dict[str, Any], ctx: RuntimeBuildContext) -> dict[str, Any]:
        raise AdapterNotReady(
            "aws-lambda adapter is on the roadmap. Track infra/runtimes/aws-lambda/ for status."
        )
